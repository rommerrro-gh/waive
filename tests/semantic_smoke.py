import json
import statistics
import time
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
        if query == "AnyDesk":
            assert result["results"] and "anydesk" in result["results"][0]["name"].casefold(), result
        timings.append(latency)
        print(query, result["search_mode"], [item["name"] for item in result["results"]], flush=True)
    print("Медиана, мс:", round(statistics.median(timings), 1), flush=True)
