"""Explicit shopping requirements and the evidence available on a single offer."""

import re
from dataclasses import dataclass, replace

from app.procurement import (CATALOG_FILTERS, LABELS, LICENSE_PATTERNS, NEGATIVE_CONDITION, catalog_match, positive_query,
                             matching_platforms, purchase_values, requested_filters)

OS = re.compile(r"(?<!\w)(?:windows server(?: 20\d{2})?|windows (?:10|11)|astra linux|alt linux|"
                r"red hat enterprise linux|ubuntu|debian|centos|(?:apple )?mac ?os(?: x)?|"
                r"linux|android|ios|freebsd|vmware esxi|windows)(?!\w)", re.I)
OS_FIELDS = ("Платформа", "Совместимая архитектура/платформа", "Поддерживаемые ОС")
SEATS = re.compile(r"(?<!\d)([1-9]\d{0,6})\s+(рабочих\s+мест|мест\b|пользовател\w*|устройств\w*|узл\w*|рабоч\w+\s+станци\w*)", re.I)
PRICE = re.compile(r"\b(от|до|не дороже)\s+((?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d+)?)\s*(тыс\.?\s*)?(?:₽|руб(?:лей|ля|\.)?(?!\w))", re.I)
PRICE_RANGE = re.compile(r"\bот\s+(\d+(?: \d{3})*)\s+до\s+(\d+(?: \d{3})*)\s*(₽|руб(?:лей|ля|\.)?(?!\w))", re.I)
EDITION = re.compile(r"\b(?:редакци\w+|edition)\s+[«\"]?([\w-]+)|\b(enterprise|professional|standard|premium|ultimate)\b", re.I)
ASTRA_EDITION = re.compile(r"\b(?:astra linux|астра линукс)\s+(se|ce|special edition|common edition)\b", re.I)
PLATFORM_PRODUCT_CATEGORIES = {"2050", "5184", "222320"}
VERSION = re.compile(r"(?<![\w.])\d+(?:\.\d+)+(?![\w.])")
STOCK = re.compile(r"\b(?:только\s+)?в наличии\b", re.I)
PREFERENCE = re.compile(r"\b(?:желательно|предпочтительно|по возможности|в идеале)\s+([^,;]+)", re.I)
DURATION = re.compile(r"\b(?:на\s+)?(?:год\b|\d{1,3}\s+(?:мес\w*\.?|года?\b|лет\b|months?\b|years?\b))", re.I)
DEVICE_CAPACITY = ("К-во узлов", "Количество устройств", "Количество рабочих мест", "Количество устройств в лицензии")
USER_CAPACITY = ("К-во пользователей до", "Количество пользователей", "К-во пользователей")
UNIT = r"(?:пользовател\w*|рабоч\w+\s+(?:мест\w*|станци\w*)|устройств\w*|узл\w*)"


@dataclass(frozen=True)
class Requirement:
    key: str
    value: object
    label: str
    mandatory: bool = True


def os_labels(value):
    value = re.sub(r"\bастра\s+линукс\b", "astra linux", value, flags=re.I)
    return {"macos" if re.fullmatch(r"(?:apple )?mac ?os(?: x)?", label, re.I) else label.casefold()
            for label in OS.findall(value)}


def requested_os(text):
    matches = list(OS.finditer(text))
    groups = []
    for index, match in enumerate(matches):
        label = next(iter(os_labels(match[0])))
        if index and re.fullmatch(r"\s+(?:или|or)\s+", text[matches[index - 1].end():match.start()]):
            groups[-1].append(label)
        else:
            groups.append([label])
    return groups


def offer_os(attributes):
    return os_labels(" / ".join(attributes.get(field) or "" for field in OS_FIELDS))


def catalog_os(offer, attributes):
    declared = offer_os(attributes)
    if offer.get("category_id") in PLATFORM_PRODUCT_CATEGORIES:
        return os_labels(offer["name"]) or declared
    return declared


def os_matches(required, offered):
    return any(required == item or item.startswith(required + " ")
               or (required == "linux" and item in {"astra linux", "alt linux", "ubuntu", "debian", "centos", "red hat enterprise linux"})
               for item in offered)


def parse_requirements(query, filters=None):
    text = query.casefold().replace("ё", "е").replace("\u00a0", " ")
    preferences = PREFERENCE.findall(text)
    text = PREFERENCE.sub(" ", text)
    positive = positive_query(text)
    excluded = [match.group(1) for match in NEGATIVE_CONDITION.finditer(text)]
    output = {}
    def add(key, value, label):
        output[key, value] = Requirement(key, value, label)

    explicit = requested_filters(positive)
    versions_without_price = PRICE.sub("", PRICE_RANGE.sub("", positive))
    for version in VERSION.findall(versions_without_price):
        add("version", version, "версия " + version)
    if not OS.fullmatch(positive.strip()):
        for group in requested_os(positive):
            if len(group) == 1:
                add("os", group[0], group[0])
            else:
                add("os_any", tuple(group), " или ".join(group))
    if "platform" in explicit and not os_labels(positive):
        add("platform", explicit["platform"], explicit["platform"])
    for key in ("term", "license_type"):
        if key in explicit:
            add(key, explicit[key], LABELS[key] + ": " + explicit[key])
    for phrase in excluded:
        excluded_os = os_labels(phrase) or matching_platforms(phrase)
        excluded_licenses = [kind for kind, pattern in LICENSE_PATTERNS.items() if re.search(pattern, phrase)]
        for os in sorted(excluded_os):
            add("exclude_os", os, "без " + os)
        for kind in excluded_licenses:
            add("exclude_license_type", kind, "без " + phrase.strip())
        if not excluded_os and not excluded_licenses:
            add("exclude_unverified", phrase.strip(), "без " + phrase.strip())
    for match in EDITION.finditer(positive):
        edition = match[1] or match[2]
        add("edition", edition, "редакция " + edition)
    if astra := ASTRA_EDITION.search(positive):
        edition = "special edition" if astra[1] in {"se", "special edition"} else "common edition"
        add("edition", edition, "редакция " + edition)
    for match in SEATS.finditer(positive):
        key = "users" if match[2].startswith("пользовател") else "devices"
        add(key, int(match[1]), f"{match[1]} {match[2]}")
    for match in PRICE_RANGE.finditer(positive):
        add("price_min", int(match[1].replace(" ", "")), "цена от " + match[1])
        add("price_max", int(match[2].replace(" ", "")), "цена до " + match[2])
    for match in PRICE.finditer(positive):
        value = float(match[2].replace(" ", "").replace(",", ".")) * (1000 if match[3] else 1)
        key = "price_min" if match[1] == "от" else "price_max"
        add(key, value, "цена " + match[0])
    if STOCK.search(positive):
        add("in_stock", True, "в наличии")
    for key, value in (filters or {}).items():
        add(key, value, LABELS.get(key, key))
    for preference in preferences:
        for requirement in parse_requirements(preference):
            output.setdefault((requirement.key, requirement.value), replace(requirement, mandatory=False))
    return tuple(output.values())


def retrieval_query(query):
    text = positive_query(PREFERENCE.sub(" ", query))
    for pattern in (PRICE_RANGE, PRICE, STOCK, SEATS, DURATION, OS):
        text = pattern.sub(" ", text)
    text = re.sub(r"(?:\b(?:для|с|на|за|в пределах|или|и|or|and)[\s,]*)+$", "", text.strip(), flags=re.I)
    return " ".join(text.split()) or query


def check_requirement(offer, attributes, requirement):
    key, value = requirement.key, requirement.value
    if key in CATALOG_FILTERS:
        return catalog_match(offer, key, value)
    if key in LABELS or key == "exclude_license_type":
        values = purchase_values(attributes)[key if key in LABELS else "license_type"]
        if not values:
            return None
        if key == "exclude_license_type":
            # Продление или обновление может относиться к подписке. Проверяем исходный тип.
            raw = (attributes.get("Тип лицензии") or "").casefold()
            if value == "subscription":
                if re.search(r"подписк|subscription", raw):
                    return False
                if values & {"renewal", "upgrade"} and "perpetual" not in purchase_values(attributes)["term"]:
                    return None
            return value not in values
        return value in values
    if key in ("os", "os_any", "exclude_os"):
        offered = catalog_os(offer, attributes)
        if not offered:
            return None
        targets = value if key == "os_any" else (value,)
        matched = any(os_matches(target, offered) for target in targets)
        # Номер ОС не считается подтверждённым записью «Windows» без номера.
        if not matched and any(target.startswith(item + " ") for item in offered for target in targets):
            return None
        return not matched if key == "exclude_os" else matched
    if key == "version":
        versions = VERSION.findall(attributes.get("Версия") or offer["name"])
        return value in versions if versions else None
    if key == "edition":
        field = attributes.get("Редакция")
        text = field or (attributes.get("Версия") or "") + " " + offer["name"]
        if value in ("special edition", "common edition"):
            alias = "se" if value == "special edition" else "ce"
            if re.search(r"\b(?:astra linux|астра линукс)\s+" + alias + r"\b", text, re.I):
                return True
        return True if re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.I) else False if field else None
    if key in ("users", "devices"):
        fields = USER_CAPACITY if key == "users" else DEVICE_CAPACITY
        for field in fields:
            raw = str(attributes.get(field) or "").strip().casefold()
            if re.fullmatch(r"(?:неограниченн\w*|unlimited)", raw):
                return True
            if re.fullmatch(r"\d+", raw):
                return int(raw) >= value
            if range_value := re.fullmatch(r"(?:от\s+)?(\d+)\s*(?:до|[-–])\s*(\d+)", raw):
                return int(range_value[1]) <= value <= int(range_value[2])
        text = offer["name"]
        for match in re.finditer(r"(?<!\d)(\d+)\s+(" + UNIT + ")", text, re.I):
            if bool(re.match(r"пользовател", match[2], re.I)) == (key == "users"):
                return int(match[1]) >= value
    return None


def unmet_requirements(offer, attributes, requirements):
    return tuple((requirement, status) for requirement in requirements
                 if requirement.mandatory and (status := check_requirement(offer, attributes, requirement)) is not True)
