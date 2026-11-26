import re
from html import unescape

from app.procurement import filter_offers, purchase_values

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
    ORDER BY c.exact DESC, row_number() OVER (
        PARTITION BY o.group_id, CASE WHEN o.group_id IS NULL THEN o.id END
        ORDER BY c.exact DESC, (o.available AND o.price > 0) DESC NULLS LAST, c.rank DESC, o.id
    ), c.rank DESC, o.available DESC, o.id
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

COMPLEMENT_CANDIDATES = """
    SELECT id AS offer_id, group_id, category_id, name, vendor, url,
        price, currency, available, attributes, left(description, 1200) AS description
    FROM offer
    WHERE snapshot_id = %(snapshot)s AND category_id = %(category)s
      AND (description ILIKE '%%Платформа:%%' OR attributes ? 'Платформа'
           OR attributes ? 'Совместимая архитектура/платформа')
    ORDER BY (available AND price > 0) DESC NULLS LAST, available DESC, id
    LIMIT 1200
"""

RECOMMENDATIONS = {
    "1145": ("650", "Защита рабочих станций"),
    "222323": ("650", "Защита рабочих станций"),
    **dict.fromkeys(SERVER_CATEGORIES, ("1033", "Резервное копирование серверов")),
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


def card_attributes(item):
    attributes = dict(item["attributes"])
    for field in unescape(item.get("description") or "").split("•"):
        label, separator, value = field.strip().partition(": ")
        if separator and label in {"Минимальная покупка", "Платформа", "Тип клиента", "Тип лицензии"}:
            attributes.setdefault(label, value)
    return attributes


def as_card(item, reason):
    return {key: value for key, value in {
        **item, "name": unescape(item["name"]), "attributes": card_attributes(item),
        "family_id": family_key(item),
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


def family_key(item):
    if item.get("group_id"):
        return "group:" + item["group_id"]
    return "product:" + normal_form(f'{item.get("vendor", "")} {item["name"]}')


def is_priced(item):
    return item["available"] and item["price"] is not None and item["price"] > 0


def select_families(candidates):
    chosen = {}
    for item in candidates:
        key = family_key(item)
        previous = chosen.get(key)
        if previous is None or (item["exact"], is_priced(item)) > (previous["exact"], is_priced(previous)):
            chosen[key] = item
    return sorted(chosen.values(), key=lambda item: (item["exact"], is_priced(item)), reverse=True)


def platform_labels(value):
    labels = re.findall(
        r"windows(?:\s+server(?:\s+20\d{2})?|\s+(?:10|11))?|"
        r"astra linux|alt linux|ubuntu|debian|centos|red hat enterprise linux|"
        r"linux|(?:apple )?mac ?os(?: x)?|android|ios|freebsd|vmware esxi",
        normal_form(value),
    )
    return {re.sub(r"^(?:apple )?mac ?os(?: x)?$", "macos", label) for label in labels}


def platform_values(card):
    fields = ("Платформа", "Совместимая архитектура/платформа", "Поддерживаемые ОС")
    labels = set()
    for field in fields:
        labels.update(platform_labels(card["attributes"].get(field) or ""))
    return labels


def buyer_type(card):
    value = normal_form(card["attributes"].get("Тип клиента") or "")
    return value if value in {"физлицо", "юрлицо"} else None


def compatibility(anchor, offer, query, category, platform=None):
    license_type = offer["attributes"].get("Тип лицензии") or ""
    if re.search(r"продлен|обновлен|renewal|upgrade", normal_form(offer["name"] + " " + license_type)):
        return None
    required = platform_labels(query) or platform_values(anchor)
    if platform:
        shared = {platform.replace("-", " ")} if platform in purchase_values(offer["attributes"])["platform"] else set()
    else:
        shared = required & platform_values(offer)
    if anchor["category_id"] in SERVER_CATEGORIES:
        shared = {label for label in shared if label.startswith("windows server")
                  or label in {"astra linux", "alt linux", "ubuntu", "debian", "centos",
                               "red hat enterprise linux", "linux", "freebsd", "vmware esxi"}}
    elif category == "650" and not re.search(
        r"endpoint|workstation|desktop|для (?:рабочих станций|компьютеров|офиса)|small office",
        offer["name"], re.IGNORECASE,
    ):
        return None
    source_buyer, target_buyer = buyer_type(anchor), buyer_type(offer)
    if not shared or (source_buyer and target_buyer and source_buyer != target_buyer):
        return None
    return {
        "matched_platforms": sorted(shared),
        "source_offer_id": anchor["offer_id"],
        "buyer_type_checked": bool(source_buyer and target_buyer),
    }


def fill_recommendations(connection, snapshot, query, results, platform=None):
    if not results or len(results) >= 4:
        return []
    anchor = results[0]
    rule = RECOMMENDATIONS.get(anchor["category_id"])
    if not rule or not (platform or platform_labels(query) or platform_values(anchor)):
        return []
    target, reason = rule
    taken = {card["family_id"] for card in results}
    rows = connection.execute(COMPLEMENT_CANDIDATES, {
        "snapshot": snapshot, "category": target,
    })
    recommendations = []
    for row in rows:
        card = as_card(row, reason)
        if card["family_id"] in taken:
            continue
        proof = compatibility(anchor, card, query, target, platform)
        if proof is None:
            continue
        card["reason"] = reason + ". Совпадение платформы: " + ", ".join(proof["matched_platforms"])
        card["compatibility"] = proof
        card["match_type"] = "recommendation"
        recommendations.append(card)
        taken.add(card["family_id"])
        if len(results) + len(recommendations) == 4:
            break
    return recommendations


def purchase_family_offers(connection, snapshot, matching, query, versions):
    families = list(dict.fromkeys(item["group_id"] for item in matching if item["group_id"]))
    if any(item["exact"] for item in matching):
        return [item for item in matching if item["exact"]]
    if not families:
        return matching
    rows = connection.execute("""
        SELECT id AS offer_id, group_id, category_id, name, vendor, url, price, currency,
               available, attributes, left(description, 1200) AS description, false AS exact
        FROM offer WHERE snapshot_id = %s AND group_id = ANY(%s)
        ORDER BY available DESC, id
    """, (snapshot, families))
    positions = {key: index for index, key in enumerate(dict.fromkeys(map(family_key, matching)))}
    seen = {item["offer_id"]: item for item in matching}
    for item in rows:
        if matches_product_variant(item, query, versions):
            seen.setdefault(item["offer_id"], item)
    return sorted(seen.values(), key=lambda item: positions[family_key(item)])


def assemble_results(connection, snapshot, query, lexical, semantic, filters=None, diagnostics=None):
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
    if filters:
        matching = purchase_family_offers(connection, snapshot, matching, query, versions)
        matching, counts = filter_offers(matching, filters, family_key, card_attributes)
        if diagnostics is not None:
            diagnostics.update(counts)
    elif diagnostics is not None:
        count = len({family_key(item) for item in matching})
        diagnostics.update(families_before=count, families_after=count, conditions={})
    lexical_ids = {item["offer_id"] for item in lexical}
    results = [as_card(item, "Совпадение артикула или ID" if item["exact"] else (
        "Совпадение по каталогу" if item["offer_id"] in lexical_ids else "Похожее назначение продукта"
    )) for item in select_families(matching)[:4]]
    for card in results:
        card["match_type"] = "direct"
    recommendations = fill_recommendations(connection, snapshot, query, results, (filters or {}).get("platform"))
    return results, [], recommendations


SUGGEST_FAMILIES = """
    WITH input AS (
        SELECT replace(lower(%(query)s), ' ', '') AS prefix
    ), candidates AS (
        SELECT c.id, c.name, strpos(replace(lower(c.name), ' ', ''), i.prefix) AS position
        FROM category c CROSS JOIN input i
        WHERE c.snapshot_id = %(snapshot)s
          AND strpos(replace(lower(c.name), ' ', ''), i.prefix) > 0
          AND EXISTS (
              SELECT 1 FROM offer o
              WHERE o.snapshot_id = c.snapshot_id AND o.group_id = c.id AND o.available
          )
    )
    SELECT name FROM candidates
    GROUP BY name
    ORDER BY (min(position) = 1) DESC, min(position), length(name), name
    LIMIT 6
"""


def suggest_families(connection, snapshot, query):
    rows = connection.execute(SUGGEST_FAMILIES, {
        "query": normalize_query(query), "snapshot": snapshot,
    }).fetchall()
    return [unescape(row["name"]) for row in rows]
