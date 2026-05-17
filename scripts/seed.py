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


def sources_for(rnd: random.Random) -> list[tuple]:
    n = rnd.randint(1, 3)
    chosen = rnd.sample(SOURCE_CATALOG, k=n)
    rows = []
    now = datetime.now(timezone.utc)
    for i, (stype, name, url, rel) in enumerate(chosen):
        days_ago = rnd.randint(0, 60)
        last_fetched = now - timedelta(days=days_ago)
        rows.append((stype, name, url, last_fetched, rel, i == 0, "active"))
    return rows


def main():
    schema_path = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"
    schema_sql = schema_path.read_text()

    rnd = random.Random(42)

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

            BATCH = 50000
            now = datetime.now(timezone.utc)
            for _ in range(BATCH):
                pid = uuid.uuid4()
                category = rnd.choice(leaf_codes)
                name = f"{rnd.choice(NAMES)} {rnd.choice(SUFFIXES)}"
                lon = rnd.uniform(LON_MIN, LON_MAX)
                lat = rnd.uniform(LAT_MIN, LAT_MAX)
                street, house, formatted = random_address(rnd)
                phone = random_phone(rnd) if rnd.random() < 0.7 else None
                website = f"https://{name.lower().replace(' ', '')}.kz" if rnd.random() < 0.4 else None
                status = "open" if rnd.random() < 0.92 else rnd.choice(["temporarily_closed", "permanently_closed", "unverified"])
                status_conf = round(rnd.uniform(0.75, 0.99), 3) if status == "open" else round(rnd.uniform(0.5, 0.9), 3)
                verified = now - timedelta(days=rnd.randint(0, 90))
                popularity = round(rnd.random(), 3)

                place_rows.append((
                    str(pid), name, code_to_id[category],
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
                    attribute_rows.append((str(pid), ns, k, "boolean", None, None, True, None))

                for stype, sname, surl, last_fetched, rel, is_primary, signal in sources_for(rnd):
                    source_rows.append((str(pid), stype, sname, surl, last_fetched, rel, is_primary, signal))

            print(f"  inserting {len(place_rows)} places...")
            cur.executemany(
                """
                INSERT INTO places
                    (id, primary_name, category_id, status, status_confidence,
                     status_reason, status_last_verified_at, location,
                     primary_website_url, phone_number, country_code, time_zone,
                     popularity_score)
                VALUES (%s, %s, %s, %s::place_status, %s, %s, %s,
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

            print(f"  inserting {len(attribute_rows)} attribute rows...")
            cur.executemany(
                """
                INSERT INTO place_attributes
                    (place_id, namespace, key, value_type, value_string, value_number, value_boolean, value_json)
                VALUES (%s, %s, %s, %s::attribute_value_type, %s, %s, %s, %s)
                """,
                attribute_rows,
            )

            print(f"  inserting {len(source_rows)} source rows...")
            cur.executemany(
                """
                INSERT INTO place_sources
                    (place_id, source_type, source_name, source_url, last_fetched_at,
                     reliability_score, is_primary, status_signal)
                VALUES (%s, %s::source_type, %s, %s, %s, %s, %s, %s)
                """,
                source_rows,
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
