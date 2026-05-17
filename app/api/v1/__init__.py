from fastapi import APIRouter, Depends

from app.api.v1 import admin, places, search
from app.core.security import require_api_key

v1_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
v1_router.include_router(places.router)
v1_router.include_router(search.router)

internal_router = APIRouter(prefix="/internal", dependencies=[Depends(require_api_key)])
internal_router.include_router(admin.router)
