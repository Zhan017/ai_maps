"""V1 search endpoint — hybrid geo + semantic."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_openai, get_pool
from app.services import search as search_svc

router = APIRouter(tags=["search"])


@router.get("/places:search")
def places_search(
    q: str | None = Query(None),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_m: int = Query(1000, gt=0, le=20000),
    category: str | None = Query(None),
    country_code: str | None = Query(None),
    open_now: bool = Query(False),
    wifi: bool | None = Query(None),
    outdoor: bool | None = Query(None),
    limit: int = Query(20, gt=0, le=100),
    offset: int = Query(0, ge=0),
    pool=Depends(get_pool),
    openai_client=Depends(get_openai),
):
    return search_svc.search(
        pool, openai_client,
        search_svc.SearchQuery(
            q=q, lat=lat, lng=lng, radius_m=radius_m,
            category=category, country_code=country_code,
            open_now=open_now,
            amenity_wifi=wifi, amenity_outdoor=outdoor,
            limit=limit, offset=offset,
        ),
    )
