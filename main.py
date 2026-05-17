import json
import logging
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

load_dotenv()

log = logging.getLogger("uvicorn")

DSN = "host=localhost port=5434 dbname=gis user=zhan password=zhan"

SUBTYPES = [
    "restaurant", "cafe", "bar", "fast_food",
    "pharmacy", "bank", "atm",
    "attraction", "museum", "hotel", "viewpoint", "guest_house",
]

SYSTEM_PROMPT = (
    "You are a concise assistant for an Astana POI map. Use the find_nearby_pois tool "
    "to answer location questions. The user's current map center is injected by the "
    "server, so you do not specify (and cannot specify) lat/lon. Pick a sensible "
    "radius_m (50-10000, default 500) and an optional subtype from the allowed enum.\n\n"
    "When tool results come back:\n"
    "  - If empty, say so plainly (\"no <subtype> within Xm\") — never invent places.\n"
    "  - Otherwise, list up to 5 by name, subtype, and distance in meters.\n\n"
    "For greetings or off-topic chat, reply without calling the tool."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_nearby_pois",
            "description": "Search POIs near the user's current map center.",
            "parameters": {
                "type": "object",
                "properties": {
                    "radius_m": {"type": "integer", "minimum": 50, "maximum": 10000},
                    "subtype": {"type": "string", "enum": SUBTYPES},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "required": ["radius_m"],
                "additionalProperties": False,
            },
        },
    }
]

MODEL = "gpt-4o-mini"
MAX_HISTORY = 40
MAX_LOOP_ITERATIONS = 4

SESSIONS: dict[str, list[dict]] = {}

pool: ConnectionPool | None = None
client: OpenAI | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool, client
    pool = ConnectionPool(DSN, min_size=2, max_size=10, open=True)
    if os.getenv("OPENAI_API_KEY"):
        client = OpenAI()
    else:
        log.warning("OPENAI_API_KEY not set — /api/chat will return 503")
    yield
    pool.close()


app = FastAPI(lifespan=lifespan, title="Astana POI Explorer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


NEARBY_SQL = """
    SELECT id, name, category, subtype,
           ST_Distance(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS meters,
           ST_AsGeoJSON(geom::geometry) AS geojson
    FROM pois
    WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
      AND (%s::text IS NULL OR subtype = %s)
    ORDER BY meters
    LIMIT %s
"""


def query_nearby(
    lat: float, lon: float, radius_m: int, subtype: str | None, limit: int
) -> tuple[list[dict], str, tuple]:
    params = (lon, lat, lon, lat, radius_m, subtype, subtype, limit)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(NEARBY_SQL, params)
        raw = cur.fetchall()
    rows = []
    for r in raw:
        geom = json.loads(r[5])
        rows.append({
            "id": r[0],
            "name": r[1],
            "category": r[2],
            "subtype": r[3],
            "distance_m": round(r[4], 1),
            "geometry": geom,
            "lat": geom["coordinates"][1],
            "lon": geom["coordinates"][0],
        })
    return rows, NEARBY_SQL.strip(), params


@app.get("/api/nearby")
def nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_m: int = Query(500, gt=0, le=10000),
    subtype: str | None = Query(None),
    limit: int = Query(100, gt=0, le=500),
):
    rows, _, _ = query_nearby(lat, lon, radius_m, subtype, limit)
    return {
        "count": len(rows),
        "query": {"lat": lat, "lon": lon, "radius_m": radius_m, "subtype": subtype},
        "results": [
            {k: r[k] for k in ("id", "name", "category", "subtype", "distance_m", "geometry")}
            for r in rows
        ],
    }


class MapCenter(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    map_center: MapCenter


def _trim_history(history: list[dict]) -> None:
    non_system = len(history) - 1
    if non_system > MAX_HISTORY:
        del history[1 : 1 + (non_system - MAX_HISTORY)]


@app.post("/api/chat")
def chat(req: ChatRequest):
    if client is None:
        raise HTTPException(503, "OPENAI_API_KEY not configured on server")

    history = SESSIONS.setdefault(
        req.session_id, [{"role": "system", "content": SYSTEM_PROMPT}]
    )
    history.append({"role": "user", "content": req.message})
    _trim_history(history)

    iterations: list[dict] = []
    places_by_id: dict[int, dict] = {}
    reply_text = ""
    total_tokens = 0
    turn_start = time.perf_counter()

    for _ in range(MAX_LOOP_ITERATIONS):
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.4,
        )
        llm_latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.usage:
            total_tokens += resp.usage.total_tokens

        msg = resp.choices[0].message

        if not msg.tool_calls:
            reply_text = msg.content or ""
            history.append({"role": "assistant", "content": reply_text})
            break

        history.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            iter_entry: dict = {
                "tool": tc.function.name,
                "llm_latency_ms": llm_latency_ms,
            }
            iter_start = time.perf_counter()

            try:
                raw_args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                tool_result = {"error": f"bad JSON args: {e}"}
                iter_entry["error"] = tool_result["error"]
                iterations.append(iter_entry)
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result),
                })
                continue

            if tc.function.name != "find_nearby_pois":
                tool_result = {"error": f"unknown tool: {tc.function.name}"}
                iter_entry["args"] = raw_args
                iter_entry["error"] = tool_result["error"]
                iterations.append(iter_entry)
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result),
                })
                continue

            radius_m = int(raw_args.get("radius_m", 500))
            subtype = raw_args.get("subtype")
            limit = int(raw_args.get("limit", 5))

            try:
                rows, sql, params = query_nearby(
                    req.map_center.lat, req.map_center.lon,
                    radius_m, subtype, limit,
                )
            except Exception as e:
                tool_result = {"error": str(e)}
                iter_entry["args"] = {
                    "radius_m": radius_m, "subtype": subtype, "limit": limit,
                    "lat": req.map_center.lat, "lon": req.map_center.lon,
                }
                iter_entry["error"] = str(e)
                iterations.append(iter_entry)
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result),
                })
                continue

            slim = [
                {
                    "id": r["id"], "name": r["name"], "subtype": r["subtype"],
                    "distance_m": r["distance_m"], "lat": r["lat"], "lon": r["lon"],
                }
                for r in rows
            ]
            for r in rows:
                places_by_id[r["id"]] = r

            tool_result = {"places": slim}
            iter_entry.update({
                "args": {
                    "radius_m": radius_m, "subtype": subtype, "limit": limit,
                    "lat": req.map_center.lat, "lon": req.map_center.lon,
                },
                "sql": sql,
                "params": list(params),
                "rows": len(rows),
                "tool_latency_ms": int((time.perf_counter() - iter_start) * 1000),
            })
            iterations.append(iter_entry)
            history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result),
            })

    _trim_history(history)

    places = [
        {k: p[k] for k in ("id", "name", "category", "subtype", "distance_m", "geometry")}
        for p in places_by_id.values()
    ]

    return {
        "reply": reply_text,
        "places": places,
        "debug": {
            "iterations": iterations,
            "total_tokens": total_tokens,
            "total_latency_ms": int((time.perf_counter() - turn_start) * 1000),
        },
    }


@app.delete("/api/chat/{session_id}")
def clear_chat(session_id: str):
    SESSIONS.pop(session_id, None)
    return {"cleared": True}


@app.get("/api/health")
def health():
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="ui")
