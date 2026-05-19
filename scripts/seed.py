"""Seed the canonical schema with 50k Astana places + mock enrichment.

Run after `docker compose up -d --build db`. Idempotent: drops + recreates the
schema using app/db/schema.sql, then inserts everything.

Prints two API keys for the demo customers — copy them somewhere safe.
"""
from __future__ import annotations

import json
import os
import random
import secrets
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import bcrypt
import psycopg

from app.core.config import DSN

# ---------- Astana geography + names (lifted from generate_pois.py) ----------

LAT_MIN, LAT_MAX = 51.05, 51.25
LON_MIN, LON_MAX = 71.30, 71.60

NAMES = ["Astana", "Nomad", "Steppe", "Silk", "Baiterek", "Khan", "Aldar",
         "Saryarka", "Esil", "Tselina", "Aral", "Tien Shan", "Altyn", "Dala"]
SUFFIXES = ["Cafe", "House", "Plaza", "Grill", "Lounge", "Inn", "Spot", "Place"]

STREET_NAMES = [
    "Kabanbay Batyr", "Mangilik El", "Republic", "Turan", "Saryarka",
    "Dostyk", "Nurly Zhol", "Kenesary", "Beibitshilik", "Tauelsizdik",
]

# Category tree — top-level groups + leaf categories
CATEGORY_TREE = [
    ("food_and_drink", "Food & Drink", None, [
        ("restaurant", "Restaurant"),
        ("cafe", "Cafe"),
        ("bar", "Bar"),
        ("fast_food", "Fast Food"),
    ]),
    ("services", "Services", None, [
        ("pharmacy", "Pharmacy"),
        ("bank", "Bank"),
        ("atm", "ATM"),
    ]),
    ("tourism", "Tourism", None, [
        ("attraction", "Attraction"),
        ("museum", "Museum"),
        ("hotel", "Hotel"),
        ("viewpoint", "Viewpoint"),
        ("guest_house", "Guest House"),
    ]),
]

# Hours patterns per category code
HOURS_PATTERNS: dict[str, tuple[time, time, list[int]]] = {
    "cafe":         (time(7, 30),  time(22, 0), [0, 1, 2, 3, 4, 5, 6]),
    "restaurant":   (time(11, 0),  time(23, 0), [0, 1, 2, 3, 4, 5, 6]),
    "bar":          (time(17, 0),  time(2, 0),  [2, 3, 4, 5, 6]),
    "fast_food":    (time(8, 0),   time(23, 0), [0, 1, 2, 3, 4, 5, 6]),
    "pharmacy":     (time(8, 0),   time(22, 0), [0, 1, 2, 3, 4, 5, 6]),
    "bank":         (time(9, 0),   time(18, 0), [0, 1, 2, 3, 4]),
    "atm":          (time(0, 0),   time(23, 59),[0, 1, 2, 3, 4, 5, 6]),
    "attraction":   (time(10, 0),  time(18, 0), [1, 2, 3, 4, 5, 6]),
    "museum":       (time(10, 0),  time(18, 0), [1, 2, 3, 4, 5, 6]),
    "hotel":        (time(0, 0),   time(23, 59),[0, 1, 2, 3, 4, 5, 6]),
    "viewpoint":    (time(0, 0),   time(23, 59),[0, 1, 2, 3, 4, 5, 6]),
    "guest_house":  (time(0, 0),   time(23, 59),[0, 1, 2, 3, 4, 5, 6]),
}

VIBE_KEYS = {
    "cafe":       ["quiet workspace", "good for studying", "specialty coffee", "cozy", "instagrammable", "popular with students"],
    "restaurant": ["date night", "family friendly", "fine dining", "casual", "local favorite", "tourist spot"],
    "bar":        ["nightlife", "live music", "craft cocktails", "sports bar", "rooftop"],
    "fast_food":  ["quick bite", "late night", "delivery friendly"],
    "pharmacy":   ["24 hours nearby", "modern"],
    "bank":       ["business district"],
    "atm":        ["24/7"],
    "attraction": ["instagrammable", "kid friendly", "iconic", "scenic"],
    "museum":     ["family friendly", "rainy day", "educational"],
    "hotel":      ["business travel", "spa", "rooftop view", "luxury"],
    "viewpoint":  ["scenic", "sunset spot", "instagrammable"],
    "guest_house":["cozy", "budget friendly", "local hosts"],
}

SOURCE_CATALOG = [
    ("directory", "2gis_kz", "https://2gis.kz/", 0.9),
    ("directory", "google_business", "https://maps.google.com/", 0.85),
    ("social",    "instagram", "https://instagram.com/", 0.6),
    ("web",       "official_site", None, 0.95),
    ("open_data", "osm", "https://openstreetmap.org/", 0.7),
]


def build_categories(cur) -> dict[str, int]:
    code_to_id: dict[str, int] = {}
    for parent_code, parent_name, _, children in CATEGORY_TREE:
        cur.execute(
            "INSERT INTO place_categories (code, name) VALUES (%s, %s) RETURNING id",
            (parent_code, parent_name),
        )
        parent_id = cur.fetchone()[0]
        code_to_id[parent_code] = parent_id
        for child_code, child_name in children:
            cur.execute(
                "INSERT INTO place_categories (parent_id, code, name) VALUES (%s, %s, %s) RETURNING id",
                (parent_id, child_code, child_name),
            )
            code_to_id[child_code] = cur.fetchone()[0]
    return code_to_id


def random_phone(rnd: random.Random) -> str:
    return f"+7-7172-{rnd.randint(100, 999)}-{rnd.randint(1000, 9999)}"


def random_address(rnd: random.Random) -> tuple[str, str, str]:
    house = str(rnd.randint(1, 250))
    street = rnd.choice(STREET_NAMES)
    formatted = f"{street} {house}, Astana, Kazakhstan"
    return street, house, formatted


def random_amenities(rnd: random.Random, category: str) -> dict | None:
    food_like = category in {"cafe", "restaurant", "bar", "fast_food"}
    hotel_like = category in {"hotel", "guest_house"}
    if not (food_like or hotel_like or category == "museum"):
        return None
    return {
        "wifi": rnd.random() < (0.9 if category == "cafe" else 0.6),
        "parking": rnd.choice(["none", "street", "lot", "garage"]),
        "outdoor_seating": rnd.random() < 0.35,
        "wheelchair_accessible": rnd.random() < 0.6,
        "vegan_options": rnd.random() < 0.4 if food_like else False,
        "pet_friendly": rnd.random() < 0.2,
        "kids_friendly": rnd.random() < 0.5,
        "price_level": rnd.choice(["$", "$$", "$$", "$$$", "$$$$"]),
    }


def hours_for(category: str, rnd: random.Random) -> list[tuple]:
    if category not in HOURS_PATTERNS:
        return []
    open_t, close_t, days = HOURS_PATTERNS[category]
    rows = []
    for d in days:
        # small per-day jitter
        jitter_min = rnd.choice([-30, 0, 0, 0, 30])
        o = (datetime.combine(datetime.today(), open_t) + timedelta(minutes=jitter_min)).time()
        c = close_t
        is_overnight = c <= o
        rows.append((d, o, c, is_overnight))
    return rows


def vibe_attributes(category: str, rnd: random.Random) -> list[tuple[str, str, str]]:
    keys = VIBE_KEYS.get(category, [])
    if not keys:
        return []
    sample = rnd.sample(keys, k=min(len(keys), rnd.randint(2, 5)))
    return [("vibe", k, "true") for k in sample]


def sources_for(rnd: random.Random, place_status: str) -> list[tuple]:
    """Produce sources whose status_signal + last_fetched correlate with place_status.

    This is what gives `validation.classify()` something to disagree about.
    Without varied signals, every place looks like unanimous "active" and the
    conflict-resolution policy never fires.
    """
    n = rnd.randint(1, 3)
    chosen = rnd.sample(SOURCE_CATALOG, k=n)
    rows = []
    now = datetime.now(timezone.utc)

    for i, (stype, name, url, rel) in enumerate(chosen):
        is_primary = i == 0

        if place_status == "permanently_closed":
            # At least one well-attested closure signal. Primary source (highest
            # reliability) carries the closed signal; secondary sources may still
            # show "active" (stale cache) to simulate real-world disagreement.
            signal = "closed" if is_primary else rnd.choice(["closed", "active"])
            days_ago = rnd.randint(0, 30)
        elif place_status == "temporarily_closed":
            # Mixed signals — closure mentioned but not authoritative
            signal = "closed" if rnd.random() < 0.5 else "active"
            days_ago = rnd.randint(0, 45)
        elif place_status == "unverified":
            # Sources are stale (> 60 days) — main reason we can't verify
            signal = "active"
            days_ago = rnd.randint(60, 180)
        else:  # "open"
            # Most active, but a small fraction of low-reliability sources
            # dissent (think: out-of-date social media post). Keeps the policy
            # interesting: a "closed" signal from rel<0.7 should NOT flip the
            # verdict.
            if not is_primary and rel < 0.7 and rnd.random() < 0.15:
                signal = "closed"
            else:
                signal = "active"
            days_ago = rnd.randint(0, 45)

        last_fetched = now - timedelta(days=days_ago)
        rows.append((stype, name, url, last_fetched, rel, is_primary, signal))
    return rows


# Category-based base popularity. Real places vary: hotels/museums get more
# attention than ATMs. Random noise is added on top, so the column is still
# noisy enough to make popularity a weak (not perfect) ranking signal.
CATEGORY_POPULARITY_BASE = {
    "hotel": 0.75, "museum": 0.70, "attraction": 0.70, "viewpoint": 0.65,
    "restaurant": 0.55, "cafe": 0.50, "bar": 0.45,
    "guest_house": 0.40, "fast_food": 0.35,
    "pharmacy": 0.25, "bank": 0.20, "atm": 0.10,
}


def popularity_for(category: str, rnd: random.Random) -> float:
    base = CATEGORY_POPULARITY_BASE.get(category, 0.4)
    return round(max(0.0, min(1.0, base + rnd.uniform(-0.15, 0.15))), 3)


def _load_osm_rows() -> list[dict]:
    """Read parsed OSM rows from data/osm_places.json. Run scripts.fetch_osm first."""
    osm_path = Path(__file__).resolve().parent.parent / "data" / "osm_places.json"
    if not osm_path.exists():
        raise SystemExit(
            f"{osm_path} not found. Run `python -m scripts.fetch_osm` first."
        )
    return json.loads(osm_path.read_text())


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", choices=("random", "osm"), default="random",
        help="random = generated NAMES × SUFFIXES (default, 50k places); "
             "osm = real Astana places from data/osm_places.json",
    )
    args = parser.parse_args()

    schema_path = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"
    schema_sql = schema_path.read_text()

    rnd = random.Random(42)
    osm_rows = _load_osm_rows() if args.source == "osm" else None

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            print("Dropping and recreating schema...")
            cur.execute("""
                DROP TABLE IF EXISTS places_vectors, place_feedback,
                    place_status_history, place_attributes, place_hours,
                    place_amenities, place_sources, place_addresses,
                    customer_place_refs, customer_accounts, places,
                    place_categories, pois CASCADE
            """)
            # types — drop if exists to allow rerun
            for t in [
                "place_status", "source_type", "parking_type",
                "price_level", "customer_ref_status", "attribute_value_type",
            ]:
                cur.execute(f"DROP TYPE IF EXISTS {t} CASCADE")
            cur.execute(schema_sql)

            print("Seeding categories...")
            code_to_id = build_categories(cur)

            print("Seeding customer accounts...")
            api_keys = []
            for i, name in enumerate(["demo-customer-1", "demo-customer-2"]):
                key = "vk_" + secrets.token_urlsafe(24)
                key_hash = bcrypt.hashpw(key.encode(), bcrypt.gensalt()).decode()
                cur.execute(
                    "INSERT INTO customer_accounts (name, api_key_hash, plan_tier) VALUES (%s, %s, %s)",
                    (name, key_hash, "demo"),
                )
                api_keys.append((name, key))

            print("Seeding places (this takes ~30s)...")
            leaf_codes = [c for c in code_to_id if c not in {"food_and_drink", "services", "tourism"}]

            # We insert places one at a time to capture UUIDs, then batch dependent rows.
            place_rows: list[tuple] = []
            address_rows: list[tuple] = []
            amenity_rows: list[tuple] = []
            hours_rows: list[tuple] = []
            attribute_rows: list[tuple] = []
            source_rows: list[tuple] = []

            # Pre-generate UUIDs via psycopg server-side default? Easier: use gen_random_uuid in INSERT RETURNING.
            # For speed, generate UUIDs client-side using uuid4.
            import uuid

            if osm_rows is not None:
                source_iter: list[dict | None] = list(osm_rows)
                print(f"  using OSM source: {len(source_iter)} real places")
            else:
                source_iter = [None] * 50000

            now = datetime.now(timezone.utc)
            for src in source_iter:
                pid = uuid.uuid4()
                name_local: str | None = None
                brand_name: str | None = None
                if src is None:
                    category = rnd.choice(leaf_codes)
                    name = f"{rnd.choice(NAMES)} {rnd.choice(SUFFIXES)}"
                    lon = rnd.uniform(LON_MIN, LON_MAX)
                    lat = rnd.uniform(LAT_MIN, LAT_MAX)
                    street, house, formatted = random_address(rnd)
                    phone = random_phone(rnd) if rnd.random() < 0.7 else None
                    website = f"https://{name.lower().replace(' ', '')}.kz" if rnd.random() < 0.4 else None
                else:
                    category = src["category"]
                    if category not in code_to_id:
                        continue  # category not in our schema
                    name = src["name"]
                    # OSM `name:en` becomes our second-script alias; the matcher
                    # uses it for cross-script fuzzy matching (Item 14).
                    name_en = src.get("name_en")
                    if name_en and name_en != name:
                        name_local = name_en
                    # OSM `brand` tag — chain-store signal that powers chain
                    # queries through both the matcher (Commit 2) and the
                    # embedding text (Commit 3, Item 8).
                    brand = src.get("brand")
                    if brand and brand != name:
                        brand_name = brand
                    lon = src["lng"]
                    lat = src["lat"]
                    street = src.get("addr_street") or rnd.choice(STREET_NAMES)
                    house = src.get("addr_housenumber") or str(rnd.randint(1, 250))
                    formatted = (
                        f"{street} {house}, {src.get('city', 'Astana')}, Kazakhstan"
                        if street and house else f"{src.get('city', 'Astana')}, Kazakhstan"
                    )
                    phone = src.get("phone") or (random_phone(rnd) if rnd.random() < 0.5 else None)
                    website = src.get("website")
                # Status distribution roughly matches real-world: most places
                # open, a small tail of closures + unverified. The source
                # signals below are generated to be CONSISTENT with this so
                # the validation engine has a fair calibration target.
                roll = rnd.random()
                if roll < 0.85:
                    status = "open"
                elif roll < 0.92:
                    status = "unverified"
                elif roll < 0.97:
                    status = "temporarily_closed"
                else:
                    status = "permanently_closed"
                status_conf = round(rnd.uniform(0.75, 0.99), 3) if status == "open" else round(rnd.uniform(0.5, 0.9), 3)
                verified = now - timedelta(days=rnd.randint(0, 90))
                popularity = popularity_for(category, rnd)

                place_rows.append((
                    str(pid), name, name_local, brand_name, code_to_id[category],
                    status, status_conf, "seeded mock",
                    verified, lon, lat, website, phone,
                    "KZ", "Asia/Almaty", popularity,
                ))
                address_rows.append((
                    str(pid), formatted, street, house, "Astana", None, "010000", "KZ", lon, lat,
                ))

                am = random_amenities(rnd, category)
                if am:
                    amenity_rows.append((
                        str(pid), am["wifi"], am["parking"], am["outdoor_seating"],
                        am["wheelchair_accessible"], am["vegan_options"], am["pet_friendly"],
                        am["kids_friendly"], am["price_level"],
                    ))

                for d, o, c, is_ov in hours_for(category, rnd):
                    hours_rows.append((str(pid), d, o, c, is_ov))

                for ns, k, v in vibe_attributes(category, rnd):
                    # source_id appended after sources are inserted (see below).
                    attribute_rows.append((str(pid), ns, k, "boolean", None, None, True, None))

                for stype, sname, surl, last_fetched, rel, is_primary, signal in sources_for(rnd, status):
                    source_rows.append((str(pid), stype, sname, surl, last_fetched, rel, is_primary, signal))

            print(f"  inserting {len(place_rows)} places...")
            cur.executemany(
                """
                INSERT INTO places
                    (id, primary_name, name_local, brand_name, category_id, status, status_confidence,
                     status_reason, status_last_verified_at, location,
                     primary_website_url, phone_number, country_code, time_zone,
                     popularity_score)
                VALUES (%s, %s, %s, %s, %s, %s::place_status, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        %s, %s, %s, %s, %s)
                """,
                place_rows,
            )

            print(f"  inserting {len(address_rows)} addresses...")
            cur.executemany(
                """
                INSERT INTO place_addresses
                    (place_id, formatted_address, street, house_number, city, state,
                     postal_code, country_code, location)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                """,
                address_rows,
            )

            print(f"  inserting {len(amenity_rows)} amenity rows...")
            cur.executemany(
                """
                INSERT INTO place_amenities
                    (place_id, wifi, parking, outdoor_seating, wheelchair_accessible,
                     vegan_options, pet_friendly, kids_friendly, price_level)
                VALUES (%s, %s, %s::parking_type, %s, %s, %s, %s, %s, %s::price_level)
                """,
                amenity_rows,
            )

            print(f"  inserting {len(hours_rows)} hours rows...")
            cur.executemany(
                """
                INSERT INTO place_hours (place_id, day_of_week, open_time, close_time, is_overnight)
                VALUES (%s, %s, %s, %s, %s)
                """,
                hours_rows,
            )

            # Sources go in BEFORE attributes so we can attach a real source_id
            # to each attribute row (vibe attribute provenance).
            print(f"  inserting {len(source_rows)} source rows...")
            sources_by_place: dict[str, list[int]] = {}
            CHUNK = 1000
            for i in range(0, len(source_rows), CHUNK):
                chunk = source_rows[i:i + CHUNK]
                placeholders = ",".join(
                    ["(%s, %s::source_type, %s, %s, %s, %s, %s, %s)"] * len(chunk)
                )
                sql = (
                    "INSERT INTO place_sources "
                    "(place_id, source_type, source_name, source_url, last_fetched_at, "
                    "reliability_score, is_primary, status_signal) "
                    f"VALUES {placeholders} "
                    "RETURNING id, place_id::text"
                )
                flat = [v for row in chunk for v in row]
                cur.execute(sql, flat)
                for sid, pid_str in cur.fetchall():
                    sources_by_place.setdefault(pid_str, []).append(sid)

            print(f"  inserting {len(attribute_rows)} attribute rows...")
            attribute_rows_with_source = [
                row + (rnd.choice(sources_by_place[row[0]]) if sources_by_place.get(row[0]) else None,)
                for row in attribute_rows
            ]
            cur.executemany(
                """
                INSERT INTO place_attributes
                    (place_id, namespace, key, value_type, value_string, value_number,
                     value_boolean, value_json, source_id)
                VALUES (%s, %s, %s, %s::attribute_value_type, %s, %s, %s, %s, %s)
                """,
                attribute_rows_with_source,
            )

            cur.execute("ANALYZE places")
            cur.execute("ANALYZE place_addresses")
            cur.execute("SELECT COUNT(*) FROM places")
            total = cur.fetchone()[0]

    print()
    print(f"Seeded {total} places, {len(source_rows)} sources, "
          f"{len(amenity_rows)} amenity rows, {len(hours_rows)} hours rows, "
          f"{len(attribute_rows)} attributes.")
    print()
    print("API keys (save these — re-seeding rotates them):")
    for name, key in api_keys:
        print(f"  {name}: {key}")
    print()
    print("Next: OPENAI_API_KEY=... python -m scripts.build_embeddings")


if __name__ == "__main__":
    main()
