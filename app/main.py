"""FastAPI entrypoint. Wires DB pool, OpenAI, routers, static UI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

from app.api.v1 import internal_router, v1_router
from app.api.v1.legacy import router as legacy_router
from app.core.config import OPENAI_API_KEY
from app.db.session import make_pool

log = logging.getLogger("uvicorn")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = make_pool()
    if OPENAI_API_KEY:
        app.state.openai_client = OpenAI(api_key=OPENAI_API_KEY)
    else:
        app.state.openai_client = None
        log.warning("OPENAI_API_KEY not set — chat + embedding endpoints disabled")
    try:
        yield
    finally:
        app.state.pool.close()


app = FastAPI(lifespan=lifespan, title="VOYGR Replica")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router)
app.include_router(internal_router)
app.include_router(legacy_router)


@app.get("/api/health")
def health(request: Request):
    with request.app.state.pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="ui")
