import os
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from psycopg import OperationalError
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from app.analytics import configured, record_search, router
from app.procurement import LICENSE_TYPES, PLATFORMS, purchase_values, requested_filters
from app.embeddings import vector_literal, vectors
from app.search import (
    SEMANTIC_OFFERS, as_card, assemble_results, fill_recommendations, lexical_candidates,
    suggest_families,
)

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
app.include_router(router)


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
def find_products(
    q: Annotated[str, Query(min_length=1, max_length=200)],
    semantic: bool = True,
    platform: Literal[*PLATFORMS] | None = None,
    term: Annotated[str | None, Query(pattern=r"^(?:[1-9][0-9]?|1[01][0-9]|120|perpetual)$")] = None,
    license_type: Literal[*LICENSE_TYPES] | None = None,
    track: bool = False,
    demo: bool = False,
    with_profile: bool = False,
):
    query = " ".join(q.split())
    if not query:
        raise HTTPException(422, "Введите поисковый запрос")
    filters = {key: value for key, value in {
        "platform": platform, "term": term, "license_type": license_type,
    }.items() if value is not None}
    diagnostics = {}
    request_id = uuid4()
    track = track and configured()
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
                connection, snapshot, query, lexical, semantic_rows, filters, diagnostics,
            )
            if track:
                track = record_search(
                    connection, request_id, query, filters, diagnostics, items, recommendations, demo, with_profile,
                )
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Поиск временно недоступен") from error
    return {
        "results": items,
        "top4": items + recommendations,
        "alternatives": alternatives,
        "recommendations": recommendations,
        "applied_filters": filters,
        "suggested_filters": requested_filters(query),
        "filter_diagnostics": diagnostics if filters else None,
        "tracked": track,
        "clarification": (
            "По выбранным условиям подходящие предложения не подтверждены" if filters and not items else
            "Уточните название, бренд или артикул" if not items else
            "Найдено меньше четырёх подходящих семейств" if len(items) + len(recommendations) < 4 else None
        ),
        "search_mode": "hybrid" if used_model else "lexical",
        "catalog_version": snapshot,
        "request_id": str(request_id),
    }


@app.get("/bundle")
def company_bundle(
    offer_id: Annotated[str, Query(min_length=1, max_length=100)],
    catalog_version: Annotated[int, Query(gt=0)],
    platform: Literal[*PLATFORMS] | None = None,
):
    try:
        with app.state.pool.connection() as connection, connection.transaction():
            active = connection.execute("SELECT snapshot_id FROM active_catalog").fetchone()
            if not active or active["snapshot_id"] != catalog_version:
                raise HTTPException(409, "Каталог обновился. Повторите поиск")
            row = connection.execute("""
                SELECT id AS offer_id, vendor_code, group_id, category_id, name, vendor, url,
                       price, currency, available, attributes, left(description, 1200) AS description
                FROM offer WHERE snapshot_id = %s AND id = %s
            """, (catalog_version, offer_id)).fetchone()
            if not row:
                raise HTTPException(404, "Предложение не найдено")
            anchor = as_card(row, "Основная позиция")
            if platform and platform not in purchase_values(anchor["attributes"])["platform"]:
                raise HTTPException(422, "Платформа основной позиции не подтверждена")
            return {"anchor": anchor, "items": fill_recommendations(
                connection, catalog_version, "", [anchor], platform
            )}
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Подбор комплекта временно недоступен") from error


app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="ui")
