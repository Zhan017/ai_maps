"""API-key auth via X-API-Key header.

Currently DEMO MODE — the dependency falls back to a default customer when
no header is present so the UI works without configuration. To re-enable
strict auth, set REQUIRE_API_KEY=1 in the environment.
"""
from __future__ import annotations

import logging
import os
import threading

import bcrypt
from fastapi import Header, HTTPException, Request

log = logging.getLogger(__name__)

# Single-worker assumption: cross-worker auth caches would require Redis. The
# lock protects in-memory dict ops only — no awaits / I/O inside critical
# sections.
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, dict] = {}
_DEFAULT: dict | None = None

REQUIRE_API_KEY = os.getenv("REQUIRE_API_KEY", "0") == "1"


def _load_accounts(pool) -> list[dict]:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text, name, api_key_hash, plan_tier FROM customer_accounts ORDER BY created_at")
        return [
            {"id": r[0], "name": r[1], "api_key_hash": r[2], "plan_tier": r[3]}
            for r in cur.fetchall()
        ]


def _default_customer(pool) -> dict:
    global _DEFAULT
    # Fast path: read snapshot under the lock, then return without I/O.
    with _CACHE_LOCK:
        cached = _DEFAULT
    if cached is not None:
        return cached
    # Slow path: DB load happens OUTSIDE the lock.
    accounts = _load_accounts(pool)
    resolved = accounts[0] if accounts else {
        "id": None, "name": "anonymous", "plan_tier": "demo"
    }
    with _CACHE_LOCK:
        if _DEFAULT is None:
            _DEFAULT = resolved
        return _DEFAULT


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict:
    pool = request.app.state.pool

    if not x_api_key:
        if REQUIRE_API_KEY:
            raise HTTPException(401, "Missing X-API-Key header")
        return _default_customer(pool)

    with _CACHE_LOCK:
        cached = _CACHE.get(x_api_key)
    if cached is not None:
        return cached

    # bcrypt + DB query happen OUTSIDE the lock.
    accounts = _load_accounts(pool)
    key_bytes = x_api_key.encode("utf-8")
    for acct in accounts:
        try:
            if bcrypt.checkpw(key_bytes, acct["api_key_hash"].encode("utf-8")):
                with _CACHE_LOCK:
                    _CACHE[x_api_key] = acct
                return acct
        except ValueError as e:
            # Malformed bcrypt hash in the DB — silently skipping it would hide
            # a real data-integrity problem (a customer effectively unable to
            # authenticate). Log so operators see it.
            log.warning("malformed api_key_hash for customer %s: %s", acct.get("id"), e)
            continue

    if REQUIRE_API_KEY:
        raise HTTPException(401, "Invalid API key")
    return _default_customer(pool)


def clear_cache() -> None:
    global _DEFAULT
    with _CACHE_LOCK:
        _CACHE.clear()
        _DEFAULT = None
