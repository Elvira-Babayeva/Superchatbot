"""Data access layer. The bot only talks to these interfaces, so the JSON
implementations (mock) can be swapped for real API adapters (see adapters/ and
config.py) without touching bot.py.

Normalised schemas (every adapter must return exactly these shapes):
  flight   {id, airline, flight_number, origin{code,city}, destination{code,city}, departure_local, arrival_local,
            duration_min, stops, via, price_azn, cabin, baggage_kg, seats_available, [booking_url]}
  product  {id, name{en,az,ru}, category, brand, price_azn, rating, stock, color, description, [image_url, url]}
  movie    {id, title, genre[], language, duration_min, age_rating, rating, synopsis}
  cinema   {id, name, district, address}
  showtime {id, movie_id, cinema_id, date, time, hall, price_azn, seats_total, seats_available}
"""
import json, os
from abc import ABC, abstractmethod

DATA_DIR = os.environ.get("SUPERCHAT_DATA", os.path.join(os.path.dirname(__file__), "data"))


class ProviderError(Exception):
    """A real API failed (network, auth, bad payload). The bot turns this into a friendly message."""


def _load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


class FlightRepository(ABC):
    supports_reserve = True          # real "search-only" providers set this to False and expose booking_url

    @abstractmethod
    def all(self): ...
    @abstractmethod
    def reserve(self, flight_id, seats): ...

    def search(self, origin=None, dest=None, date=None):
        """origin/dest = airport codes used by the bot (GYD, IST, ...), date = datetime.date or None."""
        return [f for f in self.all() if (not origin or f["origin"]["code"] == origin) and
                (not dest or f["destination"]["code"] == dest) and
                (not date or f["departure_local"][:10] == date.isoformat())]

    def airlines(self): return sorted({f["airline"] for f in self.all()})
    def available_dates(self, origin=None, dest=None):
        return sorted({f["departure_local"][:10] for f in self.search(origin, dest)})


class ProductRepository(ABC):
    live = False        # True for real shop APIs: the catalogue cannot be enumerated, so the router probes it with a search
    @abstractmethod
    def all(self): ...
    @abstractmethod
    def get(self, product_id): ...

    def search(self, terms):
        """terms = list of normalised keywords. The bot re-ranks the result locally, so returning
        a superset is fine (the JSON mock returns the whole catalogue)."""
        return self.all()

    def vocabulary(self):
        """Words that make a message 'shopping' (product names and brands; real shop adapters add categories)."""
        return {w for p in self.all() for w in (*p["name"].values(), p["brand"])}


class ShowtimeRepository(ABC):
    @abstractmethod
    def movies(self): ...
    @abstractmethod
    def cinemas(self): ...
    @abstractmethod
    def showtimes(self): ...
    @abstractmethod
    def reserve(self, showtime_id, seats): ...

    supports_reserve = True
    def checkout_url(self, showtime_id): return None


class _Reservable:
    def _reserve(self, rows, rid, n):
        row = next(r for r in rows if r["id"] == rid)
        if row["seats_available"] < n:
            return None
        row["seats_available"] -= n
        return f"R-{rid}-{n}-{abs(hash((rid, row['seats_available']))) % 10000:04d}"


class JsonFlightRepository(FlightRepository, _Reservable):
    def __init__(self): self._d = _load("flights.json")
    def all(self): return self._d
    def reserve(self, flight_id, seats): return self._reserve(self._d, flight_id, seats)


class JsonProductRepository(ProductRepository):
    def __init__(self): self._d = _load("products.json")
    def all(self): return self._d
    def get(self, product_id): return next((p for p in self._d if p["id"] == product_id), None)


class JsonShowtimeRepository(ShowtimeRepository, _Reservable):
    def __init__(self):
        self._m, self._c, self._s = _load("movies.json"), _load("cinemas.json"), _load("showtimes.json")
    def movies(self): return self._m
    def cinemas(self): return self._c
    def showtimes(self): return self._s
    def reserve(self, showtime_id, seats): return self._reserve(self._s, showtime_id, seats)
