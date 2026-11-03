"""Aviasales / Travelpayouts Data API adapter  (https://api.travelpayouts.com/aviasales/v3/prices_for_dates).

IMPORTANT properties of this API (they shape the adapter):
  * It returns CACHED lowest prices found by users in the last ~48h, not live seat availability.
    -> seats_available / baggage_kg are unknown (seats = None, baggage = None) and are not invented.
  * It cannot create reservations. The booking_url deep link opens the Aviasales results page.
    -> supports_reserve = False; the bot shows the link instead of a fake booking code.
Needs a Travelpayouts token (free): AVIASALES_TOKEN.  Optional AVIASALES_MARKER appends your affiliate marker to links."""
from datetime import datetime, timedelta
from repos import FlightRepository, ProviderError
from .http import HttpClient

# bot airport code -> Aviasales city code (Aviasales searches by city)
CITY_CODE = {"GYD": "BAK", "IST": "IST", "DXB": "DXB", "SVO": "MOW", "TBS": "TBS", "TAS": "TAS"}
AIRPORT_OF = {v: k for k, v in CITY_CODE.items()}
CITY_NAME = {"GYD": "Baku", "IST": "Istanbul", "DXB": "Dubai", "SVO": "Moscow", "TBS": "Tbilisi", "TAS": "Tashkent"}
AIRLINES = {"J2": "AZAL Azerbaijan Airlines", "TK": "Turkish Airlines", "PC": "Pegasus", "EK": "Emirates", "FZ": "flydubai",
            "SU": "Aeroflot", "S7": "S7 Airlines", "A3": "Aegean", "HY": "Uzbekistan Airways", "TB": "Buta Airways",
            "W6": "Wizz Air", "QR": "Qatar Airways", "LH": "Lufthansa", "B2": "Belavia", "U6": "Ural Airlines"}
FX_TO_AZN = {"azn": 1.0, "usd": 1.70, "eur": 1.85, "rub": 0.0185, "try": 0.05}  # fallback only; see _to_azn


class AviasalesFlightRepository(FlightRepository):
    supports_reserve = False

    def __init__(self, token, marker=None, currency="usd", client=None, fx=None):
        if not token: raise ProviderError("AVIASALES_TOKEN is not set")
        self.currency, self.marker, self.fx = currency.lower(), marker, {**FX_TO_AZN, **(fx or {})}
        self.http = client or HttpClient("https://api.travelpayouts.com", {"X-Access-Token": token}, cache_ttl=600)
        self._seen, self._airlines = {}, set(AIRLINES.values())

    # ----- interface
    def all(self): return list(self._seen.values())          # only what has been searched so far
    def airlines(self): return sorted(self._airlines)

    def search(self, origin=None, dest=None, date=None):
        if not (origin and dest):                              # the API needs both ends
            return []
        params = {"origin": CITY_CODE.get(origin, origin), "destination": CITY_CODE.get(dest, dest),
                  "departure_at": date.isoformat() if date else None, "one_way": "true", "unique": "false",
                  "sorting": "price", "limit": 30, "currency": self.currency}
        if not date: params["departure_at"] = datetime.now().strftime("%Y-%m")   # month search = cheapest per day
        payload = self.http.get("/aviasales/v3/prices_for_dates", params)
        if not isinstance(payload, dict) or not payload.get("success", True):
            raise ProviderError(f"Aviasales error: {(payload or {}).get('error') if isinstance(payload, dict) else payload}")
        out = []
        for row in payload.get("data") or []:
            try: f = self._normalise(row, origin, dest, payload.get("currency") or self.currency)
            except (KeyError, ValueError, TypeError): continue             # skip malformed rows, never crash the chat
            self._seen[f["id"]] = f; out.append(f)
        return out

    def available_dates(self, origin=None, dest=None):
        # month-wide query already returns the cheapest ticket per day for the current month
        return sorted({f["departure_local"][:10] for f in self.search(origin, dest, None)})

    def reserve(self, flight_id, seats): return None          # not possible with this API; bot uses booking_url

    # ----- mapping
    def _to_azn(self, price, cur):
        rate = self.fx.get(cur.lower())
        if rate is None: raise ValueError(f"no FX rate for {cur}")
        return round(float(price) * rate, 2)

    def _normalise(self, r, origin, dest, cur):
        dep = datetime.fromisoformat(r["departure_at"])                 # e.g. 2026-11-06T07:30:00+04:00 (local to origin)
        dur = int(r.get("duration_to") or r.get("duration") or 0)
        arr = dep + timedelta(minutes=dur)                              # NOTE: API gives no arrival; = dep + duration (origin tz)
        code, num = r.get("airline", ""), str(r.get("flight_number") or "")
        name = AIRLINES.get(code, code)
        self._airlines.add(name)
        link = r.get("link") or ""
        url = "https://www.aviasales.com" + link if link.startswith("/") else link
        if url and self.marker: url += ("&" if "?" in url else "?") + "marker=" + self.marker
        transfers = int(r.get("transfers") or 0)
        return {"id": f"AV-{code}{num}-{dep:%Y%m%d%H%M}-{origin}{dest}", "airline": name, "flight_number": f"{code}{num}",
                "origin": {"code": origin, "city": CITY_NAME.get(origin, origin)},
                "destination": {"code": dest, "city": CITY_NAME.get(dest, dest)},
                "departure_local": dep.strftime("%Y-%m-%dT%H:%M"), "arrival_local": arr.strftime("%Y-%m-%dT%H:%M"),
                "duration_min": dur, "stops": transfers, "via": None, "price_azn": self._to_azn(r["price"], cur),
                "cabin": "economy", "baggage_kg": None, "seats_available": None, "booking_url": url or None,
                "source": "aviasales"}
