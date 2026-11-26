"""Парное сравнение поисковых систем по зафиксированному набору запросов."""

import argparse
import csv
import json
import math
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from benchmark_dataset import catalog_cases
from evaluate import CASES

INTENTS = (
    ("нужно работать на офисном пк в командировке", ("327029", "327030")),
    ("удалённо открыть сеанс на другом компьютере", ("327029", "327030")),
    ("автоматически сохранять предыдущие версии папок", ("1033",)),
    ("сделать резервную копию корпоративных данных", ("1033",)),
    ("вернуть удалённые данные с диска", ("5922",)),
    ("восстановить данные после форматирования накопителя", ("5922",)),
    ("антивирус для рабочих станций компании", ("650",)),
    ("антивирус для домашнего компьютера", ("3775",)),
    ("открывать электронную почту в отдельном приложении", ("1267",)),
    ("настольный клиент для нескольких почтовых ящиков", ("1267",)),
    ("офисный пакет российского производства", ("222323",)),
    ("работать с текстом и таблицами без браузера", ("1145", "222323")),
    ("запускать на одном компьютере несколько виртуальных машин", ("2500",)),
    ("гипервизор для виртуальных машин", ("2500",)),
    ("ключ активации операционной системы Windows", ("2050",)),
    ("российская операционная система для рабочих мест", ("222320",)),
    ("физический сервер формата rack для стойки", ("86563",)),
    ("ограничить доступ сотрудников к ресурсам", ("4742",)),
)


def dataset(feed):
    rows = catalog_cases(feed)
    for index, (query, categories) in enumerate(INTENTS, 1):
        rows.append({"query": query, "segment": "Пользовательские задачи",
                     "cluster": f"intent-{index}", "field": "category_id", "accepted": categories})
    for index, (source, query, field, expected) in enumerate(CASES, 1):
        segment = "Эталоны Softline" if source == "эталон" else "Сценарии настройки"
        rows.append({
            "query": query, "segment": segment, "cluster": f"control-{index}", "field": field,
            "accepted": (expected,) if isinstance(expected, str) else expected,
        })
    return rows


def is_hit(case, item):
    field = case["field"]
    value = item.get(field)
    if field == "vendor":
        return any(str(value or "").casefold().startswith(label.casefold())
                   for label in case["accepted"])
    if case["query"] == "кибер бэкап" and not item.get("vendor", "").casefold().startswith("киберпротект"):
        return False
    return value in case["accepted"]


def search(origin, semantic, query):
    options = urlencode({"q": query, "semantic": str(semantic).lower()})
    started = time.monotonic()
    try:
        with urlopen(f"{origin.rstrip('/')}/search?{options}", timeout=8) as response:
            data = json.load(response)
    except (OSError, ValueError) as error:
        return {"error": getattr(error, "code", None) or type(error).__name__,
                "mode": None, "latency_ms": None, "items": []}
    return {
        "error": None, "mode": data["search_mode"], "snapshot": data["catalog_version"],
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
        "items": [{key: item.get(key) for key in ("offer_id", "group_id", "category_id", "vendor", "name")}
                  for item in data["results"][:4]],
    }


def summary(rows, system):
    observations = [row["systems"][system] for row in rows]
    ranks = [item["first_relevant"] for item in observations]
    latencies = sorted(item["latency_ms"] for item in observations if item["latency_ms"] is not None)
    return {
        "queries": len(rows),
        "top1": sum(rank == 1 for rank in ranks),
        "top4": sum(rank is not None for rank in ranks),
        "mrr4": round(sum(1 / rank for rank in ranks if rank is not None) / len(rows), 4),
        "empty": sum(not item["items"] and item["error"] is None for item in observations),
        "errors": sum(item["error"] is not None for item in observations),
        "hybrid_responses": sum(item["mode"] == "hybrid" for item in observations),
        "p50_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
    }


def compare(baseline, current, categories, cases):
    systems = {
        "historical": (baseline, False),
        "current_lexical": (current, False),
        "current_hybrid": (current, True),
    }
    for url, enabled in systems.values():
        for case in cases[:9]:
            search(url, enabled, case["query"])
    results = []
    channels = list(systems)
    for index, case in enumerate(cases):
        outputs = {}
        offset = index % len(channels)
        for name in channels[offset:] + channels[:offset]:
            url, enabled = systems[name]
            item = search(url, enabled, case["query"])
            for card in item["items"]:
                category = categories[card["offer_id"]] or None
                if card["category_id"] is not None and card["category_id"] != category:
                    raise RuntimeError("Системы вернули несовместимые категории для одного предложения")
                card["category_id"] = category
            item["first_relevant"] = next(
                (rank for rank, card in enumerate(item["items"], 1) if is_hit(case, card)), None
            )
            outputs[name] = item
        results.append({**case, "systems": outputs})
    segments = ["Все", *dict.fromkeys(row["segment"] for row in results)]
    report = {}
    for segment in segments:
        rows = results if segment == "Все" else [row for row in results if row["segment"] == segment]
        report[segment] = {name: summary(rows, name) for name in systems}
        historical_hits = {i for i, row in enumerate(rows) if row["systems"]["historical"]["first_relevant"]}
        hybrid_hits = {i for i, row in enumerate(rows) if row["systems"]["current_hybrid"]["first_relevant"]}
        report[segment]["paired"] = {
            "only_hybrid": len(hybrid_hits - historical_hits),
            "only_historical": len(historical_hits - hybrid_hits),
        }
    return report, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="http://127.0.0.1:8002")
    parser.add_argument("--current", default="http://127.0.0.1:8000")
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--catalog-id-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("search-comparison.json"))
    args = parser.parse_args()
    with args.catalog_id_map.open(encoding="utf-8", newline="") as mapping:
        categories = dict(csv.reader(mapping, delimiter="\t"))
    report, rows = compare(args.baseline, args.current, categories, dataset(args.feed))
    args.output.write_text(json.dumps({"report": report}, ensure_ascii=False, indent=2) + "\n")
    for segment, result in report.items():
        print(json.dumps({"segment": segment, **result}, ensure_ascii=False), flush=True)
    if any(report["Все"][system]["errors"] for system in ("historical", "current_lexical", "current_hybrid")):
        raise SystemExit("Есть ошибки HTTP. Сводку нельзя считать полноценным замером")
    snapshots = {
        result["snapshot"] for row in rows for result in row["systems"].values()
        if result["error"] is None
    }
    if len(snapshots) != 1:
        raise SystemExit("Системы использовали разные снимки каталога")
    if report["Все"]["current_hybrid"]["hybrid_responses"] != len(rows):
        raise SystemExit("Часть гибридных ответов фактически обработана без модели")


if __name__ == "__main__":
    main()
