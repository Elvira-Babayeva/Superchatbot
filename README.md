# SuperChat (Phase 3: hands-free voice with VAD, real-API switches, confidence-aware visual search)

One chat for Flights, Shopping and Cinema. Rule-based hybrid router, no LLM needed for chat.
Languages: English, Azerbaijani, Russian (replies follow the user's language).
**Defaults to offline mock data**, so `python run.py` works with no keys.

## Run
    pip install -r requirements.txt
    python run.py                  # http://localhost:5000
    python tests.py                # 25 routing checks (original brief)
    python tests_adapters.py       # adapters, vision confidence, config, endpoints (fake HTTP, no network)
    node tests_vad.js              # voice-activity detection logic (synthetic audio levels)
    node tests_handsfree.js        # hands-free glue: VAD <-> recogniser <-> TTS <-> /api/chat with faked browser APIs
    python diagnose.py             # ONE command that checks every real API (Aviasales, shop, Claude Vision) and prints the provider's own error
    python probe_cinemaplus.py     # against the REAL CinemaPlus API: which mapped fields come back empty
    GET /api/health                # which providers are active (mock vs real), never shows keys

## Structure
- `repos.py`        – repository interfaces + JSON (mock) implementations, normalised schemas, `ProviderError`.
- `adapters/`       – `aviasales.py`, `ecommerce.py`, `cinemaplus.py`, `vision.py`, shared `http.py` (timeouts, retries, TTL cache).
- `config.py`       – picks the implementation per domain from environment variables (see `.env.example`).
- `bot.py`          – router, per-domain state, Flights / Shopping / Cinema modules, visual-search logic, i18n.
- `run.py`          – Flask: `POST /api/chat`, `POST /api/visual-search`.  `static/index.html` – UI with voice + photo.

## Switching mock -> real (no code changes)
| Domain | Variable | Values |
|---|---|---|
| Flights | `SUPERCHAT_FLIGHTS` | `mock` / `aviasales` (+ `AVIASALES_TOKEN`, optional `AVIASALES_MARKER`) |
| Shopping | `SUPERCHAT_PRODUCTS` | `mock` / `dummyjson` (free demo API) / `generic` (+ `ECOMMERCE_BASE_URL`, `ECOMMERCE_FIELDS` JSON map) |
| Cinema | `SUPERCHAT_CINEMA` | `mock` / `cinemaplus` (+ `CINEMAPLUS_BASE_URL`, `CINEMAPLUS_TOKEN`) |
| Visual search | `SUPERCHAT_VISION` | `color` (offline) / `anthropic` (+ `ANTHROPIC_API_KEY`) |
| Failure policy | `SUPERCHAT_FALLBACK` | `error` (friendly message, default) / `mock` (serve mock data) |

Example: `SUPERCHAT_FLIGHTS=aviasales AVIASALES_TOKEN=... SUPERCHAT_PRODUCTS=dummyjson python run.py`.
Missing keys/URLs fail at start-up with a clear message. To add another provider, implement the interface in `repos.py`
(return the normalised schema documented at the top of that file) and add one branch in `config.py`.

## What each real provider can and cannot do
- **Aviasales** (Travelpayouts Data API): returns *cached* lowest prices, not live seats. Seats/baggage are therefore not shown, and
  "book" returns the Aviasales deep link instead of a booking code (`supports_reserve = False`). Arrival time = departure + duration.
  Prices are converted to AZN with a static table in `adapters/aviasales.py` (`FX_TO_AZN`) – replace with a live rate for production.
- **E-commerce**: mapping-driven (`FieldMap`, dotted JSON paths). Cart keeps a snapshot of name/price at the time of adding.
  Real catalogues cannot be listed, so an unrecognised message triggers one cached product search before the bot asks "which domain?".
- **CinemaPlus**: **not verified against official documentation.** No public CinemaPlus API documentation could be found (third-party
  listings show the chain now trading as CineMastercard, with ticketing also sold via iticket.az). Endpoint paths, JSON field names and
  the auth style in `adapters/cinemaplus.py` are therefore assumptions. When you have the partner docs, put them in `CINEMAPLUS_CONFIG`
  (JSON: `endpoints`, `fields`, `auth_header`/`auth_scheme`/`auth_query`) or the matching env vars, then run `python probe_cinemaplus.py`
  - it lists every mapped field that comes back empty. Booking is a hand-off link unless a `reserve` endpoint is configured.

## When a real API does not work
1. `python diagnose.py` - PASS/WARN/FAIL/SKIP per part with the provider's own reason (`HTTP 401: invalid x-api-key`, `credit balance is too low`,
   `network error`, ...). Keys are never printed.
2. `http://localhost:5000/api/health` - active providers, whether `.env` was found, and the last 10 provider errors (keys redacted).
3. With `SUPERCHAT_DEBUG=1` the chat itself shows `[debug] <reason>` under "service unavailable".
The server listens on 127.0.0.1 only (it holds paid keys) and photo searches are limited to 10/min per client.

## Voice assistant (STT + TTS + automatic start/end detection)
Browser-side. Three ways to talk, all in English / Azerbaijani / Russian (language selector; "auto" follows the conversation language):
- 🎧 **Hands-free (VAD)**: tap once, then just talk. `static/vad.js` measures microphone loudness, learns the background-noise level, detects
  when you **start** (needs ~150 ms of voice, so clicks/coughs are ignored) and when you **stop** (~0.9 s of silence, so short pauses don't cut
  you off), and then the Web Speech recogniser's transcript is sent automatically. Talking over the assistant interrupts it (barge-in); while it
  speaks, the recogniser is paused and the VAD thresholds are raised, so it does not transcribe or react to its own voice. Headphones make
  barge-in more reliable on laptop speakers.
- 🎤 push-to-talk (browser's own end-of-speech detection) and 🔊 read replies aloud (`speechSynthesis`) work as before.
- Clarification questions (photo search) are read aloud as the question itself, with tappable answers.
Needs Chrome/Edge/Safari and HTTPS or `localhost`; Firefox has no speech recognition. Chrome sends audio to Google for recognition.
The recognition language is fixed when listening starts, so pick "Azərbaycan" / "Русский" in the selector for a first utterance in that language.
An Azerbaijani TTS voice may not be installed; the reply stays visible as text.

## Visual search (Claude Vision, with confidence)
📷 (or drag-and-drop / paste). The photo is resized in the browser, validated and re-encoded on the server (EXIF/GPS stripped), kept in memory
only, and sent to Claude (`SUPERCHAT_VISION=anthropic`, model `ANTHROPIC_VISION_MODEL`, default `claude-sonnet-5-5`). Claude returns up to 3 ranked
candidates with a 0-1 confidence; the bot acts on it (thresholds in `.env.example`):
| Confidence | Behaviour |
|---|---|
| >= 0.75 | answers: "…(92% confident). Closest matches in the catalogue" |
| 0.40 - 0.75 | shows the matches **and asks** "Is that right?" with Yes / No buttons |
| two candidates within 0.15 | asks "laptop (62%) or backpack (55%)?" with a button each |
| < 0.40 or nothing recognised | **does not search**, asks what it is |
The answer ("yes", "no, running shoes", "the second one", "backpack", or a button) is understood in EN/AZ/RU; any other message is treated as a normal chat message.
A readable film-poster title that exists in the cinema catalogue goes straight to showtimes. Without a key the offline `color` provider is used: it
only matches by dominant colour and says so. **The confidence is the model's own estimate, not a calibrated probability** - use it as a
ranking/threshold signal and tune `VS_*` against your own photos.

## Known limitations
Rule-based text matching; Azerbaijani city detection relies on common suffixes; mock reservations only change in-memory seat counts;
sessions are in memory; the real adapters were tested against fake HTTP responses only (no network in the build environment),
so verify them once with real keys. The VAD is tested with synthetic loudness values and the hands-free page logic with faked browser APIs - not with a real microphone, browser or speakers, so tune the VAD constants in `static/vad.js` on your devices.
