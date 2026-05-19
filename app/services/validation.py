"""Rules-based status classifier over place_sources.

## Confidence formula

For places with sources, we score a verdict's confidence as a weighted sum of
three normalized components:

    status_confidence
      = W_AGREEMENT      * source_agreement
      + W_RELIABILITY    * weighted_reliability
      + W_FRESHNESS      * freshness_factor

where:

- **source_agreement** = fraction of this place's sources whose `status_signal`
  matches the candidate verdict. A place with 3/3 sources signaling "active"
  scores 1.0; 2/3 active scores 0.67.

- **weighted_reliability** = mean `reliability_score` across the agreeing
  sources. Reliability is configured in `scripts/seed.py:SOURCE_CATALOG`
  (official_site 0.95, 2gis_kz 0.90, …).

- **freshness_factor** = `exp(-min_days_since_fetch / DECAY_DAYS)`. The
  exponential decay means a freshly-fetched signal scores near 1.0; a 30-day
  old signal scores 0.37; a 90-day-old signal scores 0.05. We use the
  *most recently fetched* among the agreeing sources, because what matters
  is whether *any* trusted source has seen this place lately.

`status_confidence` is bounded in `[0, 1]` because each component is in
`[0, 1]` and the weights sum to 1.

## Decision rules

1. **No sources** → ("unverified", 0.5, "no sources").

2. **Authoritative closure** — any source with `reliability_score >= CLOSED_RELIABILITY_FLOOR`
   carrying a "closed" / "permanently_closed" signal short-circuits to
   ("permanently_closed", `0.5 + reliability·0.5`, …). We require reliability
   >= 0.7 to fire — without that guard a single low-rel social-media post
   would close a real place. Phase-3 spec calls this out explicitly.

3. **Otherwise** — candidate verdict is "open"; confidence comes from the
   formula above. If `confidence < OPEN_FLOOR` (0.5) we demote to "unverified".

Writes `place_status_history` when the status changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

# Formula weights — exported for tests / docs / ablation.
W_AGREEMENT = 0.4
W_RELIABILITY = 0.4
W_FRESHNESS = 0.2

DECAY_DAYS = 30.0                # freshness half-life-ish
CLOSED_RELIABILITY_FLOOR = 0.7   # any source ≥ this with "closed" pins us to closed
OPEN_FLOOR = 0.5                 # below this we say "unverified" instead of "open"


@dataclass
class StatusVerdict:
    status: str
    confidence: float
    reason: str
    sources_considered: int
    triggering_source_id: int | None = None


def _norm_signal(s: str | None) -> str:
    return (s or "").lower().strip()


def _is_closed_signal(sig: str) -> bool:
    return sig in {"closed", "permanently_closed"}


def _score_verdict(sources: list[dict], now: datetime | None = None) -> StatusVerdict:
    """Pure function — given the source rows for a place, return the verdict.

    Each source must have keys: source_name, last_fetched_at, reliability_score,
    status_signal. Time is injected so tests can pin freshness to a known clock.
    """
    if not sources:
        return StatusVerdict("unverified", 0.5, "no sources", 0)

    now = now or datetime.now(timezone.utc)

    # (2) Authoritative closure short-circuit.
    authoritative_closed = [
        s for s in sources
        if _is_closed_signal(_norm_signal(s["status_signal"]))
        and s["reliability_score"] >= CLOSED_RELIABILITY_FLOOR
    ]
    if authoritative_closed:
        best = max(authoritative_closed, key=lambda s: s["reliability_score"])
        conf = round(min(0.99, 0.5 + best["reliability_score"] * 0.5), 3)
        return StatusVerdict(
            "permanently_closed", conf,
            f"closed signal from {best['source_name']} (rel {best['reliability_score']:.2f})",
            len(sources),
            triggering_source_id=best.get("id"),
        )

    # (3) Score an "open" candidate verdict via the formula.
    agreeing = [s for s in sources if not _is_closed_signal(_norm_signal(s["status_signal"]))]
    source_agreement = len(agreeing) / len(sources)

    if agreeing:
        weighted_reliability = sum(s["reliability_score"] for s in agreeing) / len(agreeing)
        ages_days = [(now - s["last_fetched_at"]).days for s in agreeing if s["last_fetched_at"]]
        min_age = min(ages_days) if ages_days else 999
        freshness_factor = math.exp(-max(0, min_age) / DECAY_DAYS)
    else:
        weighted_reliability = 0.0
        freshness_factor = 0.0

    confidence = round(
        W_AGREEMENT * source_agreement
        + W_RELIABILITY * weighted_reliability
        + W_FRESHNESS * freshness_factor,
        3,
    )

    candidate = "open" if confidence >= OPEN_FLOOR else "unverified"

    # Reason: prefer the most-recent agreeing source.
    triggering_source_id: int | None = None
    if agreeing:
        agreeing_with_ts = [s for s in agreeing if s["last_fetched_at"]]
        if agreeing_with_ts:
            top = max(agreeing_with_ts, key=lambda s: s["last_fetched_at"])
            reason = (
                f"active on {top['source_name']} as of "
                f"{top['last_fetched_at'].date().isoformat()}"
            )
        else:
            top = max(agreeing, key=lambda s: s["reliability_score"])
            reason = f"active on {top['source_name']} (no timestamp)"
        triggering_source_id = top.get("id")
    else:
        reason = "all sources dissent"

    if candidate == "unverified":
        reason = f"low confidence ({confidence:.2f}); " + reason

    return StatusVerdict(candidate, confidence, reason, len(sources),
                         triggering_source_id=triggering_source_id)


def _fetch_sources(conn, place_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_type::text, source_name, last_fetched_at,
                   reliability_score, is_primary, status_signal
            FROM place_sources WHERE place_id = %s
            """,
            (place_id,),
        )
        return [
            {
                "id": r[0], "source_type": r[1], "source_name": r[2], "last_fetched_at": r[3],
                "reliability_score": float(r[4]), "is_primary": r[5],
                "status_signal": r[6],
            }
            for r in cur.fetchall()
        ]


def _current_status(conn, place_id: str) -> tuple[str, datetime | None]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status::text, status_last_verified_at FROM places WHERE id = %s",
            (place_id,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else ("unverified", None)


def classify(pool, place_id: str, *, persist: bool = True) -> StatusVerdict:
    """Thin wrapper around _score_verdict that hits the DB and (optionally) writes back."""
    with pool.connection() as conn:
        sources = _fetch_sources(conn, place_id)
        prev_status, _ = _current_status(conn, place_id)

    verdict = _score_verdict(sources)

    if persist:
        _persist(pool, place_id, prev_status, verdict)
    return verdict


def _persist(pool, place_id: str, prev_status: str, verdict: StatusVerdict) -> None:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE places SET status = %s::place_status,
                              status_confidence = %s,
                              status_reason = %s,
                              status_last_verified_at = now(),
                              updated_at = now()
            WHERE id = %s
            """,
            (verdict.status, verdict.confidence, verdict.reason, place_id),
        )
        if prev_status != verdict.status:
            cur.execute(
                """
                INSERT INTO place_status_history
                    (place_id, previous_status, new_status, change_reason, source_id)
                VALUES (%s, %s::place_status, %s::place_status, %s, %s)
                """,
                (place_id, prev_status, verdict.status, verdict.reason,
                 verdict.triggering_source_id),
            )
