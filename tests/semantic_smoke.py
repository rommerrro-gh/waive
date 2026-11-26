import json
import statistics
import time

# Независимые формулировки для проверки категорий в обоих режимах.
INTENT_CHECKS = {
    "создать копию документов на случай сбоя": {"1033"},
    "защитить рабочие станции компании от вредоносных вирусов": {"650"},
    "подбор оборудования для монтажа в серверную стойку": {"86563"},
    "хочу редактировать текст и таблицы на работе": {"1145", "222323"},
}
from urllib.parse import urlencode
from urllib.request import urlopen

QUERIES = (
    "подключиться к рабочему компьютеру из дома",
    "восстановить файлы после сбоя",
    "программа для защиты компьютеров отдела",
    "КриптоПро CSP 5.0",
    "AnyDesk",
)


def search(query, semantic):
    arguments = urlencode({"q": query, "semantic": str(semantic).lower()})
    start = time.monotonic()
    with urlopen("http://127.0.0.1:8000/search?" + arguments, timeout=8) as response:
        result = json.load(response)
    return result, (time.monotonic() - start) * 1000


search("AnyDesk", True)
for semantic in (False, True):
    timings = []
    for query in QUERIES:
        result, latency = search(query, semantic)
        assert result["search_mode"] == ("hybrid" if semantic else "lexical"), result
        if semantic and query.startswith("подключиться"):
            assert result["results"] and result["results"][0]["category_id"] in ("327029", "327030"), result
        if query == "AnyDesk":
            assert result["results"] and "anydesk" in result["results"][0]["name"].casefold(), result
        timings.append(latency)
        print(query, result["search_mode"], [item["name"] for item in result["results"]], flush=True)
    print("Медиана, мс:", round(statistics.median(timings), 1), flush=True)

for semantic in (False, True):
    for query, categories in INTENT_CHECKS.items():
        result, _ = search(query, semantic)
        assert result["results"] and result["results"][0]["category_id"] in categories, result
        first = result["results"][:4]
        in_scope = len([item for item in first if item["category_id"] in categories])
        assert in_scope == len(first), result
        print("Категория проверена:", query, result["search_mode"], in_scope, flush=True)
