"""Geo helpers — distance scoring + a tiny geocoding stub."""
from __future__ import annotations

import math


def distance_score(meters: float | None, ref_m: float = 200.0) -> float:
    """Smooth 0..1 score, ~1 at zero distance, decays past ref_m."""
    if meters is None:
        return 0.0
    return math.exp(-max(meters, 0.0) / ref_m)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
