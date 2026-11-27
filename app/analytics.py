import logging
import os
import re
from hmac import compare_digest
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from psycopg import Error
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)

GROUPS = """
    SELECT query_group AS query, filters, reason, count(*) AS searches,
           count(*) FILTER (WHERE direct_clicked) AS direct_clicks
    FROM search_event
    WHERE demo = %s AND created_at >= now() - interval '30 days'
      AND (%s::boolean IS NULL OR with_profile = %s)
    GROUP BY query_group, filters, reason
    HAVING reason <> 'full' OR (count(*) >= 3 AND NOT bool_or(direct_clicked))
    ORDER BY searches DESC, query_group, reason, filters
    LIMIT 30
"""


def configured():
    return bool(os.getenv("ANALYTICS_TOKEN"))


def miss_reason(diagnostics):
    if not diagnostics["families_before"]:
        return "no_candidates"
    conditions = diagnostics["conditions"]
    if diagnostics["families_after"] < 4 and conditions:
        key = max(conditions, key=lambda key: conditions[key]["would_restore"])
        condition = conditions[key]
        if condition["would_restore"]:
            kind = "unverified" if condition["unverified"] == condition["would_restore"] else "filter"
            return f"{kind}.{key}"
        if diagnostics["families_before"] > diagnostics["families_after"]:
            return "combined_filters"
    return "few_matches" if diagnostics["families_after"] < 4 else "full"


def record_search(connection, request_id, query, filters, diagnostics, items, recommendations, demo, with_profile):
    # Поисковая строка может содержать контакты. Сохраняем только группу с маскировкой.
    group = re.sub(r"\S+@\S+|https?://\S+|\+?\d[\d ()-]{8,}\d", "[скрыто]", query.casefold())
    try:
        with connection.transaction():
            connection.execute("""
                INSERT INTO search_event
                    (id, demo, with_profile, query_group, filters, reason, direct_count, direct_ids, recommendation_ids)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (request_id, demo, with_profile, group, Jsonb(filters), miss_reason(diagnostics), len(items),
                  [item["offer_id"] for item in items], [item["offer_id"] for item in recommendations]))
    except Error:
        logger.warning("Событие поиска не записано")
        return False
    return True


class Click(BaseModel):
    request_id: UUID | None = None
    visit_id: UUID | None = None
    catalog_version: int | None = Field(default=None, gt=0)
    offer_id: str = Field(min_length=1, max_length=100)


@router.post("/analytics/events/click", status_code=204)
def record_click(event: Click, request: Request):
    if not (event.request_id and configured()) and not (event.visit_id and event.catalog_version):
        return Response(status_code=204)
    try:
        with request.app.state.pool.connection() as connection, connection.transaction():
            if event.request_id and configured():
                connection.execute("""
                    UPDATE search_event SET
                        direct_clicked = direct_clicked OR %s = ANY(direct_ids),
                        recommendation_clicked = recommendation_clicked OR %s = ANY(recommendation_ids)
                    WHERE id = %s AND created_at >= now() - interval '30 days'
                      AND (%s = ANY(direct_ids) OR %s = ANY(recommendation_ids))
                """, (event.offer_id, event.offer_id, event.request_id, event.offer_id, event.offer_id))
            if event.visit_id and event.catalog_version:
                connection.execute("""
                    INSERT INTO offer_visit (visit_id, offer_id)
                    SELECT %s, o.id FROM active_catalog a JOIN offer o ON o.snapshot_id = a.snapshot_id
                    WHERE a.snapshot_id = %s AND o.id = %s
                    ON CONFLICT DO NOTHING
                """, (event.visit_id, event.catalog_version, event.offer_id))
    except (Error, PoolTimeout):
        logger.warning("Клик не записан")
    return Response(status_code=204)


@router.get("/analytics/report")
def report(
    request: Request, response: Response, demo: bool = False,
    with_profile: bool | None = None, x_analytics_token: str = Header(""),
):
    token = os.getenv("ANALYTICS_TOKEN")
    if not token:
        raise HTTPException(503, "Аналитика выключена")
    if not compare_digest(token.encode(), x_analytics_token.encode()):
        raise HTTPException(403, "Проверьте ключ аналитики")
    response.headers["Cache-Control"] = "no-store"
    segment = (demo, with_profile, with_profile)
    try:
        with request.app.state.pool.connection() as connection:
            metrics = connection.execute("""
                SELECT count(*) AS searches,
                       count(*) FILTER (WHERE direct_count = 0) AS empty,
                       count(*) FILTER (WHERE direct_count BETWEEN 1 AND 3) AS few,
                       count(*) FILTER (WHERE direct_clicked) AS direct_clicks,
                       count(*) FILTER (WHERE recommendation_clicked) AS recommendation_clicks
                FROM search_event
                WHERE demo = %s AND created_at >= now() - interval '30 days'
                  AND (%s::boolean IS NULL OR with_profile = %s)
            """, segment).fetchone()
            rows = connection.execute(GROUPS, segment).fetchall()
    except (Error, PoolTimeout) as error:
        raise HTTPException(503, "Аналитика временно недоступна") from error
    return {"metrics": metrics, "groups": rows, "days": 30}
