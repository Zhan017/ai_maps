import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool

DSN = "host=localhost port=5434 dbname=gis user=zhan password=zhan"

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ConnectionPool(DSN, min_size=2, max_size=10, open=True)
    yield
    pool.close()


app = FastAPI(lifespan=lifespan, title="Astana POI Explorer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/nearby")
def nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_m: int = Query(500, gt=0, le=10000),
    subtype: str | None = Query(None),
    limit: int = Query(100, gt=0, le=500),
):
    sql = """
        SELECT id, name, category, subtype,
               ST_Distance(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS meters,
               ST_AsGeoJSON(geom::geometry) AS geojson
        FROM pois
        WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
          AND (%s::text IS NULL OR subtype = %s)
        ORDER BY meters
        LIMIT %s
    """
    params = (lon, lat, lon, lat, radius_m, subtype, subtype, limit)

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return {
        "count": len(rows),
        "query": {"lat": lat, "lon": lon, "radius_m": radius_m, "subtype": subtype},
        "results": [
            {
                "id": r[0],
                "name": r[1],
                "category": r[2],
                "subtype": r[3],
                "distance_m": round(r[4], 1),
                "geometry": json.loads(r[5]),
            }
            for r in rows
        ],
    }


@app.get("/api/health")
def health():
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok"}


# Static UI — mounted last so /api/* takes precedence
app.mount("/", StaticFiles(directory="static", html=True), name="ui")
