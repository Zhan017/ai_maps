"""One-time seeding script. Run after `docker compose up -d`."""
import random
import psycopg

DSN = "host=localhost port=5434 dbname=gis user=zhan password=zhan"

LAT_MIN, LAT_MAX = 51.05, 51.25
LON_MIN, LON_MAX = 71.30, 71.60

CATEGORIES = {
    "amenity": ["restaurant", "cafe", "bar", "fast_food", "pharmacy", "bank", "atm"],
    "tourism": ["attraction", "museum", "hotel", "viewpoint", "guest_house"],
}

NAMES = ["Astana", "Nomad", "Steppe", "Silk", "Baiterek", "Khan", "Aldar",
         "Saryarka", "Esil", "Tselina", "Aral", "Tien Shan", "Altyn", "Dala"]
SUFFIXES = ["Cafe", "House", "Plaza", "Grill", "Lounge", "Inn", "Spot", "Place"]

random.seed(42)

with psycopg.connect(DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pois")
        cur.execute("""
            CREATE TABLE pois (
                id       BIGSERIAL PRIMARY KEY,
                name     TEXT,
                category TEXT,
                subtype  TEXT,
                geom     GEOGRAPHY(POINT, 4326)
            )
        """)

        rows = []
        for _ in range(50000):
            category = random.choice(list(CATEGORIES.keys()))
            subtype = random.choice(CATEGORIES[category])
            name = f"{random.choice(NAMES)} {random.choice(SUFFIXES)}"
            lon = random.uniform(LON_MIN, LON_MAX)
            lat = random.uniform(LAT_MIN, LAT_MAX)
            rows.append((name, category, subtype, lon, lat))

        cur.executemany(
            "INSERT INTO pois (name, category, subtype, geom) "
            "VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)",
            rows,
        )

        cur.execute("CREATE INDEX idx_pois_geom ON pois USING GIST (geom)")
        cur.execute("ANALYZE pois")

        cur.execute("SELECT COUNT(*) FROM pois")
        print(f"Loaded {cur.fetchone()[0]} POIs with GiST index.")
