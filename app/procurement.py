import re

PLATFORMS = ("windows", "windows-server", "linux", "astra-linux", "macos")
LICENSE_TYPES = ("new", "subscription", "renewal", "upgrade")
LABELS = {"platform": "Платформа", "term": "Срок лицензии", "license_type": "Тип лицензии"}
CATALOG_FILTERS = ("in_stock", "price_min", "price_max")

PLATFORM_PATTERNS = {
    "windows-server": r"\bwindows\s+server\b",
    "windows": r"\b(?:windows(?!\s+server\b)|win|винда)\b",
    "astra-linux": r"\bastra\s*linux\b|\bастра\s*линукс\b",
    "linux": r"\b(?:linux|lin|линукс|ubuntu|debian|centos)\b",
    "macos": r"\bmac\s*os(?:\s*x)?",
}
LICENSE_PATTERNS = {
    "renewal": r"продлен|renewal",
    "upgrade": r"обновлен|upgrade",
    "subscription": r"подписк|subscription",
    "new": r"полн(?:ая|ую) верси|базов(?:ая|ую)|\bfull\b|нов(?:ая|ую) лицензи",
}
MONTHS = r"(\d{1,3})\s*(?:мес(?:яц\w*)?\.?|months?)"
YEAR = r"(?:на\s+год\b|(\d{1,2})\s*(?:года?|лет|years?)\b)"


def requested_filters(query):
    query = query.casefold().replace("ё", "е")
    platforms = matching_platforms(query)
    term = re.search(MONTHS, query)
    year = re.search(YEAR, query)
    types = license_types(query)
    suggestions = {}
    if len(platforms) == 1:
        suggestions["platform"] = next(iter(platforms))
    if term and 1 <= int(term[1]) <= 120:
        suggestions["term"] = str(int(term[1]))
    elif year and int(year[1] or 1) <= 10 and int(year[1] or 1) > 0:
        suggestions["term"] = str(int(year[1] or 1) * 12)
    elif re.search(r"бессрочн|perpetual", query):
        suggestions["term"] = "perpetual"
    if len(types) == 1:
        suggestions["license_type"] = next(iter(types))
    return suggestions


def matching_platforms(value):
    value = value.casefold()
    platforms = {key for key, pattern in PLATFORM_PATTERNS.items() if re.search(pattern, value)}
    if "astra-linux" in platforms and not re.search(
        PLATFORM_PATTERNS["linux"], re.sub(PLATFORM_PATTERNS["astra-linux"], "", value)
    ):
        platforms.discard("linux")
    return platforms


def license_types(value):
    types = {key for key, pattern in LICENSE_PATTERNS.items() if re.search(pattern, value)}
    # Продление подписки остаётся продлением. Требование новой подписки проверяется отдельно.
    return types & {"renewal", "upgrade"} or types


def purchase_values(attributes):
    platform = " / ".join(attributes.get(key, "") or "" for key in (
        "Платформа", "Совместимая архитектура/платформа", "Поддерживаемые ОС",
    ))
    platforms = matching_platforms(platform)
    if "astra-linux" in platforms:
        platforms.add("linux")
    term = (attributes.get("Срок действия") or "").casefold().strip()
    month = re.fullmatch(MONTHS, term)
    term = str(int(month[1])) if month and int(month[1]) else (
        "perpetual" if re.fullmatch(r"бессрочн\w*|perpetual", term) else None
    )
    types = license_types((attributes.get("Тип лицензии") or "").casefold().replace("ё", "е"))
    return {"platform": platforms, "term": {term} if term else set(), "license_type": types}


def catalog_match(item, key, value):
    if key == "in_stock":
        return item["available"]
    price = item["price"]
    if price is None or price <= 0 or (item["currency"] or "RUB") not in {"RUB", "RUR"}:
        return None
    return price >= value if key == "price_min" else price <= value


def filter_offers(offers, filters, family_key, attributes_for):
    families_before = {family_key(item) for item in offers}
    outcome = {key: {"would_restore": set(), "unverified": set()} for key in filters}
    passed = []
    purchase = {key: value for key, value in filters.items() if key in LABELS}
    catalog = {key: value for key, value in filters.items() if key in CATALOG_FILTERS}
    for item in offers:
        values = purchase_values(attributes_for(item)) if purchase else {}
        checks = {key: value in values[key] if values[key] else None for key, value in purchase.items()}
        checks.update((key, catalog_match(item, key, value)) for key, value in catalog.items())
        failures = [key for key, matches in checks.items() if not matches]
        if not failures:
            passed.append(item)
        elif len(failures) == 1:
            key = failures[0]
            bucket = "unverified" if checks[key] is None else "would_restore"
            outcome[key][bucket].add(family_key(item))
    families_after = {family_key(item) for item in passed}
    diagnostics = {}
    for key, counts in outcome.items():
        restored = (counts["would_restore"] | counts["unverified"]) - families_after
        diagnostics[key] = {
            "would_restore": len(restored),
            "unverified": len(counts["unverified"] - families_after - counts["would_restore"]),
        }
    return passed, {
        "families_before": len(families_before),
        "families_after": len(families_after),
        "conditions": diagnostics,
    }
