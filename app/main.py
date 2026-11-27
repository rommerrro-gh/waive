import os
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
from contextlib import asynccontextmanager
from html import unescape
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg import OperationalError
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from app.analytics import configured, record_search, router
from app.brand_recovery import load_model
from app.procurement import LICENSE_TYPES, PLATFORMS, purchase_values, requested_filters
from app.quote import router as quote_router
from app.requirements import parse_requirements, retrieval_query
from app.embeddings import vector_literal, vectors
from app.search import (
    BRAND_BY_NAME, SEMANTIC_OFFERS, as_card, assemble_results, catalog_identifier, load_category_coverage,
    is_kaspersky_query, recommend_for_anchor, resolve_catalog_query, suggest_families,
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
    load_model()
    with ConnectionPool(
        os.environ["DATABASE_URL"], min_size=0, max_size=8, timeout=2,
        kwargs={"row_factory": dict_row, "autocommit": True, "options": "-c statement_timeout=1800"},
        open=False,
    ) as pool, ThreadPoolExecutor(max_workers=4) as executor:
        app.state.pool = pool
        app.state.executor = executor
        app.state.coverage = {}
        yield


app = FastAPI(title="Waive", lifespan=lifespan)
app.include_router(router)
app.include_router(quote_router)


def snapshot_coverage(connection, snapshot):
    cached = app.state.coverage.get(snapshot)
    if cached is not None:
        return cached
    calculated = load_category_coverage(connection, snapshot)
    app.state.coverage = {snapshot: calculated}
    return calculated


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
    if len(query) < 2 or not any(char.isalnum() for char in query):
        return {"suggestions": []}
    try:
        with app.state.pool.connection() as connection:
            active = connection.execute("SELECT snapshot_id FROM active_catalog").fetchone()
            items = suggest_families(connection, active["snapshot_id"], query) if active else []
            if active and not items:
                snapshot = active["snapshot_id"]
                decision = resolve_catalog_query(connection, snapshot, query, prefixes=items)
                items = decision.choices
                if decision.lexical:
                    if decision.lookup.casefold() != query.casefold() or decision.lookup.casefold() in BRAND_BY_NAME:
                        items = [decision.lookup, *suggest_families(connection, snapshot, decision.lookup)]
                    else:
                        items = [unescape(row["name"]) for row in decision.lexical if row["available"]]
            items = list(dict.fromkeys(items))[:6]
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
    in_stock: bool = False,
    price_min: Annotated[int | None, Query(ge=0, le=999_999_999)] = None,
    price_max: Annotated[int | None, Query(ge=0, le=999_999_999)] = None,
    expanded: bool = False,
    track: bool = False,
    demo: bool = False,
    with_profile: bool = False,
):
    query = " ".join(q.split())
    if len(query) < 2 or not any(char.isalnum() for char in query):
        raise HTTPException(422, "Введите название, бренд или артикул от двух символов")
    if price_min is not None and price_max is not None and price_min > price_max:
        raise HTTPException(422, "Цена «От» должна быть не выше цены «До»")
    filters = {key: value for key, value in {
        "platform": platform, "term": term, "license_type": license_type,
        "in_stock": in_stock or None, "price_min": price_min, "price_max": price_max,
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
            decision = resolve_catalog_query(connection, snapshot, query)
            lookup, lexical, spellings = decision.lookup, decision.lexical, decision.choices
            intent = retrieval_query(lookup) if parse_requirements(lookup) else lookup
            if intent != lookup and not spellings and not any(item["exact"] for item in lexical):
                narrowed = resolve_catalog_query(connection, snapshot, intent)
                existing = {item["offer_id"] for item in lexical}
                lexical.extend(item for item in narrowed.lexical if item["offer_id"] not in existing)
                if not lexical:
                    spellings = narrowed.choices
                if narrowed.lookup != intent and lookup.casefold().startswith(intent.casefold()):
                    lookup = narrowed.lookup + lookup[len(intent):]
                intent = narrowed.lookup
            corrected = lookup.casefold() != query.casefold()
            brand_only = is_kaspersky_query(lookup) or (corrected and lookup.casefold() in BRAND_BY_NAME)
            revision = os.environ.get("MODEL_REVISION")
            indexed = None
            allow_semantic = not spellings and (lexical or len(query.split()) >= 3)
            if (semantic and allow_semantic and not brand_only and not catalog_identifier(query)
                    and not any(item["exact"] for item in lexical) and revision and os.environ.get("EMBEDDING_URL")):
                indexed = connection.execute("""
                    SELECT 1 FROM semantic_catalog WHERE snapshot_id = %s AND model_revision = %s
                """, (snapshot, revision)).fetchone()
            future = request_vector(app.state.executor, intent) if indexed else None
            semantic_rows, used_model = semantic_candidates(connection, future, snapshot, revision)
            items, alternatives, recommendations = assemble_results(
                connection, snapshot, lookup, lexical, semantic_rows, filters, diagnostics,
                expanded=expanded, coverage=snapshot_coverage(connection, snapshot), intent_query=intent,
            )
            category_ids = sorted({item["category_id"] for item in items if item["category_id"]})
            result_categories = connection.execute("""
                SELECT id, name FROM category
                WHERE snapshot_id = %s AND id = ANY(%s)
                ORDER BY name, id
            """, (snapshot, category_ids)).fetchall() if category_ids else []
            if track:
                track = record_search(
                    connection, request_id, query, filters, diagnostics, items, recommendations, demo, with_profile,
                )
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Поиск временно недоступен") from error
    available_items = [item for item in items if item["available"]] if alternatives else items
    unavailable_items = [item for item in items if not item["available"]] if alternatives else []
    return {
        "results": items,
        "result_categories": result_categories,
        "top4": (available_items + alternatives + recommendations + unavailable_items)[:4],
        "alternatives": alternatives,
        "recommendations": recommendations,
        "applied_filters": filters,
        "suggested_filters": requested_filters(query),
        "corrected_query": lookup if corrected else None,
        "spelling_suggestions": spellings,
        "filter_diagnostics": diagnostics if filters or diagnostics.get("requirements_unmet") else None,
        "tracked": track,
        "clarification": (
            "Точного товара нет в наличии. Найдены альтернативы" if alternatives and not any(item["available"] for item in items) else
            "Точный артикул не найден" if catalog_identifier(query) and not items else
            "Товар найден, но сейчас недоступен. Подтверждённых аналогов нет" if items and not any(item["available"] for item in items) else
            "По заданным условиям подходящие предложения не подтверждены" if (filters or diagnostics.get("requirements_unmet")) and not items else
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
    q: Annotated[str, Query(max_length=200)] = "",
    seats: Annotated[int | None, Query(ge=1, le=1_000_000)] = None,
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
            if not anchor["available"]:
                return {"anchor": anchor, "items": [], "status": "anchor_unavailable"}
            if platform and platform not in purchase_values(anchor["attributes"])["platform"]:
                raise HTTPException(422, "Платформа основной позиции не подтверждена")
            if seats:
                q += f", на {seats} рабочих мест"
            requirements = parse_requirements(q, {"platform": platform} if platform else None)
            items, status = recommend_for_anchor(
                connection, catalog_version, anchor, snapshot_coverage(connection, catalog_version), requirements,
            )
            return {"anchor": anchor, "items": items, "status": status}
    except (OperationalError, PoolTimeout, QueryCanceled) as error:
        raise HTTPException(503, "Подбор комплекта временно недоступен") from error


static_folder = Path(__file__).with_name("static")


@app.get("/business", include_in_schema=False)
def business_page():
    return FileResponse(static_folder / "index.html")


app.mount("/", StaticFiles(directory=static_folder, html=True), name="ui")
