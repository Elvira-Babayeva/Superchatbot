"""Adapter + voice/visual-search tests. No network: every HTTP call goes through a fake transport.
Run: python tests_adapters.py"""
import os; os.environ["SUPERCHAT_NO_DOTENV"] = "1"       # a developer .env must not change test results
import io, json, unittest
from PIL import Image
import bot
from bot import Session, respond, visual_search
from repos import ProviderError, JsonFlightRepository, JsonProductRepository, JsonShowtimeRepository
from adapters.http import HttpClient
from adapters.aviasales import AviasalesFlightRepository
from adapters.ecommerce import EcommerceProductRepository
from adapters.cinemaplus import CinemaPlusShowtimeRepository
from adapters.vision import ColorVisionProvider, AnthropicVisionProvider, prepare_image
import config


def fake(routes, calls=None):
    """routes: {url_substring: (status, json)} -> transport"""
    def transport(method, url, headers, body, timeout):
        if calls is not None: calls.append((method, url, headers, body))
        for key, (status, payload) in routes.items():
            if key in url: return status, json.dumps(payload).encode()
        return 404, b"{}"
    return transport

def client(base, routes, calls=None, **kw): return HttpClient(base, kw.pop("headers", {}), retries=0, transport=fake(routes, calls), **kw)

def use(**repos):
    """swap repositories inside bot.py and reset its catalogue caches"""
    saved = {k: getattr(bot, k) for k in repos}
    for k, v in repos.items(): setattr(bot, k, v)
    bot.CINE.t = bot.VOCAB.t = None
    return saved

def png(color, size=(80, 60)):
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "PNG"); return b.getvalue()


AV = {"success": True, "currency": "usd", "data": [
    {"origin": "BAK", "destination": "IST", "price": 150, "airline": "TK", "flight_number": "273", "transfers": 0, "duration": 225,
     "departure_at": "2026-11-06T07:30:00+04:00", "link": "/search/BAK0611IST1"},
    {"origin": "BAK", "destination": "IST", "price": 99, "airline": "PC", "flight_number": "", "transfers": 1, "duration": 410,
     "departure_at": "2026-11-06T22:10:00+04:00", "link": "/search/BAK0611IST2"},
    {"origin": "BAK", "destination": "IST", "price": "bad"}]}          # malformed row must be skipped


class Aviasales(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.repo = AviasalesFlightRepository("TOKEN", marker="123", client=client(
            "https://api.travelpayouts.com", {"prices_for_dates": (200, AV)}, self.calls, headers={"X-Access-Token": "TOKEN"}))
        self.saved = use(FR=self.repo)
    def tearDown(self): use(**self.saved)

    def test_normalised_schema_and_request(self):
        fl = self.repo.search("GYD", "IST", None)
        self.assertEqual(len(fl), 2)                                   # malformed row dropped
        f = fl[0]
        for k in ("id", "airline", "flight_number", "origin", "destination", "departure_local", "arrival_local", "duration_min",
                  "stops", "price_azn", "seats_available", "baggage_kg"): self.assertIn(k, f)
        self.assertEqual((f["airline"], f["price_azn"], f["arrival_local"]), ("Turkish Airlines", 255.0, "2026-11-06T11:15"))
        self.assertIn("marker=123", f["booking_url"])
        url = self.calls[0][1]
        self.assertIn("origin=BAK", url); self.assertIn("destination=IST", url); self.assertEqual(self.calls[0][2]["X-Access-Token"], "TOKEN")

    def test_chat_flow_and_booking_handoff(self):
        S = Session()
        r, d = respond(S, "Flights to Istanbul on 6 November"); self.assertEqual(d, ["flights"])
        self.assertIn("Turkish Airlines", r); self.assertIn("cached", r); self.assertNotIn("seats left", r)
        r, _ = respond(S, "Book the first one")
        self.assertIn("aviasales.com/search/", r); self.assertNotIn("Reserved", r)     # no fake booking code

    def test_other_month_dates(self):
        S = Session(); respond(S, "Flights to Istanbul on 15 December")
        self.assertIn("departure_at=2026-12-15", self.calls[-1][1])

    def test_missing_token_fails_fast(self):
        with self.assertRaises(ProviderError): AviasalesFlightRepository("")

    def test_http_error_becomes_friendly_message(self):
        use(FR=AviasalesFlightRepository("T", client=client("https://x", {"prices_for_dates": (500, {})})))
        r, _ = respond(Session(), "Flights to Istanbul on 6 November"); self.assertIn("temporarily unavailable", r)


DJ = {"products": [
    {"id": 7, "title": "Over-ear Wireless Headphones", "brand": "Sonic", "category": "audio", "price": 50, "rating": 4.46, "stock": 12,
     "description": "Black bluetooth headphones", "thumbnail": "http://x/y.png"},
    {"id": 8, "title": "Laptop Air", "category": "laptops", "price": 600, "rating": 4.2, "stock": 0, "description": "silver"}]}

class Ecommerce(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.repo = EcommerceProductRepository.dummyjson(client("https://dummyjson.com", {
            "category-list": (200, ["laptops", "smartphones"]), "products/search": (200, DJ)}, self.calls))
        self.saved = use(PR=self.repo)
    def tearDown(self): use(**self.saved)

    def test_schema(self):
        p = self.repo.search(["headphones"])[0]
        self.assertEqual((p["id"], p["price_azn"], p["color"], p["stock"]), ("DJ7", 85.0, "black", 12))
        self.assertEqual(set(p["name"]), {"en", "az", "ru"})

    def test_search_and_cart_with_real_adapter(self):
        S = Session()
        r, d = respond(S, "Do you have wireless headphones under 100 AZN?"); self.assertEqual(d, ["shopping"]); self.assertIn("Over-ear Wireless", r)
        r, _ = respond(S, "Add it to the cart"); self.assertIn("Added to cart", r)
        r, _ = respond(S, "Show my cart"); self.assertIn("85 AZN", r)

    def test_category_vocabulary_routes_to_shopping(self):
        _, d = respond(Session(), "laptops?"); self.assertEqual(d, ["shopping"])

    def test_generic_field_map(self):
        from adapters.ecommerce import FieldMap
        repo = EcommerceProductRepository("https://shop", "/s", "query", FieldMap(items="data.items", name="n", price="p.amount", id="sku", stock=None),
            client=client("https://shop", {"/s": (200, {"data": {"items": [{"sku": "A1", "n": "Red Kettle", "p": {"amount": 20}}]}})}), currency_rate=2)
        p = repo.search(["kettle"])[0]; self.assertEqual((p["id"], p["price_azn"], p["color"]), ("ECA1", 40.0, "red"))


CP = {"/movies": [{"id": 1, "title": "Orbit Zero", "genres": ["Sci-Fi"], "language": "English", "duration": 128, "ageRating": "12+", "rating": 8.1}],
      "/cinemas": [{"id": 9, "name": "CinemaPlus Ganjlik", "district": "Nasimi", "address": "Ganjlik Mall"}],
      "/sessions": [{"id": 55, "movieId": 1, "cinemaId": 9, "startTime": "2026-11-02T19:30:00", "hall": "Hall 1", "price": 12, "seatsTotal": 100, "seatsAvailable": 30}]}

class CinemaPlus(unittest.TestCase):
    def make(self, **kw):
        return CinemaPlusShowtimeRepository("https://cp", client=client("https://cp", {k: (200, v) for k, v in CP.items()}), **kw)
    def test_schema(self):
        r = self.make(); self.assertEqual(r.movies()[0]["genre"], ["sci-fi"])
        s = r.showtimes()[0]; self.assertEqual((s["id"], s["date"], s["time"], s["movie_id"]), ("55", "2026-11-02", "19:30", "1"))
    def test_chat_and_checkout_handoff(self):
        saved = use(SR=self.make(endpoints={"checkout": "https://cinemaplus.az/buy/{id}"}))
        try:
            S = Session(); r, d = respond(S, "What is playing tonight?"); self.assertEqual(d, ["cinema"]); self.assertIn("Orbit Zero", r)
            r, _ = respond(S, "Book two seats"); self.assertIn("https://cinemaplus.az/buy/55", r)
        finally: use(**saved)
    def test_no_checkout_configured(self):
        saved = use(SR=self.make())
        try:
            S = Session(); respond(S, "What is playing tonight?"); r, _ = respond(S, "Book one seat"); self.assertIn("isn't available", r)
        finally: use(**saved)
    def test_outage_does_not_break_other_domains(self):
        bad = CinemaPlusShowtimeRepository("https://cp", client=client("https://cp", {"/": (503, {})}))
        saved = use(SR=bad)
        try:
            r, d = respond(Session(), "Show me laptops."); self.assertEqual(d, ["shopping"]); self.assertIn("Laptop", r)
            r, _ = respond(Session(), "What is playing tonight?"); self.assertIn("temporarily unavailable", r)
        finally: use(**saved)


class Config(unittest.TestCase):
    def test_defaults_are_mock(self):
        self.assertIsInstance(config.build_flights(), JsonFlightRepository)
        self.assertIsInstance(config.build_products(), JsonProductRepository)
        self.assertIsInstance(config.build_showtimes(), JsonShowtimeRepository)
    def test_misconfiguration_fails_fast(self):
        import os
        for var, val, fn in (("SUPERCHAT_FLIGHTS", "aviasales", config.build_flights), ("SUPERCHAT_CINEMA", "cinemaplus", config.build_showtimes),
                             ("SUPERCHAT_PRODUCTS", "generic", config.build_products), ("SUPERCHAT_VISION", "anthropic", config.build_vision),
                             ("SUPERCHAT_FLIGHTS", "nope", config.build_flights)):
            os.environ[var] = val
            try:
                with self.assertRaises(ProviderError): fn()
            finally: del os.environ[var]
    def test_fallback_to_mock_mode(self):
        import os; os.environ["SUPERCHAT_FALLBACK"] = "mock"
        try:
            repo = config._wrap(AviasalesFlightRepository("T", client=client("https://x", {"/": (500, {})})), JsonFlightRepository())
            self.assertTrue(repo.search("GYD", "IST", None))          # served by the mock
            self.assertFalse(repo.supports_reserve)                   # attributes still come from the real repo
        finally: del os.environ["SUPERCHAT_FALLBACK"]


class Vision(unittest.TestCase):
    def test_color_provider(self):
        for rgb, name in (((10, 10, 10), "black"), ((245, 245, 245), "white"), ((30, 80, 210), "blue")):
            jpeg, _ = prepare_image(png(rgb)); self.assertEqual(ColorVisionProvider().describe(jpeg)["colors"], [name])
    def test_prepare_image_rejects_garbage_and_resizes(self):
        with self.assertRaises(ValueError): prepare_image(b"not an image")
        big = io.BytesIO(); Image.new("RGB", (3000, 2000), "red").save(big, "JPEG")
        out, mime = prepare_image(big.getvalue()); self.assertEqual(mime, "image/jpeg"); self.assertLessEqual(max(Image.open(io.BytesIO(out)).size), 1024)
    def test_anthropic_provider_parsing(self):
        calls = []
        reply = {"content": [{"type": "text", "text": '```json\n{"caption":"black over-ear headphones","keywords":["Headphones"],"brand":null,"colors":["black"],"text":[]}\n```'}]}
        p = AnthropicVisionProvider("KEY", client=client("https://api.anthropic.com", {"/v1/messages": (200, reply)}, calls))
        info = p.describe(b"\xff\xd8fake"); self.assertEqual((info["keywords"], info["colors"]), (["headphones"], ["black"]))
        body = json.loads(calls[0][3]); self.assertEqual(body["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")
    def test_anthropic_non_json_is_provider_error(self):
        p = AnthropicVisionProvider("KEY", client=client("https://a", {"/v1/messages": (200, {"content": [{"type": "text", "text": "sorry"}]})}))
        with self.assertRaises(ProviderError): p.describe(b"x")

    def test_visual_search_object_keywords(self):
        S = Session(); r, d = visual_search(S, {"keywords": ["headphones"], "colors": ["black"], "text": [], "caption": "black headphones"})
        self.assertEqual(d, ["shopping"]); self.assertIn("Wireless Headphones", r); self.assertEqual(S.last, "shopping")
        r, _ = respond(S, "Add the first one to the cart"); self.assertIn("Added to cart", r)         # follow-up works on the visual results
    def test_visual_search_colour_only_is_honest(self):
        r, d = visual_search(Session(), {"keywords": [], "colors": ["black"], "text": [], "caption": "dominant colour: black"})
        self.assertIn("only match by colour", r); self.assertEqual(d, ["shopping"])
    def test_visual_search_poster_goes_to_cinema(self):
        S = Session(); r, d = visual_search(S, {"keywords": [], "colors": [], "text": ["ORBIT ZERO"], "caption": "poster"})
        self.assertEqual(d, ["cinema"]); self.assertIn("Orbit Zero", r)
    def test_visual_search_no_match(self):
        r, _ = visual_search(Session(), {"keywords": ["submarine"], "colors": [], "text": [], "caption": "submarine"}); self.assertIn("couldn't find", r)


class Endpoint(unittest.TestCase):
    def setUp(self):
        import run; self.c = run.app.test_client(); run.SESSIONS.clear()
    def post(self, data, name="a.png"):
        return self.c.post("/api/visual-search", data={"session_id": "s1", "image": (io.BytesIO(data), name)}, content_type="multipart/form-data")
    def test_ok(self):
        r = self.post(png((10, 10, 10))); j = r.get_json(); self.assertEqual(r.status_code, 200)
        self.assertEqual(j["domains"], ["shopping"]); self.assertEqual(j["vision"], "color")
    def test_bad_image(self):
        r = self.post(b"hello"); self.assertEqual(r.status_code, 400); self.assertIn("couldn't read", r.get_json()["reply"])
    def test_missing_image(self):
        self.assertEqual(self.c.post("/api/visual-search", data={"session_id": "s"}).status_code, 400)
    def test_too_large(self):
        self.assertEqual(self.post(b"0" * (7 * 1024 * 1024)).status_code, 413)
    def test_chat_still_works(self):
        j = self.c.post("/api/chat", json={"session_id": "s2", "message": "Show me laptops."}).get_json(); self.assertEqual(j["domains"], ["shopping"])
    def test_vision_failure_is_502(self):
        import run
        class Boom:
            def describe(self, b): raise ProviderError("down")
        old, run.VISION = run.VISION, Boom()
        try: self.assertEqual(self.post(png((1, 1, 1))).status_code, 502)
        finally: run.VISION = old


def info(*cands, text=()):
    """fake vision result: cands = (label, keywords, confidence)"""
    cs = [{"label": l, "keywords": k, "brand": None, "colors": [], "confidence": c} for l, k, c in cands]
    return {"keywords": cs[0]["keywords"] if cs else [], "colors": [], "text": list(text), "caption": cs[0]["label"] if cs else "",
            "provider": "anthropic", "confidence": cs[0]["confidence"] if cs else 0.0, "candidates": cs}


class VisionConfidence(unittest.TestCase):
    def test_parse_clamps_sorts_and_distrusts(self):
        from adapters.vision import parse_vision
        j = {"caption": "x", "candidates": [{"label": "a", "keywords": ["Backpack"], "confidence": 7},
                                            {"label": "b", "keywords": ["laptop"], "confidence": "high"},
                                            {"label": "c", "keywords": ["shoes"], "confidence": 0.8}, "junk", {"label": "d", "keywords": ["e"]}]}
        out = parse_vision(j, "anthropic")
        self.assertEqual([c["confidence"] for c in out["candidates"]], [1.0, 0.8, 0.0]); self.assertEqual(len(out["candidates"]), 3)   # capped at 3
        self.assertEqual(out["confidence"], 1.0); self.assertEqual(out["keywords"], ["backpack"])
        self.assertEqual(parse_vision({"keywords": ["Laptop"]}, "anthropic")["confidence"], 0.0)       # old flat shape, no confidence -> unsure
    def test_provider_returns_confidence(self):
        body = {"content": [{"type": "text", "text": json.dumps({"caption": "headphones", "candidates": [
            {"label": "over-ear headphones", "keywords": ["headphones"], "brand": "Sony", "colors": ["black"], "confidence": 0.82}], "text": []})}]}
        p = AnthropicVisionProvider("K", client=client("https://api.anthropic.com", {"/v1/messages": (200, body)}))
        r = p.describe(b"x"); self.assertEqual(r["confidence"], 0.82); self.assertEqual(r["keywords"], ["headphones", "sony"])

    def test_high_confidence_answers_and_shows_score(self):
        S = Session(); r, d = visual_search(S, info(("over-ear headphones", ["headphones"], 0.92)))
        self.assertIn("92% confident", r); self.assertIn("Wireless Headphones", r); self.assertIsNone(S.vs); self.assertEqual(S.choices, [])
    def test_medium_confidence_answers_then_asks_to_confirm(self):
        S = Session(); r, _ = visual_search(S, info(("headphones", ["headphones"], 0.6)))
        self.assertIn("Wireless Headphones", r); self.assertIn("60% sure", r); self.assertIn("Is that right", r)
        self.assertEqual(S.choices, ["Yes", "No, something else"]); self.assertEqual(S.vs["state"], "confirm")
        r, _ = respond(S, "yes"); self.assertIn("add the first one", r); self.assertIsNone(S.vs)
        r, _ = respond(S, "Add the first one to the cart"); self.assertIn("Added to cart", r)
    def test_medium_then_no_then_user_names_it(self):
        S = Session(); visual_search(S, info(("headphones", ["headphones"], 0.6)))
        r, _ = respond(S, "no"); self.assertIn("what is it", r); self.assertEqual(S.vs["state"], "open")
        r, d = respond(S, "a backpack"); self.assertIn("Backpack", r); self.assertEqual(d, ["shopping"]); self.assertIsNone(S.vs)
    def test_no_with_inline_correction(self):
        S = Session(); visual_search(S, info(("headphones", ["headphones"], 0.6)))
        r, _ = respond(S, "no, running shoes"); self.assertIn("Running Shoes", r); self.assertIsNone(S.vs)
    def test_low_confidence_does_not_search_and_asks(self):
        S = Session(); r, d = visual_search(S, info(("some device", ["laptop"], 0.2)))
        self.assertNotIn("Laptop 15", r); self.assertIn("not sure", r); self.assertIn("20%", r); self.assertEqual(S.sh["res"], [])
        self.assertEqual(S.vs["state"], "open")
        r, _ = respond(S, "laptops"); self.assertIn("Laptop", r); self.assertIsNone(S.vs)
    def test_low_confidence_pick_candidate_by_name(self):
        S = Session(); visual_search(S, info(("laptop", ["laptop"], 0.3), ("backpack", ["backpack"], 0.2)))
        r, _ = respond(S, "backpack"); self.assertIn("Backpack 25L", r)
    def test_ambiguous_candidates_ask_which(self):
        S = Session(); r, _ = visual_search(S, info(("laptop", ["laptop"], 0.62), ("backpack", ["backpack"], 0.55)))
        self.assertIn("laptop (62%) or backpack (55%)", r); self.assertEqual(S.choices, ["laptop", "backpack"]); self.assertEqual(S.sh["res"], [])
        r, _ = respond(S, "the second one"); self.assertIn("Backpack 25L", r)
    def test_nothing_recognised(self):
        S = Session(); r, _ = visual_search(S, info()); self.assertIn("couldn't recognise", r); self.assertEqual(S.vs["state"], "open")
    def test_unrelated_message_clears_pending_and_routes_normally(self):
        S = Session(); visual_search(S, info(("headphones", ["headphones"], 0.6)))
        r, d = respond(S, "What is playing tonight?"); self.assertEqual(d, ["cinema"]); self.assertIsNone(S.vs)
    def test_poster_title_beats_confidence_flow(self):
        r, d = visual_search(Session(), info(("poster", ["poster"], 0.3), text=["ORBIT ZERO"])); self.assertEqual(d, ["cinema"])
    def test_russian_and_azerbaijani_clarification(self):
        S = Session(); S.lang = "ru"; r, _ = visual_search(S, info(("наушники", ["headphones"], 0.6)))
        self.assertIn("уверенность 60%", r); self.assertEqual(S.choices[0], "Да")
        r, _ = respond(S, "да"); self.assertIn("добавь первый", r)
        S = Session(); S.lang = "az"; r, _ = visual_search(S, info(("a", ["laptop"], 0.2)))
        self.assertIn("əmin deyiləm", r)
        S = Session(); S.lang = "az"; visual_search(S, info(("headphones", ["headphones"], 0.6)))
        r, _ = respond(S, "xeyr"); self.assertIn("nədir", r)
    def test_endpoint_exposes_confidence_and_choices(self):
        import run
        class Fake:
            def describe(self, b): return info(("headphones", ["headphones"], 0.6))
        old, run.VISION = run.VISION, Fake(); run.SESSIONS.clear(); c = run.app.test_client()
        try:
            j = c.post("/api/visual-search", data={"session_id": "v1", "image": (io.BytesIO(png((9, 9, 9))), "a.png")},
                       content_type="multipart/form-data").get_json()
            self.assertEqual(j["confidence"], 0.6); self.assertEqual(j["choices"], ["Yes", "No, something else"]); self.assertEqual(j["candidates"][0]["label"], "headphones")
            j = c.post("/api/chat", json={"session_id": "v1", "message": "Yes"}).get_json(); self.assertIn("add the first one", j["reply"]); self.assertEqual(j["choices"], [])
        finally: run.VISION = old


class EnableRealApis(unittest.TestCase):
    KEYS = ("SUPERCHAT_FLIGHTS", "SUPERCHAT_PRODUCTS", "SUPERCHAT_CINEMA", "SUPERCHAT_VISION", "AVIASALES_TOKEN", "ECOMMERCE_BASE_URL",
            "CINEMAPLUS_BASE_URL", "ANTHROPIC_API_KEY", "CINEMAPLUS_CONFIG", "CINEMAPLUS_AUTH_HEADER", "CINEMAPLUS_AUTH_SCHEME", "CINEMAPLUS_AUTH_QUERY", "TEST_DOTENV_A")
    def setUp(self):
        import os; self.os, self.saved = os, {k: os.environ.pop(k, None) for k in self.KEYS}
    def tearDown(self):
        for k, v in self.saved.items():
            if v is None: self.os.environ.pop(k, None)
            else: self.os.environ[k] = v
    def test_defaults_are_mock(self):
        self.assertEqual(config.providers(), {"flights": "mock", "products": "mock", "cinema": "mock", "vision": "color", "fallback": "error"})
    def test_keys_switch_real_mode_on(self):
        self.os.environ.update(AVIASALES_TOKEN="tok", ANTHROPIC_API_KEY="k", SUPERCHAT_PRODUCTS="dummyjson", CINEMAPLUS_BASE_URL="https://cp")
        self.assertEqual(config.providers(), {"flights": "aviasales", "products": "dummyjson", "cinema": "cinemaplus", "vision": "anthropic", "fallback": "error"})
        self.assertEqual(type(config.build_flights()._p).__name__, "AviasalesFlightRepository")
        self.assertEqual(type(config.build_products()._p).__name__, "EcommerceProductRepository")
        self.assertEqual(type(config.build_vision()).__name__, "AnthropicVisionProvider")
        self.assertFalse(config.all_mock())
    def test_explicit_setting_beats_key(self):
        self.os.environ.update(AVIASALES_TOKEN="tok", SUPERCHAT_FLIGHTS="mock"); self.assertEqual(config.providers()["flights"], "mock")
    def test_missing_secret_fails_fast(self):
        self.os.environ["SUPERCHAT_VISION"] = "anthropic"
        with self.assertRaises(ProviderError): config.build_vision()
        self.os.environ["SUPERCHAT_FLIGHTS"] = "aviasales"
        with self.assertRaises(ProviderError): config.build_flights()
    def test_dotenv_loader(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write('# comment\nTEST_DOTENV_A=hello   # inline comment\nTEST_DOTENV_B="quoted # not a comment"\nTEST_DOTENV_C={"items":"data.items"}\nAVIASALES_TOKEN=from-file\n')
        self.os.environ["AVIASALES_TOKEN"] = "from-env"; self.os.environ.pop("SUPERCHAT_NO_DOTENV")
        try: config.load_dotenv(f.name)
        finally: self.os.environ["SUPERCHAT_NO_DOTENV"] = "1"; config.DOTENV_PATH = None
        self.assertEqual((self.os.environ["TEST_DOTENV_A"], self.os.environ["TEST_DOTENV_B"], self.os.environ["TEST_DOTENV_C"]),
                         ("hello", "quoted # not a comment", '{"items":"data.items"}'))
        self.assertEqual(self.os.environ["AVIASALES_TOKEN"], "from-env")                   # real environment wins
        for k in ("TEST_DOTENV_B", "TEST_DOTENV_C"): self.os.environ.pop(k, None)
    def test_health_endpoint_shows_providers_but_no_secrets(self):
        import run; self.os.environ["AVIASALES_TOKEN"] = "SECRET-TOKEN-123"
        r = run.app.test_client().get("/api/health"); body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200); self.assertEqual(r.get_json()["providers"]["flights"], "aviasales"); self.assertNotIn("SECRET-TOKEN-123", body)

    # ---- CinemaPlus: auth styles + probe (documentation still has to confirm endpoints/fields)
    def test_cinemaplus_auth_styles(self):
        r = CinemaPlusShowtimeRepository("https://cp", "T"); self.assertEqual(r.http.headers, {"Authorization": "Bearer T"})
        r = CinemaPlusShowtimeRepository("https://cp", "T", auth_header="X-Api-Key", auth_scheme=""); self.assertEqual(r.http.headers, {"X-Api-Key": "T"})
        r = CinemaPlusShowtimeRepository("https://cp", "T", auth_query="apikey"); self.assertEqual((r.http.headers, r.http.default_params), ({}, {"apikey": "T"}))
    def test_default_params_reach_the_url(self):
        calls = []; c = client("https://cp", {"/movies": (200, [])}, calls, default_params={"apikey": "K"}); c.get("/movies", {"a": 1})
        self.assertIn("apikey=K", calls[0][1]); self.assertIn("a=1", calls[0][1])
    def test_config_file_and_env_overrides(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"endpoints": {"movies": "/api/v2/films"}, "auth_header": "X-Key", "auth_scheme": ""}, f)
        self.os.environ.update(SUPERCHAT_CINEMA="cinemaplus", CINEMAPLUS_BASE_URL="https://cp", CINEMAPLUS_TOKEN="T", CINEMAPLUS_CONFIG=f.name)
        repo = config.build_showtimes()._p; self.assertEqual(repo.ep["movies"], "/api/v2/films"); self.assertEqual(repo.http.headers, {"X-Key": "T"})
    def test_probe_reports_unmatched_fields(self):
        from probe_cinemaplus import coverage
        routes = {"/movies": (200, [{"id": 1, "title": "A", "duration": 100}]), "/cinemas": (200, [{"id": 2, "name": "C"}]),
                  "/sessions": (200, [{"id": 3, "movieId": 1, "cinemaId": 2, "startTime": "2026-11-05T19:30:00", "price": 12}])}   # seatsTotal missing
        rep = coverage(CinemaPlusShowtimeRepository("https://cp", client=client("https://cp", routes)))
        self.assertEqual(rep["movies"]["empty_fields"], []); self.assertEqual(rep["cinemas"]["empty_fields"], []); self.assertEqual(rep["showtimes"]["empty_fields"], ["seats_total"])
        rep = coverage(CinemaPlusShowtimeRepository("https://cp", client=client("https://cp", {})))
        self.assertTrue(all(v["error"] for v in rep.values()))


class RealApiDiagnostics(unittest.TestCase):
    AV = "/aviasales/v3/prices_for_dates"
    ROW = {"origin": "BAK", "destination": "IST", "price": 120, "airline": "TK", "flight_number": "123", "departure_at": "2026-11-06T07:30:00+04:00",
           "transfers": 0, "duration_to": 190, "link": "/search/X"}
    UNAUTH = (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    def test_http_error_carries_provider_reason_and_kind(self):
        with self.assertRaises(ProviderError) as cm: client("https://api.anthropic.com", {"/v1/messages": self.UNAUTH}).post("/v1/messages", {})
        self.assertIn("invalid x-api-key", str(cm.exception)); self.assertEqual((cm.exception.kind, cm.exception.status), ("auth", 401))
    def test_secrets_never_appear_in_errors(self):
        import os; os.environ["TEST_SECRET_KEY"] = "abcdefgh12345678"
        try:
            with self.assertRaises(ProviderError) as cm: client("https://x", {"/p": (400, {"message": "bad key abcdefgh12345678"})}).get("/p")
            self.assertNotIn("abcdefgh12345678", str(cm.exception)); self.assertIn("***", str(cm.exception))
        finally: os.environ.pop("TEST_SECRET_KEY")
    def modes(self, **kw): return {"flights": "aviasales", "products": "dummyjson", "cinema": "mock", "vision": "anthropic", **kw}
    def good(self):
        fl = AviasalesFlightRepository("T", client=client("https://api.travelpayouts.com", {self.AV: (200, {"success": True, "currency": "usd", "data": [self.ROW]})}))
        pr = EcommerceProductRepository.dummyjson(client("https://dummyjson.com", {"/products/search": (200, {"products": [
            {"id": 1, "title": "Laptop X", "price": 100, "category": "laptops", "brand": "B", "rating": 4, "stock": 3, "description": "silver", "thumbnail": "u"}]})}))
        vi = AnthropicVisionProvider("K", client=client("https://api.anthropic.com", {"/v1/messages": (200, {"content": [{"type": "text", "text": json.dumps(
            {"caption": "blue square", "candidates": [{"label": "square", "keywords": ["square"], "confidence": 0.9}]})}]})}))
        return fl, pr, vi
    def test_diagnose_all_pass(self):
        import diagnose; fl, pr, vi = self.good()
        res = diagnose.run_checks(fl, pr, None, vi, self.modes()); st = {r["name"].split(" ")[0]: r["status"] for r in res}
        self.assertEqual((st["flights"], st["shopping"], st["cinema"], st["visual"]), ("PASS", "PASS", "SKIP", "PASS"))
        by = {r["name"].split(" ")[0]: r["msg"] for r in res}
        self.assertIn("'square'", by["visual"]); self.assertIn("90%", by["visual"]); self.assertIn("170.0 AZN", by["shopping"]); self.assertIn("Turkish Airlines", by["flights"])
    def test_diagnose_reports_the_real_failure_reason(self):
        import diagnose
        fl = AviasalesFlightRepository("T", client=client("https://api.travelpayouts.com", {self.AV: (401, {"error": "Unauthorized token"})}))
        vi = AnthropicVisionProvider("K", client=client("https://api.anthropic.com", {"/v1/messages": self.UNAUTH}))
        res = {r["name"].split(" ")[0]: r for r in diagnose.run_checks(fl, self.good()[1], None, vi, self.modes())}
        self.assertEqual(res["visual"]["status"], "FAIL"); self.assertIn("invalid x-api-key", res["visual"]["msg"]); self.assertIn("rejected", res["visual"]["msg"])
        self.assertEqual(res["flights"]["status"], "FAIL"); self.assertIn("HTTP 401", res["flights"]["msg"])
    def test_diagnose_warns_on_empty_flights_and_skips_mocks(self):
        import diagnose
        fl = AviasalesFlightRepository("T", client=client("https://api.travelpayouts.com", {self.AV: (200, {"success": True, "data": []})}))
        res = {r["name"].split(" ")[0]: r["status"] for r in diagnose.run_checks(fl, None, None, None, self.modes(products="mock", vision="color"))}
        self.assertEqual((res["flights"], res["shopping"], res["visual"]), ("WARN", "SKIP", "SKIP"))
    def test_dotenv_handles_windows_notepad_files(self):
        import os, tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=".env", delete=False) as f:
            f.write(b"\xef\xbb\xbfTEST_BOM_A=1\r\nexport TEST_BOM_B=2\r\n")
        os.environ.pop("SUPERCHAT_NO_DOTENV")
        try: config.load_dotenv(f.name)
        finally: os.environ["SUPERCHAT_NO_DOTENV"] = "1"; config.DOTENV_PATH = None
        self.assertEqual((os.environ.pop("TEST_BOM_A", None), os.environ.pop("TEST_BOM_B", None)), ("1", "2"))
    def test_health_shows_recent_errors_without_secrets(self):
        import os, logging, run; os.environ["TEST_SECRET_TOKEN"] = "supersecretvalue99"
        try:
            logging.getLogger("superchat").warning("flights provider failed: HTTP 401 token supersecretvalue99")
            body = run.app.test_client().get("/api/health").get_data(as_text=True)
        finally: os.environ.pop("TEST_SECRET_TOKEN")
        self.assertIn("HTTP 401", body); self.assertNotIn("supersecretvalue99", body)
    def test_photo_search_is_rate_limited(self):
        import run
        class Fake:
            def describe(self, b): return info(("headphones", ["headphones"], 0.9))
        old, run.VISION = run.VISION, Fake(); run.SESSIONS.clear(); run._HITS.clear(); c = run.app.test_client(); codes = []
        try:
            for _ in range(12): codes.append(c.post("/api/visual-search", data={"session_id": "r", "image": (io.BytesIO(png((9, 9, 9))), "a.png")}, content_type="multipart/form-data").status_code)
        finally: run.VISION = old; run._HITS.clear()
        self.assertEqual(codes[:10], [200] * 10); self.assertEqual(codes[10:], [429, 429])
    def test_debug_mode_shows_reason_in_chat(self):
        import bot; S = Session(); old = bot.DEBUG
        try:
            bot.DEBUG = True; self.assertIn("[debug] HTTP 401: invalid x-api-key", bot.down(S, ProviderError("HTTP 401: invalid x-api-key")))
            bot.DEBUG = False; self.assertNotIn("debug", bot.down(S, ProviderError("x")))
        finally: bot.DEBUG = old


class Dates(unittest.TestCase):
    def test_months(self):
        from bot import parse_date, norm
        for txt, exp in (("15 december", (2026, 12, 15)), ("december 15", (2026, 12, 15)), ("3 yanvar", (2027, 1, 3)), ("5 мая", (2027, 5, 5)),
                         ("6 november", (2026, 11, 6)), ("20.12", (2026, 12, 20)), ("2nd nov", (2026, 11, 2))):
            d = parse_date(norm(txt))[0]; self.assertEqual((d.year, d.month, d.day), exp, txt)
        self.assertIsNone(parse_date(norm("rating 8.1"))[0]); self.assertIsNone(parse_date(norm("under 89.90 azn"))[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
