"""Детерминированная выборка по каталогу. Исходные значения остаются в процессе."""

import collections
import gzip
import hashlib
import re
import xml.etree.ElementTree as ET

EXCLUDED_FAMILIES = (
    "Astra Linux", "Microsoft Office", "Kaspersky Small Office", "CryptoPro", "КриптоПро",
    "AnyDesk", "Кибер Бэкап", "СКАНЕР-ВС", "МТС Линк", "MaxPatrol", "Microsoft 365",
)
CODE = re.compile(r"[A-Za-z0-9_.+-]{4,32}")
WORD = re.compile(r"[A-Za-zА-Яа-я]{5,}")


def catalog_cases(feed):
    names = {}
    codes = collections.Counter()
    eligible = collections.defaultdict(list)
    with gzip.open(feed, "rb") as source:
        for _, item in ET.iterparse(source):
            if item.tag == "category":
                names[item.get("id")] = item.text or ""
            elif item.tag == "offer":
                group = item.get("group_id")
                code = item.findtext("vendorCode", "").strip()
                if code:
                    codes[code.casefold()] += 1
                family = names.get(group, "")
                if item.get("available") == "true" and CODE.fullmatch(code):
                    if 6 <= len(family) <= 100 and WORD.search(family):
                        eligible[group].append((item.get("id"), item.findtext("categoryId"), code, family))
                item.clear()
    ordered = sorted(eligible, key=lambda group: hashlib.sha256(("waive-benchmark-v1:" + group).encode()).digest())
    selected = []
    category_counts = collections.Counter()
    used_queries = set()
    for group in ordered:
        name = names[group]
        if any(excluded.casefold() in name.casefold() for excluded in EXCLUDED_FAMILIES):
            continue
        for identifier, category, code, family in eligible[group]:
            if category_counts[category] >= 3 or codes[code.casefold()] != 1:
                continue
            offset = WORD.search(family).start() + 2
            typo = family[:offset] + family[offset + 1] + family[offset] + family[offset + 2:]
            if typo == family:
                typo = family[:offset] + family[offset + 1:]
            queries = {value.casefold() for value in (code, family, typo)}
            if len(queries) != 3 or used_queries.intersection(queries):
                continue
            selected.append((group, identifier, code, family, typo))
            used_queries.update(queries)
            category_counts[category] += 1
            break
        if len(selected) == 40:
            break
    if len(selected) != 40:
        raise RuntimeError("В каталоге недостаточно подходящих семейств для выбранного профиля")
    return [
        {"query": query, "segment": segment, "cluster": f"family-{index}",
         "field": field, "accepted": (accepted,)}
        for index, (group, identifier, code, family, typo) in enumerate(selected, 1)
        for query, segment, field, accepted in (
            (code, "Артикул", "offer_id", identifier),
            (family, "Название семейства", "group_id", group),
            (typo, "Опечатка в названии", "group_id", group),
        )
    ]
