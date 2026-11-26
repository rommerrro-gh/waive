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


def record_search(connection, request_id, query, filters, diagnostics, items, recommendations, demo):
    # Поисковая строка может содержать контакты. Сохраняем только группу с маскировкой.
    group = re.sub(r"\S+@\S+|https?://\S+|\+?\d[\d ()-]{8,}\d", "[скрыто]", query.casefold())
    try:
        with connection.transaction():
            connection.execute("""
                INSERT INTO search_event
                    (id, demo, query_group, filters, reason, direct_count, direct_ids, recommendation_ids)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (request_id, demo, group, Jsonb(filters), miss_reason(diagnostics), len(items),
                  [item["offer_id"] for item in items], [item["offer_id"] for item in recommendations]))
    except Error:
        logger.warning("Событие поиска не записано")
        return False
    return True


class Click(BaseModel):
    request_id: UUID
    offer_id: str = Field(min_length=1, max_length=100)


@router.post("/analytics/events/click", status_code=204)
def record_click(event: Click, request: Request):
    if configured():
        try:
            with request.app.state.pool.connection() as connection:
                connection.execute("""
                    UPDATE search_event SET
                        direct_clicked = direct_clicked OR %s = ANY(direct_ids),
                        recommendation_clicked = recommendation_clicked OR %s = ANY(recommendation_ids)
                    WHERE id = %s AND created_at >= now() - interval '30 days'
                      AND (%s = ANY(direct_ids) OR %s = ANY(recommendation_ids))
                """, (event.offer_id, event.offer_id, event.request_id, event.offer_id, event.offer_id))
        except (Error, PoolTimeout):
            logger.warning("Клик не записан")
    return Response(status_code=204)


@router.get("/analytics/report")
def report(request: Request, response: Response, demo: bool = False, x_analytics_token: str = Header("")):
    token = os.getenv("ANALYTICS_TOKEN")
    if not token:
        raise HTTPException(503, "Аналитика выключена")
    if not compare_digest(token.encode(), x_analytics_token.encode()):
        raise HTTPException(403, "Проверьте ключ аналитики")
    response.headers["Cache-Control"] = "no-store"
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
            """, (demo,)).fetchone()
            rows = connection.execute(GROUPS, (demo,)).fetchall()
    except (Error, PoolTimeout) as error:
        raise HTTPException(503, "Аналитика временно недоступна") from error
    return {"metrics": metrics, "groups": rows, "days": 30}
