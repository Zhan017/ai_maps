"""Legacy endpoints used by the static UI.

`/api/nearby` and `/api/chat` are kept for the existing Leaflet UI. Both are
now thin adapters over `services/search.py` and unauthenticated (these power
the public demo). The chat tool exposes the richer search surface so the LLM
can use category, open_now, wifi, etc.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_openai, get_pool
from app.core.config import CHAT_MODEL
from app.services import enrichment, search as search_svc

log = logging.getLogger("uvicorn")
router = APIRouter(tags=["legacy"])

LEAF_CATEGORIES = [
    "restaurant", "cafe", "bar", "fast_food",
    "pharmacy", "bank", "atm",
    "attraction", "museum", "hotel", "viewpoint", "guest_house",
]

SYSTEM_PROMPT = (
    "You are a concise place-intelligence assistant for an Astana map. Use the "
    "search_places tool to answer location and discovery questions. The user's "
    "current map center is injected as lat/lng so you do not specify them. "
    "Pick a sensible radius_m (50-10000, default 1000), an optional category, "
    "and use open_now/wifi when the question implies them. For free-text or "
    "vibe queries ('quiet coffee with wifi'), pass them as `q` — semantic "
    "search will pick the right ranking.\n\n"
    "When tool results come back:\n"
    "  - If empty, say so plainly — never invent places.\n"
    "  - Otherwise, list up to 5 by name, category, distance, and the top "
    "    reason from `reasons[]`. Mention `freshness.status_last_verified_at` "
    "    if asked about how current the data is.\n\n"
    "For greetings or off-topic chat, reply without calling the tool."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "Hybrid place search over the canonical store. Pass q for vibe/semantic queries; pass category/open_now/wifi/outdoor for structured filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "radius_m": {"type": "integer", "minimum": 50, "maximum": 10000},
                    "category": {"type": "string", "enum": LEAF_CATEGORIES},
                    "open_now": {"type": "boolean"},
                    "wifi": {"type": "boolean"},
                    "outdoor": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "additionalProperties": False,
            },
        },
    }
]

MAX_HISTORY = 40
MAX_LOOP_ITERATIONS = 4

# Single-worker assumption: cross-worker session continuity would require Redis.
# The lock here protects the dict itself against concurrent registry mutations
# (setdefault / pop). Critical sections do in-memory dict ops only — no awaits,
# no I/O. Concurrent requests with the same session_id will still interleave
# their list mutations; that's an existing demo limitation, not a regression.
_SESSIONS_LOCK = threading.Lock()
SESSIONS: dict[str, list[dict]] = {}


class MapCenter(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class ChatFilters(BaseModel):
    radius_m: int | None = None
    category: str | None = None
    open_now: bool | None = None
    wifi: bool | None = None
    outdoor: bool | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str
    map_center: MapCenter
    filters: ChatFilters | None = None


def _trim_history(history: list[dict]) -> None:
    non_system = len(history) - 1
    if non_system > MAX_HISTORY:
        del history[1 : 1 + (non_system - MAX_HISTORY)]


def _result_to_marker(r: dict) -> dict:
    """Shape a search result the way the Leaflet UI expects."""
    return {
        "id": r["place_id"],
        "name": r["primary_name"],
        "category": r.get("category"),
        "subtype": r.get("category"),  # legacy alias
        "distance_m": r.get("distance_m"),
        "status": r.get("status"),
        "reasons": r.get("reasons", []),
        "freshness": r.get("freshness", {}),
        "geometry": {
            "type": "Point",
            "coordinates": [r["location"]["lng"], r["location"]["lat"]],
        },
        "lat": r["location"]["lat"],
        "lon": r["location"]["lng"],
    }


@router.get("/api/stats")
def stats(pool=Depends(get_pool)):
    """Aggregate counts for the demo stats strip.

    Not cached: the underlying query runs in ~4ms warm on the current corpus
    (EXPLAIN ANALYZE confirmed). Caching it added stale-stats UX after reseed
    without buying meaningful latency back.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                (SELECT count(*) FROM places),
                (SELECT count(*) FROM places WHERE status = 'open'),
                (SELECT count(*) FROM place_sources),
                (SELECT count(*) FROM places_vectors),
                (SELECT count(*) FROM place_hours),
                (SELECT count(*) FROM place_attributes),
                (SELECT count(*) FROM place_categories WHERE parent_id IS NOT NULL)
        """)
        row = cur.fetchone()
    return {
        "places": row[0], "open": row[1], "sources": row[2],
        "vectors": row[3], "hours": row[4], "attributes": row[5],
        "categories": row[6],
    }


@router.get("/api/place/{place_id}")
def public_place(place_id: str, pool=Depends(get_pool)):
    """Unauthenticated read of a canonical place profile.

    The same data as /v1/places/{id}?include_sources=true&include_history=true
    but without auth — used by the demo UI's profile drawer.
    """
    profile = enrichment.full_profile(
        pool, place_id, include_sources=True, include_history=True,
    )
    if not profile:
        raise HTTPException(404, "place not found")
    return profile


@router.get("/api/nearby")
def nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_m: int = Query(1000, gt=0, le=20000),
    category: str | None = Query(None),
    open_now: bool = Query(False),
    wifi: bool | None = Query(None),
    outdoor: bool | None = Query(None),
    limit: int = Query(200, gt=0, le=500),
    pool=Depends(get_pool),
):
    out = search_svc.search(pool, None, search_svc.SearchQuery(
        lat=lat, lng=lon, radius_m=radius_m,
        category=category, open_now=open_now,
        amenity_wifi=wifi, amenity_outdoor=outdoor, limit=limit,
    ))
    return {
        "count": out["count"],
        "query": {"lat": lat, "lon": lon, "radius_m": radius_m, "category": category},
        "results": [_result_to_marker(r) for r in out["results"]],
    }


@router.post("/api/chat")
def chat(
    req: ChatRequest,
    pool=Depends(get_pool),
    openai_client=Depends(get_openai),
):
    if openai_client is None:
        raise HTTPException(503, "OPENAI_API_KEY not configured on server")

    with _SESSIONS_LOCK:
        history = SESSIONS.setdefault(
            req.session_id, [{"role": "system", "content": SYSTEM_PROMPT}]
        )
    user_content = req.message
    if req.filters:
        active = {k: v for k, v in req.filters.model_dump().items() if v not in (None, False, "")}
        if active:
            hint = ", ".join(f"{k}={v}" for k, v in active.items())
            user_content = f"{req.message}\n\n[user has active filters: {hint}; prefer these in your tool call unless the message overrides them]"
    history.append({"role": "user", "content": user_content})
    _trim_history(history)

    iterations: list[dict] = []
    places_by_id: dict[str, dict] = {}
    reply_text = ""
    total_tokens = 0
    turn_start = time.perf_counter()

    for _ in range(MAX_LOOP_ITERATIONS):
        t0 = time.perf_counter()
        resp = openai_client.chat.completions.create(
            model=CHAT_MODEL,
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
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            iter_entry: dict = {"tool": tc.function.name, "llm_latency_ms": llm_latency_ms}
            iter_start = time.perf_counter()

            try:
                raw_args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                tool_result = {"error": f"bad JSON args: {e}"}
                iter_entry["error"] = tool_result["error"]
                iterations.append(iter_entry)
                history.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(tool_result)})
                continue

            if tc.function.name != "search_places":
                tool_result = {"error": f"unknown tool: {tc.function.name}"}
                iter_entry["args"] = raw_args
                iter_entry["error"] = tool_result["error"]
                iterations.append(iter_entry)
                history.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(tool_result)})
                continue

            args = {
                "q": raw_args.get("q"),
                "lat": req.map_center.lat, "lng": req.map_center.lon,
                "radius_m": int(raw_args.get("radius_m", 1000)),
                "category": raw_args.get("category"),
                "open_now": bool(raw_args.get("open_now", False)),
                "amenity_wifi": raw_args.get("wifi"),
                "amenity_outdoor": raw_args.get("outdoor"),
                "limit": int(raw_args.get("limit", 8)),
            }

            try:
                out = search_svc.search(pool, openai_client, search_svc.SearchQuery(**args))
            except Exception as e:
                log.exception("search_places failed")
                tool_result = {"error": str(e)}
                iter_entry["args"] = args
                iter_entry["error"] = str(e)
                iterations.append(iter_entry)
                history.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(tool_result)})
                continue

            results = out["results"]
            slim = [
                {
                    "id": r["place_id"], "name": r["primary_name"],
                    "category": r.get("category"),
                    "distance_m": r.get("distance_m"),
                    "status": r.get("status"),
                    "reasons": r.get("reasons", [])[:3],
                    "lat": r["location"]["lat"], "lng": r["location"]["lng"],
                }
                for r in results
            ]
            for r in results:
                places_by_id[r["place_id"]] = r

            tool_result = {"places": slim, "semantic": out["query"]["semantic"]}
            iter_entry.update({
                "args": args,
                "rows": len(results),
                "tool_latency_ms": int((time.perf_counter() - iter_start) * 1000),
                "semantic": out["query"]["semantic"],
            })
            iterations.append(iter_entry)
            history.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(tool_result)})

    _trim_history(history)

    places = [_result_to_marker(p) for p in places_by_id.values()]

    return {
        "reply": reply_text,
        "places": places,
        "debug": {
            "iterations": iterations,
            "total_tokens": total_tokens,
            "total_latency_ms": int((time.perf_counter() - turn_start) * 1000),
        },
    }


@router.delete("/api/chat/{session_id}")
def clear_chat(session_id: str):
    with _SESSIONS_LOCK:
        SESSIONS.pop(session_id, None)
    return {"cleared": True}
