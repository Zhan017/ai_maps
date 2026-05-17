"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Request
from psycopg_pool import ConnectionPool


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def get_openai(request: Request):
    return getattr(request.app.state, "openai_client", None)
