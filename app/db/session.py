from psycopg_pool import ConnectionPool

from app.core.config import DSN, POOL_MAX_SIZE, POOL_MIN_SIZE


def make_pool() -> ConnectionPool:
    return ConnectionPool(DSN, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE, open=True)
