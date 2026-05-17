"""Rules-based status classifier over place_sources.

Reads the existing source rows for a place, looks at status signals + last
fetched recency, and decides on (status, confidence, reason). Writes
place_status_history when the status changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class StatusVerdict:
    status: str
    confidence: float
    reason: str
    sources_considered: int


def _fetch_sources(conn, place_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_type::text, source_name, last_fetched_at,
                   reliability_score, is_primary, status_signal
            FROM place_sources WHERE place_id = %s
            """,
            (place_id,),
        )
        return [
            {
                "source_type": r[0], "source_name": r[1], "last_fetched_at": r[2],
                "reliability_score": float(r[3]), "is_primary": r[4],
                "status_signal": r[5],
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
    with pool.connection() as conn:
        sources = _fetch_sources(conn, place_id)
        prev_status, _ = _current_status(conn, place_id)

    if not sources:
        verdict = StatusVerdict("unverified", 0.5, "no sources", 0)
    else:
        closed_signals = [s for s in sources if (s["status_signal"] or "").lower() in
                          {"permanently_closed", "closed"}]
        if closed_signals:
            best = max(closed_signals, key=lambda s: s["reliability_score"])
            verdict = StatusVerdict(
                "permanently_closed",
                round(min(0.99, 0.5 + best["reliability_score"] * 0.5), 3),
                f"closed signal from {best['source_name']}",
                len(sources),
            )
        else:
            now = datetime.now(timezone.utc)
            fresh = [s for s in sources
                     if s["last_fetched_at"] and (now - s["last_fetched_at"]).days <= 60]
            weight = sum(s["reliability_score"] for s in fresh)
            if fresh and weight >= 0.5:
                conf = round(min(0.99, 0.5 + weight * 0.2), 3)
                top = max(fresh, key=lambda s: s["reliability_score"])
                verdict = StatusVerdict(
                    "open", conf,
                    f"active on {top['source_name']} as of "
                    f"{top['last_fetched_at'].date().isoformat()}",
                    len(sources),
                )
            else:
                verdict = StatusVerdict("unverified", 0.55, "stale or weak sources", len(sources))

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
                    (place_id, previous_status, new_status, change_reason)
                VALUES (%s, %s::place_status, %s::place_status, %s)
                """,
                (place_id, prev_status, verdict.status, verdict.reason),
            )
