"""Fetch real Astana POIs from OpenStreetMap via Overpass.

Single Overpass query over the Astana bounding box; cache the raw response
to data/osm_astana.json, then write a normalized list to data/osm_places.json
shaped for `scripts/seed.py --source osm`.

The output rows map OSM tags to our existing `place_categories.code` values
defined in `scripts/seed.py` (CATEGORY_TREE). We deliberately fetch *only*
the leaf categories the seed schema supports, so the downstream code path
needs no changes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import urllib.parse
import urllib.request

# Astana bounding box — matches scripts/seed.py
LAT_MIN, LAT_MAX = 51.05, 51.25
LON_MIN, LON_MAX = 71.30, 71.60

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM tag → leaf category code
AMENITY_MAP = {
    "cafe": "cafe",
    "restaurant": "restaurant",
    "bar": "bar",
    "pub": "bar",
    "fast_food": "fast_food",
    "pharmacy": "pharmacy",
    "bank": "bank",
    "atm": "atm",
}
TOURISM_MAP = {
    "attraction": "attraction",
    "museum": "museum",
    "hotel": "hotel",
    "viewpoint": "viewpoint",
    "guest_house": "guest_house",
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_PATH = DATA_DIR / "osm_astana.json"
PARSED_PATH = DATA_DIR / "osm_places.json"


def build_query() -> str:
    bbox = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"
    amen_re = "|".join(AMENITY_MAP)
    tour_re = "|".join(TOURISM_MAP)
    return f"""
[out:json][timeout:60];
(
  node["amenity"~"^({amen_re})$"]["name"]({bbox});
  way["amenity"~"^({amen_re})$"]["name"]({bbox});
  node["tourism"~"^({tour_re})$"]["name"]({bbox});
  way["tourism"~"^({tour_re})$"]["name"]({bbox});
);
out center tags;
""".strip()


def fetch() -> dict:
    if RAW_PATH.exists():
        print(f"using cached {RAW_PATH}")
        return json.loads(RAW_PATH.read_text())
    query = build_query()
    print("hitting Overpass…")
    req = urllib.request.Request(
        OVERPASS_URL,
        data=("data=" + urllib.parse.quote(query)).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "voygr-inspired-pet-project/0.1 (https://github.com/local-dev; not for production)",
            "Accept": "application/json",
        },
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
    print(f"  {len(raw):,} bytes in {time.time()-t0:.1f}s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(raw)
    return json.loads(raw)


def parse(raw: dict) -> list[dict]:
    out = []
    seen_keys: set[tuple] = set()  # (name, round(lat,4), round(lng,4))
    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        amenity = tags.get("amenity")
        tourism = tags.get("tourism")
        cat = AMENITY_MAP.get(amenity) or TOURISM_MAP.get(tourism)
        if not cat:
            continue

        if el["type"] == "node":
            lat, lng = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lng = c.get("lat"), c.get("lon")
        if lat is None or lng is None:
            continue

        key = (name.lower(), round(lat, 4), round(lng, 4))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        out.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "name": name,
            "name_en": tags.get("name:en"),
            "brand": tags.get("brand") or tags.get("brand:en"),
            "lat": lat,
            "lng": lng,
            "category": cat,
            "addr_street": tags.get("addr:street"),
            "addr_housenumber": tags.get("addr:housenumber"),
            "city": tags.get("addr:city") or "Astana",
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
        })
    return out


def main() -> None:
    raw = fetch()
    rows = parse(raw)
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2))

    print(f"parsed {len(rows)} unique named places")
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:12s} {n:5d}")
    print(f"\nwrote {PARSED_PATH}")


if __name__ == "__main__":
    main()
