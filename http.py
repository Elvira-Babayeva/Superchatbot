"""Tiny HTTP layer shared by all adapters: JSON GET/POST, timeouts, retries, TTL cache.
Uses only the standard library. `transport` is injectable so tests never touch the network."""
import json, os, re, threading, time, urllib.error, urllib.parse, urllib.request
from repos import ProviderError

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.I)


def redact(text):
    """Replace the value of every secret-looking environment variable (API keys, tokens) with ***, so logs/health never leak them."""
    s = str(text)
    for k, v in os.environ.items():
        if v and len(v) >= 8 and _SECRET_NAME.search(k): s = s.replace(v, "***")
    return s


def _err(msg, kind, status=None):
    e = ProviderError(redact(msg)); e.kind, e.status = kind, status; return e


def _reason(raw):
    """The provider's own explanation from an error body ('invalid x-api-key', 'credit balance is too low', ...)."""
    text = (raw or b"").decode("utf-8", "replace").strip()
    try: j = json.loads(text)
    except ValueError: return text[:150]
    if isinstance(j, dict):
        e = j.get("error")
        if isinstance(e, dict): e = e.get("message") or e.get("type")
        return str(e or j.get("message") or j.get("detail") or "")[:150]
    return ""


def _kind(status): return "auth" if status in (401, 403) else "rate" if status == 429 else "server" if status >= 500 else "client"


def _urllib_transport(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise _err(f"network error: {e}", "network") from e


class TTLCache:
    def __init__(self, ttl): self.ttl, self._d, self._lock = ttl, {}, threading.Lock()
    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl: return hit[1]
    def put(self, key, value):
        with self._lock: self._d[key] = (time.monotonic(), value)


class HttpClient:
    def __init__(self, base_url, headers=None, timeout=8, retries=2, cache_ttl=300, transport=None, default_params=None):
        self.default_params = dict(default_params or {})
        self.base, self.headers, self.timeout, self.retries = base_url.rstrip("/"), dict(headers or {}), timeout, retries
        self.cache, self.transport = TTLCache(cache_ttl), transport or _urllib_transport

    def request(self, method, path, params=None, json_body=None, cache=True):
        url = self.base + path
        params = {**self.default_params, **(params or {})}
        if params: url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        key = (method, url, json.dumps(json_body, sort_keys=True) if json_body else None)
        if cache and method == "GET" and (hit := self.cache.get(key)) is not None: return hit
        headers = {"Accept": "application/json", **self.headers}
        body = None
        if json_body is not None: body, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        last = None
        for attempt in range(self.retries + 1):
            try:
                status, raw = self.transport(method, url, headers, body, self.timeout)
                if status in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(0.4 * 2 ** attempt); continue
                if status >= 400:
                    why = _reason(raw)
                    raise _err(f"HTTP {status} from {self.base}" + (f": {why}" if why else ""), _kind(status), status)
                data = json.loads(raw.decode("utf-8")) if raw else None
                if cache and method == "GET": self.cache.put(key, data)
                return data
            except ProviderError as e:
                last = e
                if attempt < self.retries and (getattr(e, "kind", None) == "network" or str(e).startswith("network")): time.sleep(0.4 * 2 ** attempt); continue
                raise
            except (ValueError, UnicodeDecodeError) as e:
                raise ProviderError(f"invalid JSON from {self.base}") from e
        raise last or ProviderError("request failed")

    def get(self, path, params=None, **kw): return self.request("GET", path, params, **kw)
    def post(self, path, json_body, **kw): return self.request("POST", path, json_body=json_body, cache=False, **kw)
