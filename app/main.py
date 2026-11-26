import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from psycopg import OperationalError
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from app.search import search


@asynccontextmanager
async def lifespan(app):
    with ConnectionPool(
        os.environ["DATABASE_URL"], min_size=0, max_size=8, timeout=2,
        kwargs={"row_factory": dict_row, "autocommit": True, "options": "-c statement_timeout=1800"},
        open=False,
    ) as pool:
        app.state.pool = pool
        yield


app = FastAPI(title="Waive", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    try:
        with app.state.pool.connection() as connection:
            active = connection.execute("SELECT snapshot_id FROM active_catalog").fetchone()
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "База данных недоступна") from error
    if not active:
        raise HTTPException(503, "Каталог готовится")
    return {"catalog_version": active["snapshot_id"]}


@app.get("/search")
def find_products(q: Annotated[str, Query(min_length=1, max_length=200)]):
    query = " ".join(q.split())
    if not query:
        raise HTTPException(422, "Введите поисковый запрос")
    try:
        with app.state.pool.connection() as connection, connection.transaction():
            active = connection.execute("SELECT snapshot_id FROM active_catalog").fetchone()
            if not active:
                raise HTTPException(503, "Каталог готовится")
            snapshot = active["snapshot_id"]
            items = search(connection, snapshot, query)
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Поиск временно недоступен") from error
    return {
        "results": items,
        "alternatives": [],
        "recommendations": [],
        "clarification": None if items else "Уточните название, бренд или артикул",
        "search_mode": "lexical",
        "catalog_version": snapshot,
        "request_id": str(uuid4()),
    }


app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="ui")
