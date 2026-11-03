"""Check every REAL API with one tiny call each:   python diagnose.py
Prints PASS / WARN / FAIL / SKIP per part with the provider's own reason (e.g. "HTTP 401: invalid x-api-key"). Never prints keys.
Exit code 1 if anything FAILs. Costs a few tokens (one small Claude Vision call) and no money for Aviasales / DummyJSON."""
import io, sys, time
import config
from adapters.http import redact
from repos import ProviderError

HINT = {"auth": "the key/token was rejected: check it in .env (no spaces/quotes, not expired, right account)",
        "rate": "rate limit or quota exhausted: wait, or check your plan/balance",
        "network": "cannot reach the service: internet, firewall/proxy or DNS",
        "server": "the service itself is failing (5xx): retry later",
        "client": "the request was refused (400/404): wrong model name, endpoint or parameters"}


def test_image():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 240), "white"); ImageDraw.Draw(im).rectangle((90, 70, 230, 190), fill=(30, 90, 200))
    buf = io.BytesIO(); im.save(buf, "JPEG"); return buf.getvalue()


def _run(name, fn):
    t0 = time.time()
    try: status, msg = fn()
    except ProviderError as e:
        kind = getattr(e, "kind", "error"); status, msg = "FAIL", f"{redact(e)}" + (f"  ->  {HINT[kind]}" if kind in HINT else "")
    except Exception as e: status, msg = "FAIL", f"unexpected {type(e).__name__}: {redact(e)}"
    return {"name": name, "status": status, "msg": redact(msg), "ms": round((time.time() - t0) * 1000)}


def run_checks(flights, products, cinema, vision, modes):
    """modes = config.providers(); repos are the real (unwrapped) objects, so the true error is visible."""
    out = [{"name": ".env", "status": "PASS" if config.DOTENV_PATH else "WARN", "ms": 0,
            "msg": f"loaded {config.DOTENV_PATH}" if config.DOTENV_PATH else "no .env found next to run.py: only process environment variables are used"}]

    def fl():
        if modes["flights"] == "mock": return "SKIP", "mock flights (no AVIASALES_TOKEN)"
        rows = flights.search("GYD", "IST", None)
        if not rows: return "WARN", "API answered but has no cached prices for Baku->Istanbul this month (normal for quiet routes; try another route)"
        r = rows[0]; return "PASS", f"{len(rows)} cheapest-per-day prices, e.g. {r['airline']} {r['departure_local'][:10]} {r['price_azn']} AZN"
    out.append(_run("flights (Aviasales)", fl))

    def pr():
        if modes["products"] == "mock": return "SKIP", "mock catalogue (set SUPERCHAT_PRODUCTS=dummyjson)"
        rows = products.search(["laptop"])
        if not rows: return "WARN", "reachable, but no results for 'laptop'"
        return "PASS", f"{len(rows)} results for 'laptop', e.g. {rows[0]['name']['en']} {rows[0]['price_azn']} AZN"
    out.append(_run("shopping (" + modes["products"] + ")", pr))

    def ci():
        if modes["cinema"] == "mock": return "SKIP", "mock cinema data: no CinemaPlus API documentation/credentials configured (see README)"
        from probe_cinemaplus import coverage
        rep = coverage(cinema); bad = {k: v for k, v in rep.items() if v["error"] or v["rows"] == 0 or v["empty_fields"]}
        return ("FAIL", "; ".join(f"{k}: {v['error'] or ('no rows' if not v['rows'] else 'empty fields ' + ','.join(v['empty_fields']))}" for k, v in bad.items())) \
            if bad else ("PASS", "movies, cinemas and sessions map correctly")
    out.append(_run("cinema (" + modes["cinema"] + ")", ci))

    def vi():
        if modes["vision"] != "anthropic": return "SKIP", "offline colour matcher (set ANTHROPIC_API_KEY for Claude Vision)"
        info = vision.describe(test_image()); c = (info.get("candidates") or [{}])[0]
        if not c: return "WARN", f"Claude answered but named no object (caption: {info.get('caption')!r}); the API itself works"
        return "PASS", f"Claude Vision works: {c['label']!r} confidence {c['confidence']:.0%} (test image: a blue square)"
    out.append(_run("visual search (" + modes["vision"] + ")", vi))
    return out


def main():
    modes = config.providers()
    try:
        un = lambda x: getattr(x, "_p", x)
        res = run_checks(un(config.build_flights()), un(config.build_products()), un(config.build_showtimes()), config.build_vision(), modes)
    except ProviderError as e:                      # a missing secret fails fast in the builders
        res = [{"name": "configuration", "status": "FAIL", "msg": redact(e), "ms": 0}]
    for r in res: print(f"[{r['status']:4s}] {r['name']:32s} {r['ms']:5d} ms  {r['msg']}")
    sys.exit(1 if any(r["status"] == "FAIL" for r in res) else 0)


if __name__ == "__main__": main()
