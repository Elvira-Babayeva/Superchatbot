"""E-commerce adapter: one configurable REST client, mapping-driven, so any shop API with JSON search can be plugged in.

Presets:
  dummyjson  - https://dummyjson.com (free, no key, good for development and demos)
  generic    - your own shop: set ECOMMERCE_BASE_URL, ECOMMERCE_SEARCH_PATH, ECOMMERCE_TOKEN and a FieldMap
               (see FieldMap below: dotted paths into the JSON, so no code changes are needed for most shops).
Products are cached by id so the shopping cart keeps working between messages."""
from repos import ProductRepository, ProviderError
from .http import HttpClient

COLOR_WORDS = ["black", "white", "blue", "silver", "grey", "gray", "red", "green", "gold", "pink"]


def dig(obj, path, default=None):
    """dig({'a': {'b': [1]}}, 'a.b.0') -> 1"""
    for part in str(path).split("."):
        try: obj = obj[int(part)] if isinstance(obj, list) else obj[part]
        except (KeyError, IndexError, ValueError, TypeError): return default
    return obj


class FieldMap(dict):
    """path-of-each-normalised-field in the shop's JSON + where the list of items lives."""
    DEFAULT = {"items": "products", "id": "id", "name": "title", "brand": "brand", "category": "category", "price": "price",
               "rating": "rating", "stock": "stock", "description": "description", "image": "thumbnail", "color": None, "url": None}
    def __init__(self, **kw): super().__init__({**self.DEFAULT, **kw})


class EcommerceProductRepository(ProductRepository):
    live = True
    def __init__(self, base_url, search_path="/products/search", query_param="q", fields=None, token=None,
                 currency_rate=1.0, id_prefix="EC", categories_path=None, extra_params=None, client=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = client or HttpClient(base_url, headers, cache_ttl=300)
        self.path, self.qp, self.f = search_path, query_param, fields or FieldMap()
        self.rate, self.prefix, self.cat_path, self.extra = currency_rate, id_prefix, categories_path, extra_params or {}
        self._by_id, self._vocab = {}, set()

    @classmethod
    def dummyjson(cls, client=None):
        # DummyJSON prices are USD -> AZN at a configurable rate (ECOMMERCE_RATE)
        return cls("https://dummyjson.com", "/products/search", "q", FieldMap(), id_prefix="DJ", currency_rate=1.7,
                   categories_path="/products/category-list", extra_params={"limit": 20}, client=client)

    # ----- interface
    def all(self): return list(self._by_id.values())
    def get(self, product_id):
        if product_id in self._by_id: return self._by_id[product_id]
        raise ProviderError(f"unknown product {product_id}")      # only ids the bot has seen exist in the cart

    def search(self, terms):
        terms = [t for t in terms if t]
        if not terms: return []
        out = self._query(" ".join(terms))
        if not out and len(terms) > 1:                 # many shop APIs match the whole phrase only -> try each word
            seen = {}
            for t in terms[:4]:
                for p in self._query(t): seen[p["id"]] = p
            out = list(seen.values())
        return out

    def _query(self, q):
        data = self.http.get(self.path, {self.qp: q, **self.extra})
        rows = dig(data, self.f["items"], data if isinstance(data, list) else [])
        out = []
        for r in rows or []:
            p = self._normalise(r)
            if p: self._by_id[p["id"]] = p; out.append(p)
        return out

    def vocabulary(self):
        if not self._vocab and self.cat_path:
            try:
                cats = self.http.get(self.cat_path) or []
                self._vocab = {c.replace("-", " ") if isinstance(c, str) else str(c.get("name", "")) for c in cats}
                self._vocab = {w for c in self._vocab for w in c.lower().split()}
            except ProviderError: pass
        return self._vocab | {w for p in self._by_id.values() for w in (*p["name"].values(), p["brand"], p["category"])}

    # ----- mapping
    def _normalise(self, r):
        pid, name, price = dig(r, self.f["id"]), dig(r, self.f["name"]), dig(r, self.f["price"])
        if pid is None or not name or price is None: return None
        text = f"{name} {dig(r, self.f['description'], '')}".lower()
        color = (dig(r, self.f["color"]) if self.f["color"] else None) or next((c for c in COLOR_WORDS if c in text), "multicolor")
        stock = dig(r, self.f["stock"])
        return {"id": f"{self.prefix}{pid}", "name": {"en": str(name), "az": str(name), "ru": str(name)},
                "category": str(dig(r, self.f["category"], "") or ""), "brand": str(dig(r, self.f["brand"], "") or ""),
                "price_azn": round(float(price) * self.rate, 2), "rating": round(float(dig(r, self.f["rating"], 0) or 0), 1),
                "stock": int(stock) if stock is not None else 1, "color": "grey" if color == "gray" else color,
                "description": str(dig(r, self.f["description"], "") or "")[:160],
                "image_url": dig(r, self.f["image"]), "url": dig(r, self.f["url"]) if self.f["url"] else None}
