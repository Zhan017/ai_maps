"""Read-side enrichment: shape full place profiles from the canonical tables."""
from __future__ import annotations

from app.db.queries import (
    fetch_amenities,
    fetch_attributes,
    fetch_hours,
    fetch_place_core,
    fetch_sources,
    fetch_status_history,
)


def full_profile(pool, place_id: str, *, include_sources: bool = False,
                 include_history: bool = False) -> dict | None:
    with pool.connection() as conn:
        core = fetch_place_core(conn, place_id)
        if not core:
            return None
        amenities = fetch_amenities(conn, place_id)
        hours = fetch_hours(conn, place_id)
        attributes = fetch_attributes(conn, place_id)
        sources = fetch_sources(conn, place_id) if include_sources else None
        history = fetch_status_history(conn, place_id) if include_history else None

    profile = {
        "place_id": core["id"],
        "primary_name": core["primary_name"],
        "name_local": core["name_local"],
        "brand_name": core["brand_name"],
        "category": core["category"],
        "category_name": core["category_name"],
        "status": core["status"],
        "status_confidence": core["status_confidence"],
        "status_reason": core["status_reason"],
        "status_last_verified_at": core["status_last_verified_at"],
        "formatted_address": core["formatted_address"],
        "location": core["location"],
        "country_code": core["country_code"],
        "time_zone": core["time_zone"],
        "website": core["primary_website_url"],
        "phone": core["phone_number"],
        "email": core["email"],
        "popularity_score": core["popularity_score"],
        "created_at": core["created_at"],
        "updated_at": core["updated_at"],
        "hours": hours,
        "amenities": amenities,
        "attributes": attributes,
    }
    if include_sources:
        profile["sources"] = sources
    if include_history:
        profile["status_history"] = history
    return profile


def attributes_summary(pool, place_id: str) -> dict:
    """Compact view for validate-enrich responses."""
    with pool.connection() as conn:
        core = fetch_place_core(conn, place_id)
        if not core:
            return {}
        amenities = fetch_amenities(conn, place_id)
        hours = fetch_hours(conn, place_id)
    return {
        "primary_name": core["primary_name"],
        "formatted_address": core["formatted_address"],
        "category": core["category"],
        "country_code": core["country_code"],
        "location": core["location"],
        "website": core["primary_website_url"],
        "phone": core["phone_number"],
        "hours": hours,
        "amenities": amenities,
    }
