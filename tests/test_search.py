"""Tests for /v1/places:search.

Structured filters are deterministic. The semantic test only runs when
OPENAI_API_KEY is set and embeddings have been built (places_vectors is
non-empty).
"""
from __future__ import annotations

import os

import psycopg
import pytest

from app.core.config import DSN


def _vectors_built() -> bool:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM places_vectors")
        return cur.fetchone()[0] > 100


def test_structured_search_with_category(client, auth_headers):
    r = client.get(
        "/v1/places:search",
        params={"lat": 51.13, "lng": 71.43, "radius_m": 2000,
                "category": "cafe", "limit": 10},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] > 0
    for item in body["results"]:
        assert item["category"] == "cafe"
        assert item["distance_m"] is not None
        assert "reasons" in item
        assert "freshness" in item


def test_structured_search_wifi_filter(client, auth_headers):
    r = client.get(
        "/v1/places:search",
        params={"lat": 51.13, "lng": 71.43, "radius_m": 2000,
                "category": "cafe", "wifi": "true", "limit": 5},
        headers=auth_headers,
    )
    assert r.status_code == 200
    for item in r.json()["results"]:
        # "has wifi" reason should appear when wifi filter is on
        assert any("wifi" in r for r in item["reasons"])


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not _vectors_built(),
    reason="needs OPENAI_API_KEY and built embeddings",
)
def test_semantic_search_runs(client, auth_headers):
    r = client.get(
        "/v1/places:search",
        params={"q": "quiet coffee with wifi", "lat": 51.13, "lng": 71.43,
                "radius_m": 3000, "limit": 5},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"]["semantic"] is True
    assert body["count"] > 0
