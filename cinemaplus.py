"""CinemaPlus adapter (Azerbaijan cinema chain; third-party listings show the chain now trading as CineMastercard).

STATUS: NOT VERIFIED AGAINST OFFICIAL DOCUMENTATION. No public CinemaPlus / CineMastercard API documentation could be found,
so the endpoint paths and JSON field names below are ASSUMPTIONS, not facts. Everything that depends on them lives in
ENDPOINTS / FIELDS / auth settings (overridable through CinemaPlusShowtimeRepository(...), env vars or a CINEMAPLUS_CONFIG
JSON file), so when you receive the partner documentation only that configuration needs editing - bot.py and the rest of the
adapter stay untouched. Run `python probe_cinemaplus.py` against the real API to see which mapped fields come back empty.
Auth (configurable): CINEMAPLUS_TOKEN is sent as `Authorization: Bearer <token>` by default; set CINEMAPLUS_AUTH_HEADER /
CINEMAPLUS_AUTH_SCHEME ("" for a raw key) for another header (e.g. X-Api-Key), or CINEMAPLUS_AUTH_QUERY for `?apikey=...`.
Booking is done via checkout_url (hand-off) unless the API gives a reservation endpoint (set ENDPOINTS['reserve'])."""
from datetime import date, timedelta
from repos import ShowtimeRepository, ProviderError
from .http import HttpClient
from .ecommerce import dig

ENDPOINTS = {"movies": "/movies", "cinemas": "/cinemas", "sessions": "/sessions", "reserve": None, "checkout": None}
FIELDS = {
    "movie":   {"id": "id", "title": "title", "genre": "genres", "language": "language", "duration_min": "duration",
                "age_rating": "ageRating", "rating": "rating", "synopsis": "description"},
    "cinema":  {"id": "id", "name": "name", "district": "district", "address": "address"},
    "session": {"id": "id", "movie_id": "movieId", "cinema_id": "cinemaId", "start": "startTime",   # ISO datetime
                "hall": "hall", "price": "price", "seats_total": "seatsTotal", "seats_available": "seatsAvailable"},
    "list_key": None,   # set to e.g. "data" if lists are wrapped as {"data": [...]}
}


class CinemaPlusShowtimeRepository(ShowtimeRepository):
    def __init__(self, base_url, token=None, endpoints=None, fields=None, days_ahead=7, client=None,
                 auth_header="Authorization", auth_scheme="Bearer", auth_query=None):
        headers, params = {}, {}
        if token and auth_query: params[auth_query] = token
        elif token: headers[auth_header] = f"{auth_scheme} {token}".strip()
        self.http = client or HttpClient(base_url, headers, cache_ttl=120, default_params=params)     # showtimes change; keep cache short
        self.ep, self.days = {**ENDPOINTS, **(endpoints or {})}, days_ahead
        self.F = {**FIELDS, **(fields or {})}
        self.supports_reserve = bool(self.ep.get("reserve"))
        self._sessions = {}

    def _list(self, path, params=None):
        data = self.http.get(path, params)
        key = self.F.get("list_key")
        rows = dig(data, key) if key else data
        if not isinstance(rows, list): raise ProviderError(f"unexpected payload from {path}")
        return rows

    def _map(self, row, kind, extra=None):
        out = {k: dig(row, p) for k, p in self.F[kind].items() if p and k != "start"}
        return out, extra

    def movies(self):
        out = []
        for r in self._list(self.ep["movies"]):
            m, _ = self._map(r, "movie")
            if m.get("id") is None or not m.get("title"): continue
            g = m.get("genre") or []
            out.append({"id": str(m["id"]), "title": m["title"], "genre": [x.lower() for x in (g if isinstance(g, list) else [g])],
                        "language": m.get("language") or "", "duration_min": int(m.get("duration_min") or 0),
                        "age_rating": m.get("age_rating") or "", "rating": float(m.get("rating") or 0),
                        "synopsis": m.get("synopsis") or ""})
        return out

    def cinemas(self):
        out = []
        for r in self._list(self.ep["cinemas"]):
            c, _ = self._map(r, "cinema")
            if c.get("id") is not None and c.get("name"):
                out.append({"id": str(c["id"]), "name": c["name"], "district": c.get("district") or "", "address": c.get("address") or ""})
        return out

    def showtimes(self):
        today, out = date.today(), []
        for r in self._list(self.ep["sessions"], {"from": today.isoformat(), "to": (today + timedelta(days=self.days)).isoformat()}):
            s, _ = self._map(r, "session")
            start = dig(r, self.F["session"]["start"])
            if s.get("id") is None or not start or len(str(start)) < 16: continue
            row = {"id": str(s["id"]), "movie_id": str(s["movie_id"]), "cinema_id": str(s["cinema_id"]), "date": str(start)[:10],
                   "time": str(start)[11:16], "hall": s.get("hall") or "", "price_azn": float(s.get("price") or 0),
                   "seats_total": int(s.get("seats_total") or 0), "seats_available": int(s.get("seats_available") or 0)}
            self._sessions[row["id"]] = row; out.append(row)
        return out

    def reserve(self, showtime_id, seats):
        if not self.supports_reserve: return None
        res = self.http.post(self.ep["reserve"], {"sessionId": showtime_id, "seats": seats})
        code = dig(res, "code") or dig(res, "reservationId")
        return str(code) if code else None

    def checkout_url(self, showtime_id):
        tpl = self.ep.get("checkout")
        return tpl.format(id=showtime_id) if tpl else None
