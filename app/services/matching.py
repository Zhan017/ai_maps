"""Match a customer input to a canonical place.

Pipeline: PostGIS radius candidate generation, then weighted scoring across
name (rapidfuzz token_set_ratio), address tokens, phone equality, website
equality, distance, and category. Returns (best_match | None, confidence,
candidates) so callers can branch on HIGH/LOW thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from app.core.config import MATCH_HIGH_THRESHOLD, MATCH_LOW_THRESHOLD
from app.db.queries import NEARBY_CANDIDATES_SQL
from app.utils.geo import distance_score
from app.utils.text import normalize_name, normalize_phone, normalize_website, tokenize

WEIGHTS = {
    "name": 0.45,
    "address": 0.15,
    "phone": 0.10,
    "website": 0.10,
    "distance": 0.15,
    "category": 0.05,
}

CANDIDATE_RADIUS_M = 500
CANDIDATE_LIMIT = 25


@dataclass
class MatchInput:
    name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    category: str | None = None
    website: str | None = None
    phone: str | None = None


@dataclass
class MatchResult:
    place_id: str | None
    confidence: float
    decision: str  # "match" | "low_confidence" | "no_match"
    candidate: dict | None
    candidates: list[dict]


def _name_score(input_name: str, cand_name: str, cand_brand: str | None) -> float:
    a = normalize_name(input_name)
    b = normalize_name(cand_name)
    s = fuzz.token_set_ratio(a, b) / 100.0
    if cand_brand:
        s = max(s, fuzz.token_set_ratio(a, normalize_name(cand_brand)) / 100.0)
    return s


def _address_score(input_addr: str | None, cand_addr: str | None) -> float:
    if not input_addr or not cand_addr:
        return 0.0
    ai = set(tokenize(input_addr))
    ac = set(tokenize(cand_addr))
    if not ai or not ac:
        return 0.0
    inter = len(ai & ac)
    return inter / max(len(ai), 1)


def _equality_score(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a == b else 0.0


def _candidate_distance(cand: dict, lat: float | None, lng: float | None) -> float | None:
    # PostGIS already gives us `meters` if input had coords; else None
    return cand.get("meters")


def score(inp: MatchInput, cand: dict) -> tuple[float, dict]:
    """Weighted score normalized over features the input actually provides.

    Missing input fields don't penalize — they're dropped from the denominator
    so a name+coords-only input can still hit 1.0.
    """
    contribs: dict[str, tuple[float, float]] = {}  # feature -> (score, weight)

    contribs["name"] = (
        _name_score(inp.name, cand["primary_name"], cand.get("brand_name")),
        WEIGHTS["name"],
    )

    if inp.address:
        contribs["address"] = (_address_score(inp.address, cand.get("formatted_address")), WEIGHTS["address"])
    if inp.phone:
        contribs["phone"] = (
            _equality_score(normalize_phone(inp.phone), normalize_phone(cand.get("phone_number"))),
            WEIGHTS["phone"],
        )
    if inp.website:
        contribs["website"] = (
            _equality_score(normalize_website(inp.website), normalize_website(cand.get("primary_website_url"))),
            WEIGHTS["website"],
        )
    if cand.get("meters") is not None:
        contribs["distance"] = (distance_score(cand["meters"]), WEIGHTS["distance"])
    if inp.category:
        cat_s = 1.0 if cand.get("category_code") == inp.category else 0.0
        contribs["category"] = (cat_s, WEIGHTS["category"])

    total_w = sum(w for _, w in contribs.values())
    total = sum(s * w for s, w in contribs.values()) / total_w if total_w else 0.0

    return round(total, 3), {k: round(s, 3) for k, (s, _) in contribs.items()}


def candidates(pool, inp: MatchInput, radius_m: int = CANDIDATE_RADIUS_M) -> list[dict]:
    if inp.lat is None or inp.lng is None:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id::text, p.primary_name, p.brand_name, p.phone_number, p.primary_website_url,
                       pa.formatted_address, pa.street, pa.house_number, pa.city,
                       NULL::float8 AS meters, p.category_id, pc.code AS category_code
                FROM places p
                LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
                LEFT JOIN place_categories pc ON pc.id = p.category_id
                WHERE lower(p.primary_name) LIKE %s
                LIMIT %s
                """,
                (f"%{inp.name.lower()}%", CANDIDATE_LIMIT),
            )
            rows = cur.fetchall()
    else:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                NEARBY_CANDIDATES_SQL,
                (inp.lng, inp.lat, inp.lng, inp.lat, radius_m, CANDIDATE_LIMIT),
            )
            rows = cur.fetchall()
    cols = [
        "id", "primary_name", "brand_name", "phone_number", "primary_website_url",
        "formatted_address", "street", "house_number", "city",
        "meters", "category_id", "category_code",
    ]
    return [dict(zip(cols, r)) for r in rows]


def match(pool, inp: MatchInput) -> MatchResult:
    cands = candidates(pool, inp)
    if not cands:
        return MatchResult(None, 0.0, "no_match", None, [])

    scored: list[tuple[float, dict, dict]] = []
    for c in cands:
        s, breakdown = score(inp, c)
        scored.append((s, c, breakdown))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_cand, best_breakdown = scored[0]

    if best_score >= MATCH_HIGH_THRESHOLD:
        decision = "match"
    elif best_score >= MATCH_LOW_THRESHOLD:
        decision = "low_confidence"
    else:
        decision = "no_match"

    enriched_best = {**best_cand, "match_breakdown": best_breakdown, "match_score": best_score}
    out_candidates = [
        {**c, "match_score": s, "match_breakdown": b}
        for (s, c, b) in scored[:5]
    ]
    return MatchResult(
        place_id=best_cand["id"] if decision != "no_match" else None,
        confidence=best_score,
        decision=decision,
        candidate=enriched_best if decision != "no_match" else None,
        candidates=out_candidates,
    )
