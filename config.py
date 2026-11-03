"""Chooses which repository implementation each domain uses, from environment variables.

  SUPERCHAT_FLIGHTS   mock (default) | aviasales      needs AVIASALES_TOKEN  [AVIASALES_MARKER, AVIASALES_CURRENCY]
  SUPERCHAT_PRODUCTS  mock (default) | dummyjson | generic
                      generic needs ECOMMERCE_BASE_URL [ECOMMERCE_SEARCH_PATH, ECOMMERCE_QUERY_PARAM, ECOMMERCE_TOKEN,
                      ECOMMERCE_RATE, ECOMMERCE_FIELDS (JSON FieldMap overrides)]
  SUPERCHAT_CINEMA    mock (default) | cinemaplus     needs CINEMAPLUS_BASE_URL [CINEMAPLUS_TOKEN,
                      CINEMAPLUS_ENDPOINTS / CINEMAPLUS_FIELDS (JSON overrides)]
  SUPERCHAT_VISION    color (default) | anthropic     needs ANTHROPIC_API_KEY [ANTHROPIC_VISION_MODEL]
  SUPERCHAT_FALLBACK  error (default) | mock          what to do when a real API fails at request time
A missing key/URL fails fast at start-up with a clear message instead of failing in the middle of a chat.

Real mode switches ON by itself when the key is present and the SUPERCHAT_* variable is not set:
  AVIASALES_TOKEN set -> flights=aviasales;  ANTHROPIC_API_KEY set -> vision=anthropic;  CINEMAPLUS_BASE_URL set -> cinema=cinemaplus.
E-commerce needs a choice (dummyjson = free demo shop, generic = your shop), so set SUPERCHAT_PRODUCTS (or ECOMMERCE_BASE_URL -> generic).
Settings are read from the process environment and from a `.env` file next to this file (the environment wins).
Set SUPERCHAT_NO_DOTENV=1 to ignore the file (the test-suites do)."""
import json, logging, os
from repos import (JsonFlightRepository, JsonProductRepository, JsonShowtimeRepository, ProviderError)

log = logging.getLogger("superchat")


DOTENV_PATH = None


def load_dotenv(path=None):
    """Tiny .env reader: KEY=value, `export KEY=value`, '#' comments (also inline after whitespace), optional quotes, UTF-8 BOM (Windows
    Notepad), CRLF. Never overrides real env vars. Returns the path that was loaded, else None."""
    global DOTENV_PATH
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.environ.get("SUPERCHAT_NO_DOTENV"): return None
    if not os.path.isfile(path):
        if os.path.isfile(path + ".txt"): log.warning("%s not found, but %s.txt exists: rename it to .env (Notepad adds .txt)", path, path)
        return None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "): line = line[7:].lstrip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1); k, v = k.strip(), v.strip()
            if v[:1] in "\"'" and v.count(v[0]) >= 2: v = v[1:v.index(v[0], 1)]
            else: v = v.split(" #")[0].split("\t#")[0].strip()
            os.environ.setdefault(k, v)
    DOTENV_PATH = path
    return path


load_dotenv()
E = os.environ.get


def mode(var, real_if=None, real="real"):
    """Resolved provider kind: explicit variable wins; otherwise `real` when its key/URL variable is present; otherwise mock-like default."""
    v = E(var)
    return v.lower() if v else (real if real_if and E(real_if) else None)


class Resilient:
    """Proxy: call the real repository; on ProviderError either re-raise (error) or serve the mock (mock)."""
    def __init__(self, primary, fallback, mode):
        self._p, self._f, self._mode = primary, fallback, mode
    def __getattr__(self, name):
        attr = getattr(self._p, name)
        if not callable(attr) or self._mode != "mock": return attr
        def call(*a, **kw):
            try: return attr(*a, **kw)
            except ProviderError as e:
                log.warning("%s.%s failed (%s) -> falling back to mock data", type(self._p).__name__, name, e)
                return getattr(self._f, name)(*a, **kw)
        return call


def _wrap(real, mock): return Resilient(real, mock, E("SUPERCHAT_FALLBACK", "error").lower())


def build_flights():
    kind = mode("SUPERCHAT_FLIGHTS", "AVIASALES_TOKEN", "aviasales") or "mock"
    if kind == "mock": return JsonFlightRepository()
    if kind == "aviasales":
        from adapters.aviasales import AviasalesFlightRepository
        return _wrap(AviasalesFlightRepository(E("AVIASALES_TOKEN"), E("AVIASALES_MARKER"), E("AVIASALES_CURRENCY", "usd")), JsonFlightRepository())
    raise ProviderError(f"unknown SUPERCHAT_FLIGHTS={kind}")


def build_products():
    kind = mode("SUPERCHAT_PRODUCTS", "ECOMMERCE_BASE_URL", "generic") or "mock"
    if kind == "mock": return JsonProductRepository()
    from adapters.ecommerce import EcommerceProductRepository, FieldMap
    if kind == "dummyjson":
        repo = EcommerceProductRepository.dummyjson()
        if E("ECOMMERCE_RATE"): repo.rate = float(E("ECOMMERCE_RATE"))
    elif kind == "generic":
        if not E("ECOMMERCE_BASE_URL"): raise ProviderError("ECOMMERCE_BASE_URL is not set")
        repo = EcommerceProductRepository(E("ECOMMERCE_BASE_URL"), E("ECOMMERCE_SEARCH_PATH", "/products/search"),
                                          E("ECOMMERCE_QUERY_PARAM", "q"), FieldMap(**json.loads(E("ECOMMERCE_FIELDS", "{}"))),
                                          E("ECOMMERCE_TOKEN"), float(E("ECOMMERCE_RATE", "1")))
    else: raise ProviderError(f"unknown SUPERCHAT_PRODUCTS={kind}")
    return _wrap(repo, JsonProductRepository())


def build_showtimes():
    kind = mode("SUPERCHAT_CINEMA", "CINEMAPLUS_BASE_URL", "cinemaplus") or "mock"
    if kind == "mock": return JsonShowtimeRepository()
    if kind == "cinemaplus":
        from adapters.cinemaplus import CinemaPlusShowtimeRepository
        if not E("CINEMAPLUS_BASE_URL"): raise ProviderError("CINEMAPLUS_BASE_URL is not set")
        cfg = {}
        if E("CINEMAPLUS_CONFIG"):                               # JSON file with {"endpoints": {...}, "fields": {...}} from the partner docs
            try:
                with open(E("CINEMAPLUS_CONFIG"), encoding="utf-8") as f: cfg = json.load(f)
            except (OSError, ValueError) as e: raise ProviderError(f"cannot read CINEMAPLUS_CONFIG: {e}")
        ep = {**cfg.get("endpoints", {}), **json.loads(E("CINEMAPLUS_ENDPOINTS", "{}"))}
        fl = {**cfg.get("fields", {}), **json.loads(E("CINEMAPLUS_FIELDS", "{}"))}
        repo = CinemaPlusShowtimeRepository(E("CINEMAPLUS_BASE_URL"), E("CINEMAPLUS_TOKEN"), ep or None, fl or None,
                                            auth_header=E("CINEMAPLUS_AUTH_HEADER", cfg.get("auth_header", "Authorization")),
                                            auth_scheme=E("CINEMAPLUS_AUTH_SCHEME", cfg.get("auth_scheme", "Bearer")),
                                            auth_query=E("CINEMAPLUS_AUTH_QUERY", cfg.get("auth_query")))
        return _wrap(repo, JsonShowtimeRepository())
    raise ProviderError(f"unknown SUPERCHAT_CINEMA={kind}")


def build_vision():
    kind = mode("SUPERCHAT_VISION", "ANTHROPIC_API_KEY", "anthropic") or "color"
    if kind == "color":
        from adapters.vision import ColorVisionProvider; return ColorVisionProvider()
    if kind == "anthropic":
        from adapters.vision import AnthropicVisionProvider
        return AnthropicVisionProvider(E("ANTHROPIC_API_KEY"), E("ANTHROPIC_VISION_MODEL", "claude-sonnet-5-5"))
    raise ProviderError(f"unknown SUPERCHAT_VISION={kind}")


def all_mock(): return (mode("SUPERCHAT_FLIGHTS", "AVIASALES_TOKEN", "aviasales") or "mock") == "mock" and \
    (mode("SUPERCHAT_PRODUCTS", "ECOMMERCE_BASE_URL", "generic") or "mock") == "mock" and \
    (mode("SUPERCHAT_CINEMA", "CINEMAPLUS_BASE_URL", "cinemaplus") or "mock") == "mock"


def providers():
    """Which implementation is active per domain (no secrets). Used by /api/health and the start-up log."""
    return {"flights": mode("SUPERCHAT_FLIGHTS", "AVIASALES_TOKEN", "aviasales") or "mock",
            "products": mode("SUPERCHAT_PRODUCTS", "ECOMMERCE_BASE_URL", "generic") or "mock",
            "cinema": mode("SUPERCHAT_CINEMA", "CINEMAPLUS_BASE_URL", "cinemaplus") or "mock",
            "vision": mode("SUPERCHAT_VISION", "ANTHROPIC_API_KEY", "anthropic") or "color",
            "fallback": E("SUPERCHAT_FALLBACK", "error").lower()}
