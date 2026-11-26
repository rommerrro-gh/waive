import os
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
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

from app.embeddings import vector_literal, vectors
from app.search import SEMANTIC_OFFERS, assemble_results, lexical_candidates, suggest_families

logger = logging.getLogger(__name__)
inference_slots = BoundedSemaphore(4)


def request_vector(executor, query):
    if not inference_slots.acquire(blocking=False):
        return None
    future = executor.submit(vectors, "query", [query], 0.8)
    future.add_done_callback(lambda _: inference_slots.release())
    return future


def semantic_candidates(connection, future, snapshot, revision):
    if future is None:
        return [], False
    try:
        actual_revision, embeddings = future.result(timeout=0.8)
        if actual_revision != revision:
            return [], False
        with connection.transaction():
            rows = connection.execute(SEMANTIC_OFFERS, {
                "snapshot": snapshot, "embedding": vector_literal(embeddings[0]),
            }).fetchall()
        return rows, True
    except (OSError, ValueError, TypeError, KeyError, TimeoutError, QueryCanceled):
        logger.warning("Запрос обслужен в режиме текстового поиска")
        return [], False


@asynccontextmanager
async def lifespan(app):
    with ConnectionPool(
        os.environ["DATABASE_URL"], min_size=0, max_size=8, timeout=2,
        kwargs={"row_factory": dict_row, "autocommit": True, "options": "-c statement_timeout=1800"},
        open=False,
    ) as pool, ThreadPoolExecutor(max_workers=4) as executor:
        app.state.pool = pool
        app.state.executor = executor
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


@app.get("/suggest")
def suggest(q: Annotated[str, Query(max_length=120)]):
    query = " ".join(q.split())
    if len(query) < 2:
        return {"suggestions": []}
    try:
        with app.state.pool.connection() as connection:
            active = connection.execute("SELECT snapshot_id FROM active_catalog").fetchone()
            items = suggest_families(connection, active["snapshot_id"], query) if active else []
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Подсказки временно недоступны") from error
    return {"suggestions": items}


@app.get("/search")
def find_products(q: Annotated[str, Query(min_length=1, max_length=200)], semantic: bool = True):
    query = " ".join(q.split())
    if not query:
        raise HTTPException(422, "Введите поисковый запрос")
    try:
        with app.state.pool.connection() as connection, connection.transaction():
            active = connection.execute("SELECT snapshot_id FROM active_catalog").fetchone()
            if not active:
                raise HTTPException(503, "Каталог готовится")
            snapshot = active["snapshot_id"]
            revision = os.environ.get("MODEL_REVISION")
            indexed = None
            if semantic and revision and os.environ.get("EMBEDDING_URL"):
                indexed = connection.execute("""
                    SELECT 1 FROM semantic_catalog WHERE snapshot_id = %s AND model_revision = %s
                """, (snapshot, revision)).fetchone()
            future = request_vector(app.state.executor, query) if indexed else None
            lexical = lexical_candidates(connection, snapshot, query)
            semantic_rows, used_model = semantic_candidates(connection, future, snapshot, revision)
            items, alternatives, recommendations = assemble_results(
                connection, snapshot, query, lexical, semantic_rows
            )
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Поиск временно недоступен") from error
    return {
        "results": items,
        "alternatives": alternatives,
        "recommendations": recommendations,
        "clarification": None if items else "Уточните название, бренд или артикул",
        "search_mode": "hybrid" if used_model else "lexical",
        "catalog_version": snapshot,
        "request_id": str(uuid4()),
    }


app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="ui")
