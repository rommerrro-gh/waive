import re

VERSION = re.compile(r"(?<![\w.])\d+(?:\.\d+)+(?![\w.])")

SEARCH_OFFERS = """
    WITH input AS (
        SELECT lower(%(query)s) AS raw,
            plainto_tsquery('simple', %(query)s) ||
            plainto_tsquery('russian', %(query)s) AS keywords
    ), candidates AS (
        SELECT o.id, (o.id = i.raw OR lower(o.vendor_code) = i.raw) IS TRUE AS exact,
            ts_rank_cd(s.document, i.keywords) AS rank
        FROM offer o
        JOIN offer_search s ON (s.snapshot_id, s.offer_id) = (o.snapshot_id, o.id)
        CROSS JOIN input i
        WHERE o.snapshot_id = %(snapshot)s
            AND (s.document @@ i.keywords OR o.id = i.raw OR lower(o.vendor_code) = i.raw)
    )
    SELECT o.id AS offer_id, o.group_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, c.exact
    FROM candidates c
    JOIN offer o ON o.snapshot_id = %(snapshot)s AND o.id = c.id
    ORDER BY c.exact DESC, c.rank DESC, o.available DESC, o.id
    LIMIT 100
"""

FUZZY_OFFERS = """
    SELECT o.id AS offer_id, o.group_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, false AS exact
    FROM offer_search s
    JOIN offer o ON (o.snapshot_id, o.id) = (s.snapshot_id, s.offer_id)
    WHERE s.snapshot_id = %(snapshot)s AND lower(%(query)s) <%% s.name_normalized
    ORDER BY word_similarity(lower(%(query)s), s.name_normalized) DESC, o.available DESC, o.id
    LIMIT 100
"""


def matching_version(item, requested):
    if not requested:
        return True
    version_field = item["attributes"].get("Версия", "")
    recorded = set(VERSION.findall(version_field or item["name"]))
    return requested <= recorded


def search(connection, snapshot, query):
    params = {"snapshot": snapshot, "query": query}
    requested = set(VERSION.findall(query))
    candidates = connection.execute(SEARCH_OFFERS, params).fetchall()
    if not candidates and len(query) >= 4:
        candidates = connection.execute(FUZZY_OFFERS, params).fetchall()
    items = []
    for candidate in candidates:
        if not matching_version(candidate, requested):
            continue
        exact = candidate.pop("exact")
        candidate["price"] = candidate["price"] or None
        candidate["reason"] = "Совпадение артикула или ID" if exact else "Совпадение по каталогу"
        items.append(candidate)
        if len(items) == 4:
            break
    return items
