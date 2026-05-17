"""API-key auth via X-API-Key header.

Currently DEMO MODE — the dependency falls back to a default customer when
no header is present so the UI works without configuration. To re-enable
strict auth, set REQUIRE_API_KEY=1 in the environment.
"""
from __future__ import annotations

import os

import bcrypt
from fastapi import Header, HTTPException, Request

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
    if _DEFAULT is None:
        accounts = _load_accounts(pool)
        _DEFAULT = accounts[0] if accounts else {
            "id": None, "name": "anonymous", "plan_tier": "demo"
        }
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

    if x_api_key in _CACHE:
        return _CACHE[x_api_key]

    accounts = _load_accounts(pool)
    key_bytes = x_api_key.encode("utf-8")
    for acct in accounts:
        try:
            if bcrypt.checkpw(key_bytes, acct["api_key_hash"].encode("utf-8")):
                _CACHE[x_api_key] = acct
                return acct
        except ValueError:
            continue

    if REQUIRE_API_KEY:
        raise HTTPException(401, "Invalid API key")
    return _default_customer(pool)


def clear_cache() -> None:
    _CACHE.clear()
    global _DEFAULT
    _DEFAULT = None
