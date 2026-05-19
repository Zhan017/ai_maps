"""Hybrid place search: geo + structured filters, optional semantic ANN.

Always produces `reasons[]` and `freshness{}` to make agent integration easy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import DEFAULT_TZ
from app.services import embeddings

# Hybrid (semantic path) score = HYBRID_ANN_W·ANN + HYBRID_DISTANCE_W·dist_score
# + HYBRID_POPULARITY_W·popularity. Extracted as module-level constants so
# the eval harness can override them via monkeypatch without monkey-patching
# the function body.
HYBRID_ANN_W = 0.6
HYBRID_DISTANCE_W = 0.25
HYBRID_POPULARITY_W = 0.15

# Structured-only (no semantic query) score = STRUCTURED_DISTANCE_W·dist_score
# + STRUCTURED_POPULARITY_W·popularity.
STRUCTURED_DISTANCE_W = 0.7
STRUCTURED_POPULARITY_W = 0.3


@dataclass
class SearchQuery:
    q: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_m: int = 1000
    category: str | None = None
    country_code: str | None = None
    open_now: bool = False
    amenity_wifi: bool | None = None
    amenity_outdoor: bool | None = None
    limit: int = 20
    offset: int = 0


def _open_now_clause(tz_name: str = DEFAULT_TZ) -> tuple[str, tuple]:
    now = datetime.now(ZoneInfo(tz_name))
    return (
        """
        EXISTS (
            SELECT 1 FROM place_hours h
            WHERE h.place_id = p.id
              AND h.day_of_week = %s
              AND (
                  (NOT h.is_overnight AND h.open_time <= %s AND %s < h.close_time)
                  OR (h.is_overnight AND (%s >= h.open_time OR %s < h.close_time))
              )
        )
        """,
        (now.weekday(), now.time(), now.time(), now.time(), now.time()),
    )


def _build_where(query: SearchQuery) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if query.lat is not None and query.lng is not None:
        clauses.append("ST_DWithin(p.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)")
        params.extend([query.lng, query.lat, query.radius_m])

    if query.category:
        clauses.append("pc.code = %s")
        params.append(query.category)

    if query.country_code:
        clauses.append("p.country_code = %s")
        params.append(query.country_code)

    # always exclude permanently_closed by default
    clauses.append("p.status <> 'permanently_closed'")

    if query.open_now:
        sql, ps = _open_now_clause()
        clauses.append(sql)
        params.extend(ps)

    if query.amenity_wifi is True:
        clauses.append("EXISTS (SELECT 1 FROM place_amenities a WHERE a.place_id = p.id AND a.wifi)")
    if query.amenity_outdoor is True:
        clauses.append("EXISTS (SELECT 1 FROM place_amenities a WHERE a.place_id = p.id AND a.outdoor_seating)")

    return clauses, params


def _make_reasons(row: dict, query: SearchQuery) -> list[str]:
    reasons: list[str] = []
    if query.category and row.get("category_code") == query.category:
        reasons.append(f"matches category {query.category}")
    if row.get("wifi"):
        reasons.append("has wifi")
    if row.get("outdoor_seating"):
        reasons.append("outdoor seating")
    if row.get("pet_friendly"):
        reasons.append("pet friendly")
    for v in (row.get("vibe_keys") or [])[:3]:
        reasons.append(v.replace("_", " "))
    if query.open_now:
        reasons.append("open now")
    if row.get("distance_m") is not None and row["distance_m"] < 300:
        reasons.append(f"only {int(row['distance_m'])}m away")
    return reasons


def _row_to_result(row: dict, query: SearchQuery, score: float) -> dict:
    return {
        "place_id": row["id"],
        "primary_name": row["primary_name"],
        "formatted_address": row.get("formatted_address"),
        "location": {"lat": row["lat"], "lng": row["lng"]},
        "category": row.get("category_code"),
        "distance_m": round(row["distance_m"], 1) if row.get("distance_m") is not None else None,
        "status": row["status"],
        "score": round(score, 3),
        "popularity_score": float(row.get("popularity_score") or 0),
        "reasons": _make_reasons(row, query),
        "freshness": {
            "profile_last_updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "status_last_verified_at": row["status_last_verified_at"].isoformat() if row.get("status_last_verified_at") else None,
        },
    }


BASE_SELECT = """
    SELECT p.id::text AS id, p.primary_name, p.status::text, p.status_last_verified_at,
           p.updated_at, p.popularity_score,
           ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lng,
           pa.formatted_address, pc.code AS category_code,
           am.wifi, am.outdoor_seating, am.pet_friendly,
           {distance_expr} AS distance_m,
           (SELECT array_agg(key) FROM place_attributes
              WHERE place_id = p.id AND namespace IN ('vibe', 'audience')) AS vibe_keys
    FROM places p
    LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
    LEFT JOIN place_categories pc ON pc.id = p.category_id
    LEFT JOIN place_amenities am ON am.place_id = p.id
"""


def _structured_search(pool, query: SearchQuery) -> list[dict]:
    where_clauses, params = _build_where(query)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    if query.lat is not None and query.lng is not None:
        dist_expr = "ST_Distance(p.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)"
        select_params = [query.lng, query.lat]
        order_clause = " ORDER BY distance_m ASC, popularity_score DESC"
    else:
        dist_expr = "NULL::float8"
        select_params = []
        order_clause = " ORDER BY popularity_score DESC"

    sql = BASE_SELECT.format(distance_expr=dist_expr) + where_sql + order_clause + " LIMIT %s OFFSET %s"
    all_params = select_params + params + [query.limit, query.offset]

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, all_params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def _semantic_search(pool, openai_client, query: SearchQuery) -> list[dict]:
    """ANN over places_vectors → join structured filters, hybrid score.

    Semantic component is dominant (0.6); distance (0.25) + popularity (0.15)
    pull strong local results forward.
    """
    qvec = embeddings.embed_one(openai_client, query.q or "")
    qvec_lit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

    where_clauses, where_params = _build_where(query)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    if query.lat is not None and query.lng is not None:
        dist_expr = "ST_Distance(p.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)"
        dist_params = [query.lng, query.lat]
    else:
        dist_expr = "NULL::float8"
        dist_params = []

    sql = f"""
        SELECT p.id::text AS id, p.primary_name, p.status::text, p.status_last_verified_at,
               p.updated_at, p.popularity_score,
               ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lng,
               pa.formatted_address, pc.code AS category_code,
               am.wifi, am.outdoor_seating, am.pet_friendly,
               {dist_expr} AS distance_m,
               (SELECT array_agg(key) FROM place_attributes
                  WHERE place_id = p.id AND namespace IN ('vibe', 'audience')) AS vibe_keys,
               1 - (v.embedding <=> %s::vector) AS ann_score
        FROM places p
        JOIN places_vectors v ON v.place_id = p.id
        LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
        LEFT JOIN place_categories pc ON pc.id = p.category_id
        LEFT JOIN place_amenities am ON am.place_id = p.id
        {where_sql}
        ORDER BY v.embedding <=> %s::vector
        LIMIT 200
    """
    params = dist_params + [qvec_lit] + where_params + [qvec_lit]
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def _hybrid_score(row: dict, query: SearchQuery) -> float:
    ann = float(row.get("ann_score") or 0.0)
    if query.lat is not None and row.get("distance_m") is not None:
        d = row["distance_m"]
        d_score = max(0.0, 1.0 - d / max(query.radius_m, 1))
    else:
        d_score = 0.5
    pop = float(row.get("popularity_score") or 0.0)
    return HYBRID_ANN_W * ann + HYBRID_DISTANCE_W * d_score + HYBRID_POPULARITY_W * pop


def search(pool, openai_client, query: SearchQuery) -> dict:
    use_semantic = bool(query.q and query.q.strip() and openai_client)
    if use_semantic:
        rows = _semantic_search(pool, openai_client, query)
        scored = [(_hybrid_score(r, query), r) for r in rows]
        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[query.offset: query.offset + query.limit]
        results = [_row_to_result(r, query, s) for (s, r) in scored]
    else:
        rows = _structured_search(pool, query)
        # score = distance score + popularity, no ANN
        scored = []
        for r in rows:
            if query.lat is not None and r.get("distance_m") is not None:
                d_score = max(0.0, 1.0 - r["distance_m"] / max(query.radius_m, 1))
            else:
                d_score = 0.5
            s = STRUCTURED_DISTANCE_W * d_score + STRUCTURED_POPULARITY_W * float(r.get("popularity_score") or 0.0)
            scored.append((s, r))
        results = [_row_to_result(r, query, s) for (s, r) in scored]

    return {
        "results": results,
        "query": {
            "q": query.q, "lat": query.lat, "lng": query.lng,
            "radius_m": query.radius_m, "category": query.category,
            "open_now": query.open_now, "limit": query.limit,
            "semantic": use_semantic,
        },
        "count": len(results),
    }
