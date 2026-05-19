"""FastAPI entrypoint. Wires DB pool, OpenAI, routers, static UI."""
from __future__ import annotations

import logging
import time
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

# Dedicated namespace so we don't collide with uvicorn.access's formatter.
# uvicorn doesn't configure handlers for our namespace, so attach one once on
# import — otherwise the log calls silently drop.
access_log = logging.getLogger("app.requests")
access_log.setLevel(logging.INFO)
if not access_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    access_log.addHandler(_h)
    access_log.propagate = False  # don't double-emit through uvicorn's root


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


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    access_log.info(
        "%s %s -> %d in %dms",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response


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
