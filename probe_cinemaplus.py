"""Check a CinemaPlus configuration against the REAL API:  python probe_cinemaplus.py
Uses the same env vars as run.py (CINEMAPLUS_BASE_URL, CINEMAPLUS_TOKEN, CINEMAPLUS_ENDPOINTS, CINEMAPLUS_FIELDS, CINEMAPLUS_CONFIG ...).
Prints how many movies / cinemas / sessions came back and which mapped fields are EMPTY in every row, i.e. the part of
ENDPOINTS / FIELDS that does not match the partner documentation yet."""
import os, sys
import config                                            # loads .env
from repos import ProviderError

REQUIRED = {"movies": ("id", "title", "duration_min"), "cinemas": ("id", "name"),
            "showtimes": ("id", "movie_id", "cinema_id", "date", "time", "price_azn", "seats_total")}


def coverage(repo):
    """-> {section: {"rows": n, "empty_fields": [...], "error": str|None}}  (pure function, tested with a fake transport)"""
    out = {}
    for name in ("movies", "cinemas", "showtimes"):
        try: rows = getattr(repo, name)()
        except ProviderError as e: out[name] = {"rows": 0, "empty_fields": list(REQUIRED[name]), "error": str(e)}; continue
        empty = [f for f in REQUIRED[name] if not any(r.get(f) not in (None, "", 0, 0.0) for r in rows)] if rows else list(REQUIRED[name])
        out[name] = {"rows": len(rows), "empty_fields": empty, "error": None}
    return out


def main():
    if os.environ.get("SUPERCHAT_CINEMA", "").lower() != "cinemaplus": os.environ["SUPERCHAT_CINEMA"] = "cinemaplus"
    repo = config.build_showtimes()
    repo = getattr(repo, "_p", repo)                     # unwrap the mock-fallback proxy: we want the real errors
    ok = True
    for name, r in coverage(repo).items():
        status = "OK " if r["rows"] and not r["empty_fields"] and not r["error"] else "FIX"
        ok &= status == "OK "
        print(f"[{status}] {name:10s} rows={r['rows']:<4d} " + (f"error: {r['error']}" if r["error"] else
              (f"fields never filled: {', '.join(r['empty_fields'])}" if r["empty_fields"] else "all required fields present")))
    print("\nNext: edit CINEMAPLUS_ENDPOINTS / CINEMAPLUS_FIELDS (or CINEMAPLUS_CONFIG) until every line says OK." if not ok else "\nMapping looks consistent.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__": main()
