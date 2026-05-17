"""Test fixtures.

Tests use the *existing* seeded DB. They assume `python -m scripts.seed` has
been run. We create an isolated FastAPI TestClient and an ephemeral customer
account with a known API key so /v1/* calls auth correctly.
"""
from __future__ import annotations

import os
import secrets

import bcrypt
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import DSN
from app.core import security as security_mod


@pytest.fixture(scope="session")
def api_key() -> str:
    key = "vk_test_" + secrets.token_urlsafe(16)
    key_hash = bcrypt.hashpw(key.encode(), bcrypt.gensalt()).decode()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_accounts (name, api_key_hash, plan_tier) VALUES (%s, %s, 'test') RETURNING id",
            (f"test-{secrets.token_hex(4)}", key_hash),
        )
    yield key
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM customer_accounts WHERE api_key_hash = %s", (key_hash,))
    security_mod.clear_cache()


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers(api_key):
    return {"X-API-Key": api_key}
