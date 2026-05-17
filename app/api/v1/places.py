"""V1 places endpoints: validate-enrich, GET, batch status, feedback."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_pool
from app.core.config import MATCH_HIGH_THRESHOLD, MATCH_LOW_THRESHOLD
from app.core.security import require_api_key
from app.services import enrichment, matching, validation

router = APIRouter(tags=["places"])


# ---------- request models ----------

class PlaceInput(BaseModel):
    customer_place_id: str | None = None
    name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    category: str | None = None
    website: str | None = None
    phone: str | None = None
    country_code: str | None = None


class ValidateEnrichOptions(BaseModel):
    enrich: bool = True
    validate_status: bool = True
    return_full_profile: bool = False


class ValidateEnrichRequest(BaseModel):
    places: list[PlaceInput]
    options: ValidateEnrichOptions = Field(default_factory=ValidateEnrichOptions)


class BatchStatusRequest(BaseModel):
    place_ids: list[str]


class FeedbackRequest(BaseModel):
    kind: str = Field(..., description="closed | wrong_address | wrong_name | other")
    note: str | None = None
    payload: dict[str, Any] | None = None


# ---------- helpers ----------

def _create_unverified(pool, p: PlaceInput, category_id: int | None) -> str:
    place_id = str(uuid.uuid4())
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (id, primary_name, category_id, status, status_confidence,
                                status_reason, location, country_code)
            VALUES (%s, %s, %s, 'unverified', 0.5, 'no canonical match',
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
            """,
            (place_id, p.name, category_id,
             p.lng if p.lng is not None else 71.43,
             p.lat if p.lat is not None else 51.13,
             p.country_code or "KZ"),
        )
        if p.address:
            cur.execute(
                """
                INSERT INTO place_addresses (place_id, formatted_address, country_code, is_primary)
                VALUES (%s, %s, %s, TRUE)
                """,
                (place_id, p.address, p.country_code or "KZ"),
            )
    return place_id


def _category_id_for(pool, code: str | None) -> int | None:
    if not code:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM place_categories WHERE code = %s", (code,))
        row = cur.fetchone()
    return row[0] if row else None


def _record_customer_ref(pool, customer_id: str, customer_place_id: str | None,
                         place_id: str | None, status: str) -> None:
    if not customer_place_id:
        return
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_place_refs (customer_id, customer_place_id, place_id, status, last_synced_at)
            VALUES (%s, %s, %s, %s::customer_ref_status, now())
            ON CONFLICT (customer_id, customer_place_id)
            DO UPDATE SET place_id = EXCLUDED.place_id,
                          status = EXCLUDED.status,
                          last_synced_at = now()
            """,
            (customer_id, customer_place_id, place_id, status),
        )


# ---------- endpoints ----------

@router.post("/places:validate-enrich")
def validate_enrich(
    req: ValidateEnrichRequest,
    pool=Depends(get_pool),
    customer: dict = Depends(require_api_key),
):
    out_results = []
    for p in req.places:
        m = matching.match(pool, matching.MatchInput(
            name=p.name, address=p.address, lat=p.lat, lng=p.lng,
            category=p.category, website=p.website, phone=p.phone,
        ))

        if m.decision == "no_match":
            cat_id = _category_id_for(pool, p.category)
            place_id = _create_unverified(pool, p, cat_id)
            status = "unverified"
            status_conf = 0.5
            status_reason = "newly created — no match in canonical store"
            match_conf = 0.0
            ref_status = "unmatched"
        else:
            place_id = m.place_id
            match_conf = m.confidence
            ref_status = "matched" if m.decision == "match" else "low_confidence"
            if req.options.validate_status:
                verdict = validation.classify(pool, place_id)
                status = verdict.status
                status_conf = verdict.confidence
                status_reason = verdict.reason
            else:
                with pool.connection() as conn, conn.cursor() as cur:
                    cur.execute("SELECT status::text, status_confidence, status_reason FROM places WHERE id = %s", (place_id,))
                    row = cur.fetchone()
                status, status_conf, status_reason = row[0], float(row[1]), row[2]

        _record_customer_ref(pool, customer["id"], p.customer_place_id, place_id, ref_status)

        item: dict[str, Any] = {
            "customer_place_id": p.customer_place_id,
            "canonical_place_id": place_id,
            "match_confidence": match_conf,
            "match_decision": m.decision,
            "match_breakdown": (m.candidate or {}).get("match_breakdown") if m.candidate else None,
            "status": status,
            "status_confidence": status_conf,
            "status_reason": status_reason,
        }

        if req.options.enrich:
            if req.options.return_full_profile:
                item["profile"] = enrichment.full_profile(pool, place_id)
            else:
                item["attributes"] = enrichment.attributes_summary(pool, place_id)

        out_results.append(item)

    return {"results": out_results}


@router.get("/places/{place_id}")
def get_place(
    place_id: str,
    include_sources: bool = Query(False),
    include_history: bool = Query(False),
    pool=Depends(get_pool),
):
    profile = enrichment.full_profile(
        pool, place_id,
        include_sources=include_sources, include_history=include_history,
    )
    if not profile:
        raise HTTPException(404, "place not found")
    return profile


@router.post("/places:status")
def batch_status(req: BatchStatusRequest, pool=Depends(get_pool)):
    if not req.place_ids:
        return {"results": []}
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, status::text, status_confidence, status_reason,
                   status_last_verified_at, updated_at
            FROM places WHERE id = ANY(%s::uuid[])
            """,
            (req.place_ids,),
        )
        rows = cur.fetchall()
    return {
        "results": [
            {
                "place_id": r[0],
                "status": r[1],
                "status_confidence": float(r[2]),
                "status_reason": r[3],
                "freshness": {
                    "status_last_verified_at": r[4].isoformat() if r[4] else None,
                    "profile_last_updated_at": r[5].isoformat() if r[5] else None,
                },
            }
            for r in rows
        ]
    }


@router.post("/places/{place_id}:feedback")
def submit_feedback(
    place_id: str,
    req: FeedbackRequest,
    pool=Depends(get_pool),
    customer: dict = Depends(require_api_key),
):
    import json
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM places WHERE id = %s", (place_id,))
        if not cur.fetchone():
            raise HTTPException(404, "place not found")
        cur.execute(
            """
            INSERT INTO place_feedback (place_id, customer_id, kind, note, payload)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (place_id, customer["id"], req.kind, req.note,
             json.dumps(req.payload) if req.payload else None),
        )
        fid = cur.fetchone()[0]
    return {"feedback_id": fid, "accepted": True}
