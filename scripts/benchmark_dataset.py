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


def catalog_cases(feed, seed="waive-benchmark-v1:", count=40, excluded_groups=()):
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
    ordered = sorted(eligible, key=lambda group: hashlib.sha256((seed + group).encode()).digest())
    selected = []
    category_counts = collections.Counter()
    used_queries = set()
    for group in ordered:
        if group in excluded_groups:
            continue
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
        if len(selected) == count:
            break
    if len(selected) != count:
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

def extra_typos(feed, original):
    groups = {row["accepted"][0] for row in original if row["field"] == "group_id"}
    candidates = catalog_cases(feed, seed="waive-typo-holdout-v1:", count=80, excluded_groups=groups)
    titles = [row for row in candidates if row["segment"] == "Название семейства"]
    result = []
    for index, row in enumerate(titles):
        name = row["query"]
        offset = WORD.search(name).start() + 2
        substitution = "ж" if re.search(r"[А-Яа-я]", name[offset]) else "z"
        if substitution.casefold() == name[offset].casefold():
            substitution = "ф" if substitution == "ж" else "x"
        variants = (
            ("Перестановка", candidates[index * 3 + 2]["query"]),
            ("Пропуск", name[:offset] + name[offset + 1:]),
            ("Лишний символ", name[:offset] + name[offset] + name[offset:]),
            ("Замена", name[:offset] + substitution + name[offset + 1:]),
        )
        segment, query = variants[index % len(variants)]
        result.append({**row, "cluster": f"holdout-typo-{index}", "segment": segment,
                       "query": query, "suite": "additional"})
    return result
