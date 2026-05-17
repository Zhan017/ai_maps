"""Happy-path tests for POST /v1/places:validate-enrich.

Picks a real seeded place at random, sends back its own name + coords, and
expects a HIGH-confidence match. Then sends a clearly novel input and
expects an unverified place to be created.
"""
from __future__ import annotations

import psycopg

from app.core.config import DSN


def _sample_place():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id::text, p.primary_name, pa.formatted_address, pc.code,
                   ST_Y(p.location::geometry), ST_X(p.location::geometry)
            FROM places p
            LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
            LEFT JOIN place_categories pc ON pc.id = p.category_id
            WHERE p.status = 'open'
            ORDER BY p.id
            LIMIT 1
            """
        )
        return cur.fetchone()


def test_known_place_matches(client, auth_headers):
    sample = _sample_place()
    assert sample, "DB is empty — run scripts.seed first"
    pid, name, addr, cat, lat, lng = sample

    body = {
        "places": [{
            "customer_place_id": "test-1",
            "name": name,
            "address": addr,
            "lat": lat,
            "lng": lng,
            "category": cat,
        }],
        "options": {"enrich": True, "validate_status": True, "return_full_profile": False},
    }
    r = client.post("/v1/places:validate-enrich", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["canonical_place_id"] == pid
    assert result["match_confidence"] >= 0.85
    assert result["match_decision"] == "match"
    assert "attributes" in result


def test_novel_place_is_created_unverified(client, auth_headers):
    import random
    import uuid
    # Unique name + randomized coords on the South Pacific (Point Nemo region).
    # Two safeguards against prior-test pollution: a) the candidate radius is
    # 500m and we randomize within ±0.5° (~50km), so no two runs share a 500m
    # window; b) the name is unique. Together these guarantee an empty
    # candidate pool on every run.
    unique = uuid.uuid4().hex[:8]
    lat = -48.0 + random.uniform(-0.5, 0.5)
    lng = -123.0 + random.uniform(-0.5, 0.5)
    body = {
        "places": [{
            "customer_place_id": f"test-novel-{unique}",
            "name": f"ZZZZZZ NoMatchTest {unique}",
            "lat": lat,
            "lng": lng,
            "category": "cafe",
        }]
    }
    r = client.post("/v1/places:validate-enrich", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["match_decision"] in {"no_match", "low_confidence"}
    if result["match_decision"] == "no_match":
        assert result["status"] == "unverified"


def test_get_place_returns_full_profile(client, auth_headers):
    sample = _sample_place()
    pid = sample[0]
    r = client.get(f"/v1/places/{pid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["place_id"] == pid
    assert "hours" in body
    assert "amenities" in body


def test_batch_status(client, auth_headers):
    sample = _sample_place()
    pid = sample[0]
    r = client.post("/v1/places:status", json={"place_ids": [pid]}, headers=auth_headers)
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["place_id"] == pid
    assert "freshness" in res
