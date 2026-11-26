import re
from html import unescape

NORMALIZATIONS = (
    (re.compile(r"\bкрипто[ -]+про\b", re.IGNORECASE), "КриптоПро"),
    (re.compile(r"\bантивирь\b", re.IGNORECASE), "антивирус"),
    (re.compile(r"\bвинда\b", re.IGNORECASE), "Windows"),
)
REMOTE_CATEGORIES = {"327029", "327030"}
SERVER_CATEGORIES = {"86563", "87093", "87268", "87276"}
SERVER_REQUEST = re.compile(r"(?:(?:купить|подобрать)\s+)?сервер(?:ы)?", re.IGNORECASE)
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
    SELECT o.id AS offer_id, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, c.exact
    FROM candidates c
    JOIN offer o ON o.snapshot_id = %(snapshot)s AND o.id = c.id
    ORDER BY c.exact DESC, c.rank DESC, o.available DESC, o.id
    LIMIT 100
"""

FUZZY_OFFERS = """
    SELECT o.id AS offer_id, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, false AS exact
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


SEMANTIC_OFFERS = """
    WITH nearest AS MATERIALIZED (
        SELECT group_id, offer_id, embedding <=> %(embedding)s::vector AS distance
        FROM semantic_document
        WHERE snapshot_id = %(snapshot)s
        ORDER BY distance
        LIMIT 25
    )
    SELECT o.id AS offer_id, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, false AS exact
    FROM nearest n
    CROSS JOIN LATERAL (
        SELECT candidate.* FROM (
            (SELECT * FROM offer
             WHERE snapshot_id = %(snapshot)s AND group_id = n.group_id
             ORDER BY available DESC, id LIMIT 6)
            UNION ALL
            (SELECT * FROM offer WHERE snapshot_id = %(snapshot)s AND id = n.offer_id)
        ) candidate
    ) o
    WHERE n.distance <= 0.30
    ORDER BY n.distance, o.available DESC, o.id
"""

RELATED_OFFERS = """
    SELECT id AS offer_id, group_id, category_id, name, vendor, url,
        price, currency, available, attributes, left(description, 1200) AS description
    FROM offer
    WHERE snapshot_id = %(snapshot)s AND category_id = %(category)s AND available
        AND id <> ALL(%(excluded)s::text[])
        AND (%(group)s::text IS NULL OR group_id IS DISTINCT FROM %(group)s::text)
    ORDER BY (price > 0) DESC NULLS LAST, id
    LIMIT %(limit)s
"""

RECOMMENDATIONS = {
    "1145": ("650", "Защита компьютеров, на которых используется офисное ПО"),
    "86563": ("1033", "Резервное копирование данных серверной инфраструктуры"),
}


CATEGORY_CANDIDATES = """
    SELECT id AS offer_id, group_id, category_id, name, vendor, url,
        price, currency, available, attributes, left(description, 1200) AS description, false AS exact
    FROM offer
    WHERE snapshot_id = %(snapshot)s AND category_id = ANY(%(categories)s)
    ORDER BY available DESC,
        row_number() OVER (PARTITION BY group_id, CASE WHEN group_id IS NULL THEN id END
                           ORDER BY available DESC, (price > 0) DESC NULLS LAST, id),
        (price > 0) DESC NULLS LAST, id
    LIMIT 25
"""


def preferred_categories(query):
    if SERVER_REQUEST.fullmatch(query):
        return SERVER_CATEGORIES
    normalized = query.casefold().replace("ё", "е")
    if re.search(r"удаленн\w* доступ|удаленн\w* рабоч\w* стол", normalized) or (
        "из дома" in normalized and re.search(r"(подключ|зайти).*(компьютер|пк)", normalized)
    ):
        return REMOTE_CATEGORIES
    return set()


def normalize_query(query):
    for pattern, replacement in NORMALIZATIONS:
        query = pattern.sub(replacement, query)
    return query


def lexical_candidates(connection, snapshot, query):
    params = {"snapshot": snapshot, "query": normalize_query(query)}
    if SERVER_REQUEST.fullmatch(query):
        preferred = connection.execute(CATEGORY_CANDIDATES, {
            "snapshot": snapshot, "categories": sorted(SERVER_CATEGORIES),
        }).fetchall()
        if preferred:
            return preferred
    candidates = connection.execute(SEARCH_OFFERS, params).fetchall()
    if not candidates and len(query) >= 4:
        candidates = connection.execute(FUZZY_OFFERS, params).fetchall()
    categories = preferred_categories(query)
    if categories and not any(item["category_id"] in categories for item in candidates):
        candidates.extend(connection.execute(CATEGORY_CANDIDATES, {
            "snapshot": snapshot, "categories": sorted(categories),
        }).fetchall())
    return candidates


def merge_candidates(lexical, semantic, lexical_weight):
    scores = {}
    items = {}
    for weight, candidates in ((lexical_weight, lexical), (1.0, semantic)):
        family_rank = {}
        for rank, item in enumerate(candidates, start=1):
            identifier = item["offer_id"]
            family = item["group_id"] or identifier
            family_rank.setdefault(family, len(family_rank) + 1)
            scores[identifier] = scores.get(identifier, 0.0) + weight / (60 + family_rank[family])
            scores[identifier] += 0.0001 / rank
            items.setdefault(identifier, item)
    return sorted(items.values(), key=lambda item: (
        item["exact"], scores[item["offer_id"]], item["available"]
    ), reverse=True)


def as_card(item, reason):
    attributes = dict(item["attributes"])
    for field in unescape(item.get("description") or "").split("•"):
        label, separator, value = field.strip().partition(": ")
        if separator and label in {"Минимальная покупка", "Платформа"}:
            attributes.setdefault(label, value)
    return {key: value for key, value in {
        **item, "name": unescape(item["name"]), "attributes": attributes,
        "price": item["price"] or None, "reason": reason,
    }.items() if key not in {"exact", "description"}}


def matches_product_variant(item, query, versions):
    if item["exact"]:
        return True
    if not matching_version(item, versions):
        return False
    # CSP TLS использует CSP, но это отдельное расширение.
    if versions and re.search(r"\bcsp\b", query, re.IGNORECASE):
        tls_requested = bool(re.search(r"\btls\b", query, re.IGNORECASE))
        tls_product = bool(re.search(r"\bcsp\s+tls\b", item["name"], re.IGNORECASE))
        return tls_requested == tls_product
    return True


def assemble_results(connection, snapshot, query, lexical, semantic):
    versions = set(VERSION.findall(query))
    # Длинные формулировки чаще описывают задачу. Версия и артикул сохраняют приоритет.
    natural_language = len(query.split()) >= 5 and not versions
    merged = merge_candidates(lexical, semantic, 0.65 if natural_language else 1.3)
    categories = preferred_categories(query)
    if categories:
        merged.sort(key=lambda item: (
            item["exact"], item["category_id"] in categories,
        ), reverse=True)
    lexical_ids = {item["offer_id"] for item in lexical}
    results = [as_card(item, "Совпадение артикула или ID" if item["exact"] else (
        "Совпадение по каталогу" if item["offer_id"] in lexical_ids else "Похожее назначение продукта"
    )) for item in merged if matches_product_variant(item, query, versions)][:4]
    if not results:
        return results, [], []
    top = results[0]
    category = top["category_id"]
    params = {"snapshot": snapshot, "category": category,
              "excluded": [item["offer_id"] for item in results], "group": top["group_id"]}
    alternatives = []
    if len(results) < 4 and category:
        rows = connection.execute(RELATED_OFFERS, {**params, "limit": 4 - len(results)}).fetchall()
        alternatives = [as_card(row, "Другое семейство в той же категории") for row in rows]
    rule = RECOMMENDATIONS.get(category)
    recommendations = []
    if rule:
        target, reason = rule
        rows = connection.execute(RELATED_OFFERS, {**params, "category": target, "limit": 2}).fetchall()
        recommendations = [as_card(row, reason) for row in rows]
    return results, alternatives, recommendations
