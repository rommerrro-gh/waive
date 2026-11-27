import re
from dataclasses import dataclass, field
from html import unescape

from app.procurement import filter_offers, positive_query
from app.relations import alternative_difference, category_coverage, comparable_offer, compatibility_evidence
from app.requirements import catalog_os, check_requirement, parse_requirements, unmet_requirements
from app.spelling import clear_winner, rank_spellings

NORMALIZATIONS = (
    (re.compile(r"\bкрипто[ -]+про\b", re.IGNORECASE), "КриптоПро"),
    (re.compile(r"\bкасперск(?:ий|ого|ому|им|ом|и)\b", re.IGNORECASE), "Kaspersky"),
    (re.compile(r"\bантивирь\b", re.IGNORECASE), "антивирус"),
    (re.compile(r"\bвинда\b", re.IGNORECASE), "Windows"),
)
SEARCH_ALIASES = (
    (re.compile(r"\bвпн\b", re.I), "VPN"),
    (re.compile(r"\bвиртуальн(?:ая|ую|ой|ые|ых)\s+частн(?:ая|ую|ой|ые|ых)\s+сет(?:ь|и|ей)\b", re.I), "VPN"),
    (re.compile(r"\bmax[ -]+patrol\b", re.I), "MaxPatrol"),
)
REMOTE_CATEGORIES = {"327029", "327030"}
SERVER_CATEGORIES = {"86563", "87093", "87268", "87276"}
SERVER_REQUEST = re.compile(r"(?:(?:купить|подобрать)\s+)?сервер(?:ы)?", re.IGNORECASE)
HARDWARE_REQUESTS = (
    (re.compile(r"ноутбук(?:и)?"), {"70824"}),
    (re.compile(r"монитор(?:ы)?"), {"80843"}),
    (re.compile(r"принтер(?:ы)?|мфу"), {"91774", "93995"}),
    (re.compile(r"коммутатор(?:ы)?"), {"85741"}),
)
WORKSTATION_SECURITY_REQUEST = re.compile(r"антивирус\w*.*рабоч\w+\s+станци", re.I)
SPECIALIZED_RECOVERY = re.compile(r"\b(?:sql|postgresql|exchange|outlook|pst|edb|почтов\w*|баз[аыуе]? данных)\b", re.I)
ASTRA_PRODUCT = re.compile(r"(?:астра\s+линукс|astra\s+linux)(?:\s+(?:(?:common|special)\s+edition|ce|se))?", re.I)
WINDOWS_PRODUCT = re.compile(r"windows(?:\s+(?:10|11|server(?:\s+20\d{2})?))?", re.I)
OS_PRODUCT_PREFIX = r"^(?:(?:ос|операционная система|лицензия|microsoft)\s+)*"
VPN_REQUEST = re.compile(r"^(?:(?:купить|подобрать|нуж(?:ен|на)|корпоративн\w+|защищенн\w+)\s+)*vpn\b", re.I)
VPN_BROWSE = re.compile(VPN_REQUEST.pattern + r"(?:\s+для\s+(?:компани\w+|бизнеса|офиса|организаци\w+|дома))?\s*", re.I)
VPN_IN_TITLE = re.compile(r"\b(?:vpn|openvpn|lirvpn|wireguard|l[23]vpn|ipsec|vipnet\s+(?:client|coordinator))\b", re.I)
VPN_EXCLUDED_OFFER = re.compile(r"\b(?:without|без|no)\s+vpn\b|\b(?:формуляр|документация|установочный комплект)\b", re.I)
VERSION = re.compile(r"(?<![\w.])\d+(?:\.\d+)+(?![\w.])")
KASPERSKY_NAMES = ("kaspersky", "касперский", "касперски")
KASPERSKY_VENDOR_NAMES = {"лаборатория касперского", "kaspersky", "kaspersky lab"}
SUGGESTION_ALIASES = {
    "Kaspersky": KASPERSKY_NAMES,
    "Microsoft": ("майкрософт", "микрософт"),
    "Dr.Web": ("доктор веб", "докторвеб", "др веб", "др. веб", "dr web", "drweb"),
    "AnyDesk": ("any desk", "анидеск", "анидэск", "энидеск", "энидэск", "ани деск", "эни деск"),
    "WinRAR": ("винрар", "вин рар"),
    "Astra": ("астра",),
    "Astra Linux": ("астра линукс", "астра linux", "астралинукс"),
    "Altova": ("алтова", "альтова"),
    "PRO32": ("про32", "про 32"),
    "Logitech": ("логитек", "лоджитек"),
    "Adobe": ("адоб", "адобе", "адоби", "эдоб", "эдобе", "эдоби"),
    "Autodesk": ("автодеск", "аутодеск"),
    "VMware": ("вмвар", "вмваре", "вмвэр"),
    "ESET": ("есет", "эсет"),
}
SPELLING_ALIASES = {**SUGGESTION_ALIASES, "КриптоПро": ("крипто про", "cryptopro")}
SPELLING_BRANDS = sorted(SPELLING_ALIASES, key=len, reverse=True)
BRAND_BY_NAME = {
    name: canonical
    for canonical, aliases in SUGGESTION_ALIASES.items()
    for name in (*aliases, canonical.casefold())
}
LEADING_BRAND = re.compile(
    r"^(?:" + "|".join(map(re.escape, sorted(BRAND_BY_NAME, key=len, reverse=True))) + r")(?=\s)",
    re.IGNORECASE,
)
LATIN_KEYS = "qwertyuiopasdfghjklzxcvbnm"
CYRILLIC_KEYS = "йцукенгшщзфывапролдячсмить"
TO_RUSSIAN = str.maketrans(LATIN_KEYS, CYRILLIC_KEYS)
TO_ENGLISH = str.maketrans(CYRILLIC_KEYS, LATIN_KEYS)

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
    SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, c.exact
    FROM candidates c
    JOIN offer o ON o.snapshot_id = %(snapshot)s AND o.id = c.id
    ORDER BY c.exact DESC, row_number() OVER (
        PARTITION BY o.group_id, CASE WHEN o.group_id IS NULL THEN o.id END
        ORDER BY c.exact DESC, (o.available AND o.price > 0) DESC NULLS LAST, c.rank DESC, o.id
    ), c.rank DESC, o.available DESC, o.id
    LIMIT 100
"""

VPN_OFFERS = """
    SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor, o.url,
           o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, false AS exact
    FROM offer_search s JOIN offer o ON (o.snapshot_id, o.id) = (s.snapshot_id, s.offer_id)
    WHERE s.snapshot_id = %(snapshot)s AND (
        s.name_normalized LIKE '%%vpn%%' OR s.name_normalized LIKE '%%vipnet client%%'
        OR s.name_normalized LIKE '%%vipnet coordinator%%' OR s.name_normalized LIKE '%%wireguard%%'
    )
    ORDER BY o.available DESC, o.id
    LIMIT 300
"""

FUZZY_OFFERS = """
    SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor, o.url,
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
    SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor, o.url,
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


def is_kaspersky_query(query):
    name = normal_form(query)
    if name in {"каспер", "лаборатория касперского"} or normalize_query(name) == "Kaspersky":
        return True
    return name.isalpha() and any(one_edit_title(name, brand) for brand in KASPERSKY_NAMES)


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
    SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor, o.url,
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
    SELECT o.id AS offer_id, o.vendor_code, o.group_id, o.category_id, o.name, o.vendor, o.url,
        o.price, o.currency, o.available, o.attributes, left(o.description, 1200) AS description, false AS exact
    FROM selected s JOIN offer o ON o.id = s.id AND o.snapshot_id = %(snapshot)s
    ORDER BY s.available DESC, s.family_position, (s.price > 0) DESC NULLS LAST, s.id
"""


def preferred_categories(query):
    if SERVER_REQUEST.fullmatch(query):
        return SERVER_CATEGORIES
    normalized = query.casefold().replace("ё", "е")
    hardware = re.sub(r"^(?:купить|подобрать|найти)\s+", "", normalized)
    for pattern, categories in HARDWARE_REQUESTS:
        if pattern.fullmatch(hardware):
            return categories
    if ASTRA_PRODUCT.fullmatch(normalized):
        return {"222320"}
    if WINDOWS_PRODUCT.fullmatch(normalized):
        return {"2050"}
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


def vpn_requested(query):
    return bool(VPN_REQUEST.match(query) or (LEADING_BRAND.match(query) and re.search(r"\bvpn\s*$", query, re.I)))


def vendor_matches(item, brand):
    vendor = normal_form(item.get("vendor") or "").strip("«»\" ")
    if brand == "Kaspersky":
        return vendor in KASPERSKY_VENDOR_NAMES
    aliases = (brand, *SPELLING_ALIASES.get(brand, ()))
    if brand == "Astra Linux":
        aliases += ("астра",)
    return any(re.search(r"(?<!\w)" + re.escape(normal_form(alias)) + r"(?!\w)", vendor) for alias in aliases)


def vpn_offer(item, query):
    name = normal_form(item["name"])
    if not VPN_IN_TITLE.search(name) or VPN_EXCLUDED_OFFER.search(name):
        return False
    leading = LEADING_BRAND.match(query)
    if not leading:
        return True
    brand = BRAND_BY_NAME[normal_form(leading[0])]
    return vendor_matches(item, brand)


def matches_requested_product(item, intent):
    if item["exact"]:
        return True
    requested = normal_form(intent)
    if ASTRA_PRODUCT.fullmatch(requested):
        return bool(re.match(OS_PRODUCT_PREFIX + r"(?:astra linux|астра линукс)\b", normal_form(item["name"])))
    if WINDOWS_PRODUCT.fullmatch(requested):
        return bool(re.match(OS_PRODUCT_PREFIX + re.escape(requested) + r"\b", normal_form(item["name"])))
    return True


def normalize_query(query):
    for pattern, replacement in NORMALIZATIONS:
        query = pattern.sub(replacement, query)
    return query


def lexical_candidates(connection, snapshot, query, *, allow_fuzzy=True):
    params = {"snapshot": snapshot, "query": normalize_query(query)}
    if VPN_BROWSE.fullmatch(query):
        return [item for item in connection.execute(VPN_OFFERS, params) if vpn_offer(item, query)]
    if normal_form(query) == "kaspersky":
        candidates = connection.execute(SEARCH_OFFERS, {**params, "query": "Kaspersky"}).fetchall()
        return [
            item for item in candidates
            if normal_form(item["vendor"] or "").strip("«»\" ") in KASPERSKY_VENDOR_NAMES
        ]
    if SERVER_REQUEST.fullmatch(query):
        preferred = connection.execute(CATEGORY_CANDIDATES, {
            "snapshot": snapshot, "categories": sorted(SERVER_CATEGORIES),
        }).fetchall()
        if preferred:
            return preferred
    candidates = connection.execute(SEARCH_OFFERS, params).fetchall()
    if vpn_requested(query):
        return [item for item in candidates if vpn_offer(item, query)]
    categories = preferred_categories(query)
    if allow_fuzzy and not candidates and not categories and 4 <= len(query) <= 110 and len(query.split()) <= 12:
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


def buyer_type(card):
    value = normal_form(card["attributes"].get("Тип клиента") or "")
    return value if value in {"физлицо", "юрлицо"} else None


def workstation_security(offer):
    return re.search(
        r"endpoint|workstation|desktop|для (?:рабочих станций|компьютеров|офиса)|small office",
        offer["name"], re.IGNORECASE,
    )


def load_category_coverage(connection, snapshot):
    categories = {row["id"]: (row["name"], row["parent_id"]) for row in connection.execute(
        "SELECT id, name, parent_id FROM category WHERE snapshot_id = %s", (snapshot,),
    )}
    used = {row["category_id"] for row in connection.execute(
        "SELECT DISTINCT category_id FROM offer WHERE snapshot_id = %s AND category_id IS NOT NULL", (snapshot,),
    )}
    return category_coverage(categories, used)


def bundle_available(card, coverage):
    return card["available"] and bool(coverage.get(card["category_id"]))


def recommend_for_anchor(connection, snapshot, anchor, coverage, requirements=(), limit=3, taken=()):
    relations = coverage.get(anchor["category_id"], ())
    if not relations:
        return [], "not_applicable"
    # Версия и редакция основного продукта не задают номер версии самостоятельного дополнения.
    applicable = [requirement for requirement in requirements if requirement.key not in {"edition", "version", "term"}]
    explicit_os = [label for requirement in applicable if requirement.mandatory and requirement.key in {"os", "os_any"}
                   for label in ((requirement.value,) if requirement.key == "os" else requirement.value)]
    unique = {anchor["family_id"], *taken}
    pools = []
    for relation, targets in relations:
        if relation.check == "endpoint_backup" and not workstation_security(anchor):
            continue
        needs_platform = relation.check in {"platform", "server", "endpoint_backup"}
        if needs_platform and not (explicit_os or catalog_os(anchor, anchor["attributes"])):
            continue
        rows = connection.execute("""
            SELECT id AS offer_id, vendor_code, group_id, category_id, name, vendor, url,
                   price, currency, available, attributes, left(description, 1200) AS description
            FROM offer WHERE snapshot_id = %s AND available AND category_id = ANY(%s)
              AND (NOT %s OR attributes ?| ARRAY['Платформа', 'Совместимая архитектура/платформа', 'Поддерживаемые ОС']
                   OR description LIKE '%%Платформа:%%')
            ORDER BY (price > 0) DESC NULLS LAST, id LIMIT 1200
        """, (snapshot, list(targets), needs_platform))
        relation_items = []
        for row in rows:
            card = as_card(row, "")
            if card["family_id"] in unique or unmet_requirements(row, card["attributes"], applicable):
                continue
            if row["category_id"] == "650" and not workstation_security(row):
                continue
            license_text = normal_form(row["name"] + " " + (card["attributes"].get("Тип лицензии") or ""))
            if re.search(r"продлен|обновлен|renewal|upgrade", license_text):
                continue
            left, right = buyer_type(anchor), buyer_type(card)
            if left and right and left != right:
                continue
            proof = compatibility_evidence(anchor, card, relation.check, explicit_os)
            if not proof:
                continue
            card.update(reason=relation.label + ". " + ". ".join(proof["evidence"]),
                        compatibility={**proof, "source_offer_id": anchor["offer_id"]},
                        match_type="recommendation")
            relation_items.append(card)
            unique.add(card["family_id"])
            if len(relation_items) == limit:
                break
        if relation_items:
            pools.append(relation_items)
        if len(pools) == limit:
            break
    items = [pool[index] for index in range(limit) for pool in pools if len(pool) > index][:limit]
    return items, "confirmed" if items else "needs_confirmation"


def purchase_family_offers(connection, snapshot, matching, query, versions):
    families = list(dict.fromkeys(item["group_id"] for item in matching if item["group_id"]))
    if any(item["exact"] for item in matching):
        return [item for item in matching if item["exact"]]
    if not families:
        return matching
    rows = connection.execute("""
        SELECT id AS offer_id, vendor_code, group_id, category_id, name, vendor, url, price, currency,
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


def named_families(connection, snapshot, intent, lexical):
    """A confirmed catalog family constrains the scope of a product-name search."""
    query = normal_form(intent)
    if len(query) < 4 or not lexical or any(item["exact"] for item in lexical):
        return set()
    group_ids = sorted({item["group_id"] for item in lexical if item["group_id"]})
    if not group_ids:
        return set()

    def family_name(value):
        return re.sub(r"^(?:программа|лицензия на)\s+", "", normal_form(value))

    query = family_name(query)
    names = [(row["id"], family_name(row["name"])) for row in connection.execute("""
        SELECT id, name FROM category WHERE snapshot_id = %s AND id = ANY(%s)
    """, (snapshot, group_ids))]
    exact = {identifier for identifier, name in names if name == query}
    if exact:
        return exact
    if len(query.split()) >= 2 and not re.search(r"\b20\d{2}\b", query):
        return {identifier for identifier, name in names
                if " ".join(re.sub(r"\b20\d{2}\b", " ", name).split()) == query}
    return set()


def assemble_results(connection, snapshot, query, lexical, semantic, filters=None, diagnostics=None, *,
                     expanded=False, coverage=None, intent_query=None):
    versions = set(VERSION.findall(query))
    # Длинные формулировки чаще описывают задачу. Версия и артикул сохраняют приоритет.
    natural_language = len(query.split()) >= 5 and not versions
    merged = merge_candidates(lexical, semantic, 0.65 if natural_language else 1.3)
    intent = intent_query or query
    vpn = vpn_requested(positive_query(query))
    categories = preferred_categories(intent)
    requirements = parse_requirements(query, filters)
    general_file_recovery = categories == {"5922"} and not SPECIALIZED_RECOVERY.search(query)
    families = set() if vpn or categories else named_families(connection, snapshot, intent, lexical)
    brand = (BRAND_BY_NAME.get(normal_form(intent)) if not categories and not families and not vpn else None)
    use_vendor = brand and any(vendor_matches(item, brand) for item in lexical)
    brand_in_title = re.compile(r"(?<!\w)" + re.escape(normal_form(brand)) + r"(?!\w)") if brand and not use_vendor else None
    workstation = WORKSTATION_SECURITY_REQUEST.search(query)

    def matches_purpose(item):
        return (matches_requested_product(item, intent)
                and (not workstation or workstation_security(item))
                and (not general_file_recovery or not SPECIALIZED_RECOVERY.search(item["name"]))
                and (not vpn or vpn_offer(item, query)))

    def matches(item):
        if item["exact"]:
            return True
        return (matches_purpose(item)
                and (not categories or item["category_id"] in categories)
                and (not families or item["group_id"] in families)
                and (not use_vendor or vendor_matches(item, brand))
                and (not brand_in_title or brand_in_title.search(normal_form(item["name"]))))

    matching = [item for item in merged if matches_product_variant(item, query, versions)
                and matches(item)]
    if filters or requirements:
        matching = [item for item in purchase_family_offers(connection, snapshot, matching, query, versions)
                    if matches(item)]
        matching, counts = filter_offers(matching, filters or {}, family_key, card_attributes)
        unmatched = {requirement.label for requirement in requirements if requirement.mandatory} if not matching else set()
        eligible = []
        for item in matching:
            failures = unmet_requirements(item, card_attributes(item), requirements)
            if failures:
                unmatched.update(requirement.label for requirement, _ in failures)
            else:
                eligible.append(item)
        matching = eligible
        if diagnostics is not None:
            diagnostics.update(counts)
            diagnostics["requirements_unmet"] = sorted(unmatched)
            diagnostics["families_after"] = len({family_key(item) for item in matching})
    elif diagnostics is not None:
        count = len({family_key(item) for item in matching})
        diagnostics.update(families_before=count, families_after=count, conditions={})
    lexical_ids = {item["offer_id"] for item in lexical}
    ranked = select_families(matching)
    if vpn:
        available = [item for item in ranked if item["available"]]
        if available:
            ranked = available
        ranked.sort(key=lambda item: (item["exact"], item["available"],
                                      not re.search(r"\b(?:own|with|с) vpn\b", item["name"], re.I)), reverse=True)
    preferences = [requirement for requirement in requirements if not requirement.mandatory]
    if preferences:
        ranked.sort(key=lambda item: (item["exact"], sum(
            check_requirement(item, card_attributes(item), requirement) is True for requirement in preferences
        )), reverse=True)
    results = [as_card(item, "Совпадение артикула или ID" if item["exact"] else (
        "Совпадение по каталогу" if item["offer_id"] in lexical_ids else "Похожее назначение продукта"
    )) for item in (ranked if expanded else ranked[:4])]
    for card in results:
        card["match_type"] = "direct"
        card["bundle_available"] = bundle_available(card, coverage or {})
    alternatives = find_alternatives(
        connection, snapshot, results, lexical, semantic, categories, requirements, matches_purpose,
    )
    # В top4 оставляем дополнения на свободных местах. Подбор по выбранному товару независим от размера выдачи.
    free = 4 - len(results) - len(alternatives)
    recommendations = []
    if not vpn and coverage and results and free > 0 and results[0]["available"]:
        recommendations, _ = recommend_for_anchor(
            connection, snapshot, results[0], coverage, requirements, free,
            (card["family_id"] for card in (*results, *alternatives)),
        )
    return results, alternatives, recommendations


def find_alternatives(connection, snapshot, results, lexical, semantic, categories, requirements, matches):
    if any(card["available"] for card in results):
        return []
    exact = next((item for item in lexical if item["exact"]), None)
    unavailable = next((row for row in lexical if results and row["offer_id"] == results[0]["offer_id"]), None)
    reference = exact or unavailable
    if reference and reference["category_id"]:
        categories = {reference["category_id"]}
    if not categories:
        return []
    candidates = [item for item in semantic if item["category_id"] in categories]
    candidates.extend(connection.execute(CATEGORY_CANDIDATES, {"snapshot": snapshot, "categories": sorted(categories)}))
    taken = {card["family_id"] for card in results}
    anchor = as_card(reference, "") if reference else None
    if anchor:
        taken.add(anchor["family_id"])
    output = []
    for item in candidates:
        family = family_key(item)
        if not item["available"] or family in taken or not matches(item):
            continue
        card = as_card(item, "")
        if unmet_requirements(item, card["attributes"], requirements) or (anchor and not comparable_offer(anchor, card)):
            continue
        reason = "То же назначение по категории каталога"
        if anchor and (difference := alternative_difference(anchor, card)):
            reason += ". " + unescape(difference)
        output.append({**card, "reason": reason, "match_type": "alternative", "bundle_available": False})
        taken.add(family)
        if len(output) == 4:
            break
    return output


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

SUGGEST_UNGROUPED = """
    SELECT o.name
    FROM offer_search s
    JOIN offer o ON (o.snapshot_id, o.id) = (s.snapshot_id, s.offer_id)
    WHERE s.snapshot_id = %(snapshot)s AND o.group_id IS NULL AND o.available
      AND char_length(o.name) <= 200
      AND (s.name_normalized LIKE %(leading)s OR s.name_normalized LIKE %(word)s)
    GROUP BY o.name
    ORDER BY bool_or(s.name_normalized LIKE %(leading)s) DESC, length(o.name), o.name
    LIMIT 6
"""

SUGGEST_IDENTIFIER = """
    SELECT DISTINCT name FROM offer
    WHERE snapshot_id = %(snapshot)s AND available AND char_length(name) <= 200
      AND (id = %(identifier)s OR lower(vendor_code) = %(identifier)s)
    ORDER BY name
    LIMIT 6
"""

SPELLING_OFFERS = """
    SELECT o.name, o.vendor
    FROM offer_search s
    JOIN offer o ON (o.snapshot_id, o.id) = (s.snapshot_id, s.offer_id)
    WHERE s.snapshot_id = %(snapshot)s AND o.available
      AND lower(%(query)s) <%% s.name_normalized
    ORDER BY word_similarity(lower(%(query)s), s.name_normalized) DESC, o.id
    LIMIT 36
"""


def catalog_spelling_options(connection, snapshot, query):
    tokens = query.split()
    brand_candidates = []
    for brand, names in SPELLING_ALIASES.items():
        for name in (brand, *names):
            length = len(name.split())
            suffix = " ".join(tokens[length:])
            brand_candidates.append((f"{brand} {suffix}".strip(), f"{name} {suffix}".strip()))
    brands = rank_spellings(query, brand_candidates)
    if len(tokens) == 1 and clear_winner(brands):
        return brands

    params = {"snapshot": snapshot, "query": query}
    families = connection.execute(NEARBY_FAMILIES, params).fetchall()
    # Изменённый порог действует только для выборки кандидатов внутри откатываемой транзакции.
    with connection.transaction(force_rollback=True):
        connection.execute("SELECT set_config('pg_trgm.word_similarity_threshold', '0.35', true)")
        offers = connection.execute(SPELLING_OFFERS, params).fetchall()
    names = {unescape(row["name"]) for row in (*families, *offers)}
    names.update(unescape(row["vendor"]) for row in offers if row["vendor"])
    candidates = [(name, name) for name in names if 4 <= len(name) <= 90 and name.casefold() != query.casefold()]
    return rank_spellings(query, [*brand_candidates, *candidates])


def catalog_identifier(query):
    return (
        2 <= len(query) <= 100 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_-]*", query) is not None
        and any(char.isdigit() for char in query)
    )


def resolve_suggestion_alias(query):
    name = normal_form(query)
    if name in {"каспер", "лаборатория касперского"} or normalize_query(name) == "Kaspersky":
        return "Kaspersky"
    exact = BRAND_BY_NAME.get(name)
    if exact or len(name) < 4:
        return exact
    matches = {
        canonical for canonical, aliases in SUGGESTION_ALIASES.items()
        if any(alias.startswith(name) for alias in aliases)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def known_brand(query):
    canonical = resolve_suggestion_alias(query)
    if canonical:
        return canonical
    name = normal_form(query)
    if re.fullmatch(r"[a-z]{4,}", name):
        return resolve_suggestion_alias(name.translate(TO_RUSSIAN))
    if re.fullmatch(r"[а-яё]{4,}", name):
        return resolve_suggestion_alias(name.translate(TO_ENGLISH))
    return None


def normalize_search_aliases(query):
    for pattern, replacement in SEARCH_ALIASES:
        query = pattern.sub(replacement, query)
    return query


def resolve_search_input(query):
    query = normalize_search_aliases(query)
    brand = known_brand(query)
    if brand:
        return brand
    leading = LEADING_BRAND.match(query)
    if leading and normal_form(leading[0]) != "астра":
        return BRAND_BY_NAME[normal_form(leading[0])] + query[leading.end():]
    return query


def suggest_families(connection, snapshot, query):
    if catalog_identifier(query):
        rows = connection.execute(SUGGEST_IDENTIFIER, {
            "identifier": query.lower(), "snapshot": snapshot,
        }).fetchall()
        if rows:
            return [unescape(row["name"]) for row in rows]
    canonical = known_brand(query)
    lookup = canonical or normalize_query(resolve_search_input(query))
    rows = connection.execute(SUGGEST_FAMILIES, {"query": lookup, "snapshot": snapshot}).fetchall()
    if not rows and len(lookup) >= 4:
        literal = lookup.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = connection.execute(SUGGEST_UNGROUPED, {
            "snapshot": snapshot, "leading": literal + "%", "word": "% " + literal + "%",
        }).fetchall()
    suggestions = [unescape(row["name"]) for row in rows]
    if canonical == "Kaspersky" and suggestions:
        return ["Kaspersky", *[item for item in suggestions if item.casefold() != "kaspersky"][:5]]
    if lookup.casefold() == "vpn" and suggestions:
        return ["VPN", *[item for item in suggestions if item.casefold() != "vpn"][:5]]
    return suggestions


@dataclass
class QueryResolution:
    lookup: str
    lexical: list
    choices: list[str] = field(default_factory=list)


def spelling_matches(connection, snapshot, label):
    rows = lexical_candidates(connection, snapshot, label, allow_fuzzy=False)
    key = normal_form(label)
    brand = next((name for name in SPELLING_BRANDS
                  if key == name.casefold() or key.startswith(name.casefold() + " ")), None)
    if not brand:
        return rows
    return [row for row in rows if vendor_matches(row, brand)]


def resolve_catalog_query(connection, snapshot, query, *, prefixes=None, speller=None):
    lookup = resolve_search_input(query)
    lexical = lexical_candidates(connection, snapshot, lookup, allow_fuzzy=False)
    if lexical or lookup.casefold() != normalize_search_aliases(query).casefold() or catalog_identifier(query):
        return QueryResolution(lookup, lexical)

    query = lookup
    if not 4 <= len(query) <= 64 or len(query.split()) > 6:
        return QueryResolution(lookup, lexical)

    if prefixes is None:
        prefixes = suggest_families(connection, snapshot, query)
    completions = [name for name in prefixes if name.casefold().startswith(query.casefold())]
    ranked = [(0, name) for name in completions] if completions else catalog_spelling_options(connection, snapshot, query)
    confirmed = [(score, label, rows) for score, label in ranked
                 if (rows := spelling_matches(connection, snapshot, label))]
    labels = [label for _, label, _ in confirmed]
    choices = labels.copy()
    if speller and not completions:
        proposals = speller.suggest(query, labels)
        choices = list(dict.fromkeys([
            *(label for label in proposals if label in labels or spelling_matches(connection, snapshot, label)),
            *labels,
        ]))

    # Только близкое однозначное совпадение можно применить без выбора пользователя.
    if (confirmed and choices[0] == confirmed[0][1] and confirmed[0][0] <= 20
            and clear_winner([(score, label) for score, label, _ in confirmed])):
        _, corrected, rows = confirmed[0]
        return QueryResolution(corrected, rows)
    return QueryResolution(lookup, lexical, choices[:6])
