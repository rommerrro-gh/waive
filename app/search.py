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
    WITH input AS MATERIALIZED (
        SELECT lower(%(query)s) AS raw,
            plainto_tsquery('simple', %(query)s) ||
            plainto_tsquery('russian', %(query)s) AS keywords
    ), matches AS (
        SELECT s.offer_id AS id, false AS exact, ts_rank_cd(s.document, i.keywords) AS rank
        FROM input i JOIN offer_search s ON s.document @@ i.keywords
        WHERE s.snapshot_id = %(snapshot)s
        UNION ALL
        SELECT o.id, true, 0::real
        FROM input i JOIN offer o ON o.id = i.raw
        WHERE o.snapshot_id = %(snapshot)s
        UNION ALL
        SELECT o.id, true, 0::real
        FROM input i JOIN offer o ON lower(o.vendor_code) = i.raw
        WHERE o.snapshot_id = %(snapshot)s
    ), candidates AS (
        SELECT id, bool_or(exact) AS exact, max(rank) AS rank
        FROM matches
        GROUP BY id
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


NEARBY_FAMILIES = """
    SELECT c.id, c.name
    FROM category c
    WHERE c.snapshot_id = %(snapshot)s
      AND EXISTS (
          SELECT 1 FROM offer o WHERE o.snapshot_id = c.snapshot_id AND o.group_id = c.id
      )
    ORDER BY lower(c.name) <-> lower(%(query)s)
    LIMIT 24
"""

FAMILY_OFFERS = """
    SELECT o.id AS offer_id, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description,
        false AS exact
    FROM unnest(%(families)s::text[]) WITH ORDINALITY AS family(id, rank)
    CROSS JOIN LATERAL (
        SELECT * FROM offer
        WHERE snapshot_id = %(snapshot)s AND group_id = family.id
        ORDER BY available DESC, id
        LIMIT 12
    ) o
    ORDER BY family.rank, o.available DESC, o.id
"""


def normal_form(text):
    return " ".join(unescape(text).casefold().replace("ё", "е").split())


def one_edit_title(query, title):
    query, title = normal_form(query), normal_form(title)
    if re.findall(r"\d+", query) != re.findall(r"\d+", title):
        return False
    if len(query) == len(title):
        mismatches = [i for i, (left, right) in enumerate(zip(query, title)) if left != right]
        if len(mismatches) <= 1:
            return True
        if len(mismatches) != 2:
            return False
        first, second = mismatches
        return second == first + 1 and query[first:second + 1] == title[first:second + 1][::-1]
    if abs(len(query) - len(title)) != 1:
        return False
    shorter, longer = sorted((query, title), key=len)
    difference = next((i for i, (left, right) in enumerate(zip(shorter, longer)) if left != right), len(shorter))
    return shorter[difference:] == longer[difference + 1:]


def typo_candidates(connection, params):
    families = connection.execute(NEARBY_FAMILIES, params).fetchall()
    matching = [row["id"] for row in families if one_edit_title(params["query"], row["name"])]
    if matching:
        return connection.execute(FAMILY_OFFERS, {**params, "families": matching[:4]}).fetchall()
    # Поддерживаем прежний поиск по похожим названиям отдельных предложений.
    return connection.execute(FUZZY_OFFERS, params).fetchall()




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
    WITH cheapest AS (
        SELECT id, available, price, row_number() OVER (
            PARTITION BY group_id, CASE WHEN group_id IS NULL THEN id END
            ORDER BY available DESC, (price > 0) DESC NULLS LAST, id
        ) AS family_position
        FROM offer
        WHERE snapshot_id = %(snapshot)s AND category_id = ANY(%(categories)s)
    ), selected AS MATERIALIZED (
        SELECT id, available, price, family_position FROM cheapest
        ORDER BY available DESC, family_position, (price > 0) DESC NULLS LAST, id
        LIMIT 25
    )
    SELECT o.id AS offer_id, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, false AS exact
    FROM selected s JOIN offer o ON o.id = s.id AND o.snapshot_id = %(snapshot)s
    ORDER BY s.available DESC, s.family_position, (s.price > 0) DESC NULLS LAST, s.id
"""


def preferred_categories(query):
    if SERVER_REQUEST.fullmatch(query):
        return SERVER_CATEGORIES
    normalized = query.casefold().replace("ё", "е")
    remote = re.search(r"удаленн\w* доступ|удаленн\w* рабоч\w* стол", normalized)
    away = re.search(r"из дома|командировк|из друг\w* город|удаленн", normalized)
    computer = re.search(r"компьютер|\bпк\b|рабоч\w* станци", normalized)
    if remote or (away and computer and re.search(r"подключ|зайти|работат|сеанс|доступ", normalized)):
        return REMOTE_CATEGORIES
    if re.search(r"восстанов|вернут|достать|реанимир", normalized) and re.search(
        r"файл|данн|форматирован|удален|накопител|флешк|жестк\w* диск", normalized
    ):
        return {"5922"}
    if re.search(r"резервн\w* копи|б[эе]кап|\bbackup\b", normalized) or (
        re.search(r"сохрани|сохран[яе]|сделат|созда", normalized)
        and re.search(r"копи[юи]|копирован|верси", normalized)
        and re.search(r"файл|данн|документ|папк", normalized)
    ):
        return {"1033"}
    if re.search(r"антивирус|антивирь|защит.*вирус", normalized):
        return {"3775"} if re.search(r"домашн|личн\w* (?:пк|компьютер)", normalized) else {"650"}
    if re.search(r"защит", normalized) and re.search(r"рабоч\w* станци|сотрудник", normalized):
        return {"650"}
    if re.search(r"стойк|стоечн|rack", normalized) and re.search(
        r"\bсервер\b|сервер[ыа]\b|желез|оборудован", normalized
    ):
        return {"86563"}
    office_documents = re.search(r"документ|текст", normalized) and re.search(r"таблиц", normalized)
    if re.search(r"российск", normalized) and re.search(r"офисн\w* (?:пакет|по)", normalized):
        return {"222323"}
    if office_documents and re.search(r"редактор|редактир|офисн\w* пакет|работат|созда", normalized):
        return {"1145", "222323"}
    if re.search(r"гипервизор|виртуальн\w* машин", normalized) and re.search(
        r"гипервизор|запуск|запуст|созда|разверн|одновременно", normalized
    ):
        return {"2500"}
    if re.search(r"почтов\w* клиент", normalized) or (
        re.search(r"электронн\w* почт|почтов\w* ящик", normalized)
        and re.search(r"клиент|настольн|приложени|открыва", normalized)
    ):
        return {"1267"}
    if re.search(r"операционн\w* систем", normalized):
        if re.search(r"\bwindows\b|винда", normalized):
            return {"2050"}
        if re.search(r"российск|отечественн", normalized):
            return {"222320"}
    if re.search(r"огранич|управля|настраив", normalized) and re.search(
        r"доступ.*(?:ресурс|сотрудник)|права.*(?:сотрудник|команд|пользовател)", normalized
    ):
        return {"4742"}
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
    categories = preferred_categories(query)
    if not candidates and not categories and 4 <= len(query) <= 110 and len(query.split()) <= 12:
        candidates = typo_candidates(connection, params)
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


def diversify(candidates):
    families = set()
    first, variants = [], []
    for item in candidates:
        family = item["group_id"] or item["offer_id"]
        if item["exact"] or family not in families:
            first.append(item)
        else:
            variants.append(item)
        families.add(family)
    return first + variants


def assemble_results(connection, snapshot, query, lexical, semantic):
    versions = set(VERSION.findall(query))
    # Длинные формулировки чаще описывают задачу. Версия и артикул сохраняют приоритет.
    natural_language = len(query.split()) >= 5 and not versions
    merged = merge_candidates(lexical, semantic, 0.65 if natural_language else 1.3)
    categories = preferred_categories(query)
    matching = [item for item in merged if matches_product_variant(item, query, versions)]
    if categories:
        preferred = [item for item in matching if item["exact"] or item["category_id"] in categories]
        if preferred:
            matching = preferred
    if (natural_language or categories) and not versions:
        matching = diversify(matching)
    lexical_ids = {item["offer_id"] for item in lexical}
    results = [as_card(item, "Совпадение артикула или ID" if item["exact"] else (
        "Совпадение по каталогу" if item["offer_id"] in lexical_ids else "Похожее назначение продукта"
    )) for item in matching[:4]]
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
