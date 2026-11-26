"""Сравнение текстового и гибридного поиска по целевым семействам и категориям."""
import json
import statistics
import time
from urllib.parse import urlencode
from urllib.request import urlopen

CASES = [
    ("эталон", "astra linux", "group_id", "121231"),
    ("эталон", "Microsoft Office для дома и бизнеса", "group_id", "391338"),
    ("эталон", "kaspersky small office security", "group_id", "3761"),
    ("эталон", "крипто про csp 5.0", "group_id", "22268"),
    ("эталон", "anydesk", "group_id", "306539"),
    ("эталон", "кибер бэкап", "category_id", "1033"),
    ("эталон", "сканер-вс", "group_id", "46421"),
    ("эталон", "мтс", "vendor", "МТС Линк"),
    ("эталон", "max patrol", "group_id", "437421"),
    ("эталон", "365", "group_id", "165034"),
    ("смысл", "зайти на рабочий компьютер из дома", "category_id", ("327029", "327030")),
    ("смысл", "сохранить копии файлов на случай поломки", "category_id", "1033"),
    ("смысл", "защита компьютеров сотрудников от вирусов", "category_id", "650"),
    ("смысл", "купить железо для установки в серверную стойку", "category_id", "86563"),
    ("смысл", "редактор документов и электронных таблиц для работы", "category_id", "1145"),
]


def evaluate(semantic):
    rows = []
    for sample, query, field, expected in CASES:
        params = urlencode({"q": query, "semantic": str(semantic).lower()})
        started = time.monotonic()
        with urlopen("http://127.0.0.1:8000/search?" + params, timeout=8) as response:
            data = json.load(response)
        elapsed = round((time.monotonic() - started) * 1000, 1)
        hits = []
        for card in data["results"]:
            actual = card[field]
            if field == "vendor":
                hits.append(actual.casefold().startswith(expected.casefold()))
            elif isinstance(expected, tuple):
                hits.append(actual in expected)
            else:
                matches = actual == expected
                if query == "кибер бэкап":
                    matches = matches and card["vendor"].casefold().startswith("киберпротект")
                hits.append(matches)
        rows.append({
            "sample": sample, "query": query, "target_at_1": bool(hits and hits[0]),
            "hit_at_4": any(hits), "latency_ms": elapsed, "mode": data["search_mode"],
            "catalog_version": data["catalog_version"],
            "products": [card["name"] for card in data["results"]],
        })
    return rows


if __name__ == "__main__":
    for semantic in (False, True):
        rows = evaluate(semantic)
        for sample in ("эталон", "смысл"):
            subset = [row for row in rows if row["sample"] == sample]
            summary = {
                "total": len(subset),
                "target_at_1": sum(row["target_at_1"] for row in subset),
                "hit_at_4": sum(row["hit_at_4"] for row in subset),
                "p50_ms": statistics.median(row["latency_ms"] for row in subset),
                "hybrid_responses": sum(row["mode"] == "hybrid" for row in subset),
            }
            print(json.dumps({"semantic": semantic, "sample": sample, **summary, "rows": subset}, ensure_ascii=False))
