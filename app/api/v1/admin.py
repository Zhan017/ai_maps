"""Internal admin endpoints: per-place diagnostics + manual overrides."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_pool
from app.services import enrichment

router = APIRouter(tags=["admin"])


class OverrideRequest(BaseModel):
    status: str | None = None
    status_reason: str | None = None
    primary_name: str | None = None
    popularity_score: float | None = None


@router.get("/places/{place_id}/debug")
def place_debug(place_id: str, pool=Depends(get_pool)):
    profile = enrichment.full_profile(pool, place_id, include_sources=True, include_history=True)
    if not profile:
        raise HTTPException(404, "place not found")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT customer_id::text, customer_place_id, status::text, last_synced_at "
            "FROM customer_place_refs WHERE place_id = %s",
            (place_id,),
        )
        refs = [
            {"customer_id": r[0], "customer_place_id": r[1], "status": r[2],
             "last_synced_at": r[3].isoformat() if r[3] else None}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT id, kind, note, created_at FROM place_feedback WHERE place_id = %s ORDER BY created_at DESC LIMIT 50",
            (place_id,),
        )
        feedback = [
            {"id": r[0], "kind": r[1], "note": r[2], "created_at": r[3].isoformat()}
            for r in cur.fetchall()
        ]
    return {"profile": profile, "customer_refs": refs, "feedback": feedback}


@router.post("/places/{place_id}/override")
def place_override(place_id: str, req: OverrideRequest, pool=Depends(get_pool)):
    updates: list[str] = []
    params: list[Any] = []
    if req.status:
        updates.append("status = %s::place_status")
        params.append(req.status)
        updates.append("status_reason = %s")
        params.append(req.status_reason or "manual override")
        updates.append("status_last_verified_at = now()")
    if req.primary_name:
        updates.append("primary_name = %s")
        params.append(req.primary_name)
    if req.popularity_score is not None:
        updates.append("popularity_score = %s")
        params.append(req.popularity_score)
    if not updates:
        raise HTTPException(400, "no fields to update")
    updates.append("updated_at = now()")
    params.append(place_id)
    sql = f"UPDATE places SET {', '.join(updates)} WHERE id = %s RETURNING id::text"
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "place not found")
        if req.status:
            cur.execute(
                """
                INSERT INTO place_status_history (place_id, new_status, change_reason)
                VALUES (%s, %s::place_status, %s)
                """,
                (place_id, req.status, req.status_reason or "manual override"),
            )
    return {"place_id": row[0], "updated": True}
