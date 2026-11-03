"""Single command to start SuperChat:  python run.py  ->  http://localhost:5000"""
import logging, os, time
from collections import defaultdict, deque
from flask import Flask, jsonify, request, send_from_directory
from bot import Session, respond, visual_search, t, down
import config
from config import build_vision, providers
from adapters.http import redact
from adapters.vision import prepare_image
from repos import ProviderError

logging.basicConfig(level=logging.INFO)
app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024          # a little over the 5 MB image limit
SESSIONS = {}
VISION = build_vision()                                      # fails fast if SUPERCHAT_VISION=anthropic has no key

log = logging.getLogger("superchat")
log.info("providers: %s | .env: %s", providers(), config.DOTENV_PATH or "not found (using process environment only)")


class _Ring(logging.Handler):
    """Keeps the last warnings (provider failures) so /api/health can show WHY a real API is not working. Secrets are redacted."""
    def __init__(self): super().__init__(logging.WARNING); self.items = deque(maxlen=10)
    def emit(self, rec): self.items.append({"time": time.strftime("%H:%M:%S"), "where": rec.name, "message": redact(rec.getMessage())[:300]})
RING = _Ring(); logging.getLogger().addHandler(RING)

_HITS = defaultdict(deque)
def rate_limited(key, limit, per=60):
    """Sliding-window limiter: real vision calls cost money, so one client may not spam them."""
    now = time.monotonic(); q = _HITS[key]
    while q and now - q[0] > per: q.popleft()
    if len(q) >= limit: return True
    q.append(now); return False

@app.get("/api/health")
def health():
    """Which providers are active (mock vs real). Never returns keys."""
    return jsonify(status="ok", providers=providers(), dotenv_loaded=bool(config.DOTENV_PATH), recent_errors=list(RING.items), voice={"vad": "browser (static/vad.js)", "languages": ["en-US", "az-AZ", "ru-RU"]})

@app.get("/")
def index(): return send_from_directory("static", "index.html")

@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    msg = (body.get("message") or "").strip()
    S = SESSIONS.setdefault(body.get("session_id", "default"), Session())
    if not msg: return jsonify(reply="", domains=[])
    reply, domains = respond(S, msg)
    return jsonify(reply=reply, domains=domains, lang=S.lang, choices=S.choices)

@app.post("/api/visual-search")
def visual():
    """multipart/form-data: image=<file>, session_id=<str>, lang=<en|az|ru, optional>. Images are processed in memory only."""
    S = SESSIONS.setdefault(request.form.get("session_id", "default"), Session())
    if not S.lang and request.form.get("lang") in ("en", "az", "ru"): S.lang = request.form["lang"]
    if rate_limited(request.remote_addr or "?", int(os.environ.get("SUPERCHAT_PHOTOS_PER_MIN", "10"))):
        return jsonify(reply=t(S, "rate"), domains=[], lang=S.lang), 429
    f = request.files.get("image")
    try:
        if f is None: raise ValueError("no image")
        jpeg, _ = prepare_image(f.read())
    except ValueError:
        return jsonify(reply=t(S, "vs_bad"), domains=[], lang=S.lang), 400
    try:
        info = VISION.describe(jpeg)
    except ProviderError as e:
        app.logger.warning("vision failed: %s", e)
        return jsonify(reply=down(S, e), domains=[], lang=S.lang), 502
    reply, domains = visual_search(S, info)
    return jsonify(reply=reply, domains=domains, lang=S.lang, vision=info.get("provider"), choices=S.choices,
                   confidence=info.get("confidence"), candidates=[{"label": c["label"], "confidence": c["confidence"]} for c in info.get("candidates", [])])

@app.errorhandler(413)
def too_big(_):
    return jsonify(reply=t(Session(), "vs_bad"), domains=[]), 413

if __name__ == "__main__":
    # localhost only by default: the server holds paid API keys. SUPERCHAT_HOST=0.0.0.0 exposes it to the network (then add authentication!)
    app.run(host=os.environ.get("SUPERCHAT_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
