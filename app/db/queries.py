"""SQL constants and small helpers."""
from __future__ import annotations

import json
from typing import Any

NEARBY_CANDIDATES_SQL = """
    SELECT p.id::text, p.primary_name, p.name_local, p.brand_name, p.phone_number, p.primary_website_url,
           pa.formatted_address, pa.street, pa.house_number, pa.city,
           ST_Distance(p.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS meters,
           p.category_id, pc.code AS category_code
    FROM places p
    LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
    LEFT JOIN place_categories pc ON pc.id = p.category_id
    WHERE ST_DWithin(p.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
    ORDER BY meters
    LIMIT %s
"""


def fetch_place_core(conn, place_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id::text, p.primary_name, p.name_local, p.brand_name,
                   p.status::text, p.status_confidence, p.status_reason, p.status_last_verified_at,
                   ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lng,
                   p.primary_website_url, p.phone_number, p.email,
                   p.country_code, p.time_zone, p.popularity_score,
                   p.created_at, p.updated_at,
                   pa.formatted_address, pa.street, pa.house_number, pa.city, pa.state, pa.postal_code,
                   pc.code AS category_code, pc.name AS category_name
            FROM places p
            LEFT JOIN place_addresses pa ON pa.place_id = p.id AND pa.is_primary
            LEFT JOIN place_categories pc ON pc.id = p.category_id
            WHERE p.id = %s
            """,
            (place_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "primary_name": row[1],
        "name_local": row[2],
        "brand_name": row[3],
        "status": row[4],
        "status_confidence": float(row[5]) if row[5] is not None else None,
        "status_reason": row[6],
        "status_last_verified_at": row[7].isoformat() if row[7] else None,
        "location": {"lat": row[8], "lng": row[9]},
        "primary_website_url": row[10],
        "phone_number": row[11],
        "email": row[12],
        "country_code": row[13],
        "time_zone": row[14],
        "popularity_score": float(row[15]) if row[15] is not None else 0.0,
        "created_at": row[16].isoformat() if row[16] else None,
        "updated_at": row[17].isoformat() if row[17] else None,
        "formatted_address": row[18],
        "street": row[19],
        "house_number": row[20],
        "city": row[21],
        "state": row[22],
        "postal_code": row[23],
        "category": row[24],
        "category_name": row[25],
    }


def fetch_amenities(conn, place_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT wifi, parking::text, outdoor_seating, wheelchair_accessible,
                   vegan_options, pet_friendly, kids_friendly, price_level::text
            FROM place_amenities WHERE place_id = %s
            """,
            (place_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    keys = ["wifi", "parking", "outdoor_seating", "wheelchair_accessible",
            "vegan_options", "pet_friendly", "kids_friendly", "price_level"]
    return {k: v for k, v in zip(keys, row) if v is not None}


def fetch_hours(conn, place_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT day_of_week, to_char(open_time, 'HH24:MI'), to_char(close_time, 'HH24:MI'),
                   is_overnight
            FROM place_hours WHERE place_id = %s ORDER BY day_of_week
            """,
            (place_id,),
        )
        rows = cur.fetchall()
    return [
        {"day_of_week": r[0], "open_time": r[1], "close_time": r[2], "is_overnight": r[3]}
        for r in rows
    ]


def fetch_attributes(conn, place_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT namespace, key, value_type::text,
                   value_string, value_number, value_boolean, value_json,
                   updated_at
            FROM place_attributes WHERE place_id = %s
            ORDER BY namespace, key
            """,
            (place_id,),
        )
        rows = cur.fetchall()
    out = []
    for ns, key, vt, vs, vn, vb, vj, ts in rows:
        value: Any
        if vt == "string":
            value = vs
        elif vt == "number":
            value = float(vn) if vn is not None else None
        elif vt == "boolean":
            value = vb
        else:
            value = vj
        out.append({
            "namespace": ns,
            "key": key,
            "value": value,
            "updated_at": ts.isoformat() if ts else None,
        })
    return out


def fetch_sources(conn, place_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_type::text, source_name, source_url,
                   last_fetched_at, reliability_score, is_primary, status_signal
            FROM place_sources WHERE place_id = %s
            ORDER BY is_primary DESC, reliability_score DESC
            """,
            (place_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "source_type": r[0],
            "source_name": r[1],
            "source_url": r[2],
            "last_fetched_at": r[3].isoformat() if r[3] else None,
            "reliability_score": float(r[4]),
            "is_primary": r[5],
            "status_signal": r[6],
        }
        for r in rows
    ]


def fetch_status_history(conn, place_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT previous_status::text, new_status::text, changed_at, change_reason
            FROM place_status_history WHERE place_id = %s
            ORDER BY changed_at DESC LIMIT 20
            """,
            (place_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "previous_status": r[0],
            "new_status": r[1],
            "changed_at": r[2].isoformat() if r[2] else None,
            "change_reason": r[3],
        }
        for r in rows
    ]
