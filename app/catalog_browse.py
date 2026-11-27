from datetime import UTC, datetime

from app.procurement import LABELS, purchase_values
from app.search import as_card, card_attributes

SORT_ORDERS = {
    "relevance": "o.available DESC, o.id",
    "price-asc": "CASE WHEN o.price > 0 THEN o.price END ASC NULLS LAST, o.id",
    "price-desc": "CASE WHEN o.price > 0 THEN o.price END DESC NULLS LAST, o.id",
    "popularity": "coalesce(p.visits, 0) DESC, o.available DESC, o.id",
}

POPULARITY = """
    SELECT offer_id, count(*) AS visits FROM offer_visit
"""


def offer_popularity(connection, offer_ids):
    if not offer_ids:
        return {}
    rows = connection.execute(POPULARITY + " WHERE created_at >= now() - interval '30 days'"
                              + " AND offer_id = ANY(%s) GROUP BY offer_id",
                              (offer_ids,)).fetchall()
    return {row["offer_id"]: row["visits"] for row in rows}


def browse_offers(connection, snapshot, filters, sort, categories, offset, limit, as_of):
    now = datetime.now(UTC)
    as_of = min(now, as_of) if as_of else now
    params = {"snapshot": snapshot, "categories": categories, "offset": offset, "limit": limit,
              "as_of": as_of, **filters}
    conditions = ["o.snapshot_id = %(snapshot)s"]
    if filters.get("in_stock"):
        conditions.append("o.available")
    if "price_min" in filters or "price_max" in filters:
        conditions.append("o.price > 0 AND coalesce(o.currency, 'RUB') IN ('RUB', 'RUR')")
    if "price_min" in filters:
        conditions.append("o.price >= %(price_min)s")
    if "price_max" in filters:
        conditions.append("o.price <= %(price_max)s")
    purchase = {key: filters[key] for key in LABELS if key in filters}
    if purchase:
        fields = {
            "platform": "(o.attributes ?| ARRAY['Платформа', 'Совместимая архитектура/платформа', 'Поддерживаемые ОС']"
                        " OR o.description LIKE '%%Платформа:%%')",
            "term": "o.attributes ? 'Срок действия'",
            "license_type": "(o.attributes ? 'Тип лицензии' OR o.description LIKE '%%Тип лицензии:%%')",
        }
        rows = connection.execute("""
            SELECT o.id, o.attributes, left(o.description, 1200) AS description
            FROM offer o WHERE """ + " AND ".join([*conditions, *(fields[key] for key in purchase)]), params)
        matching = []
        for row in rows:
            values = purchase_values(card_attributes(row))
            if all(value in values[key] for key, value in purchase.items()):
                matching.append(row["id"])
        params["licensed"] = matching
        conditions.append("o.id = ANY(%(licensed)s)")
    where = " AND ".join(conditions)
    counts = connection.execute("""
        SELECT c.id, c.name, count(*) AS offers
        FROM offer o LEFT JOIN category c ON c.snapshot_id = o.snapshot_id AND c.id = o.category_id
        WHERE """ + where + " GROUP BY c.id, c.name ORDER BY c.name, c.id", params).fetchall()
    total = sum(row["offers"] for row in counts if not categories or row["id"] in categories)
    popularity_join = ""
    if sort == "popularity":
        popularity_join = " LEFT JOIN (" + POPULARITY + """
            WHERE created_at BETWEEN %(as_of)s::timestamptz - interval '30 days' AND %(as_of)s
            GROUP BY offer_id
        ) p ON p.offer_id = o.id"""
    if categories:
        where += " AND o.category_id = ANY(%(categories)s)"
    rows = connection.execute("""
        SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor,
               o.url, o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description
        FROM offer o""" + popularity_join + " WHERE " + where + " ORDER BY " + SORT_ORDERS[sort]
        + " LIMIT %(limit)s OFFSET %(offset)s", params).fetchall() if offset < total else []
    return {
        "results": [as_card(row, "") for row in rows],
        "result_categories": [{"id": row["id"], "name": row["name"]} for row in counts if row["id"]],
        "total": total,
        "catalog_version": snapshot,
        "as_of": as_of,
        "applied_filters": filters,
    }
