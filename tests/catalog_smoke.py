import os
import re
from urllib.parse import urlencode
from urllib.request import urlopen
import json

import psycopg


def get(path):
    with urlopen("http://127.0.0.1:8000" + path, timeout=5) as response:
        return json.load(response)


with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    row = connection.execute("""
        SELECT count(*), count(*) FILTER (WHERE o.available)
        FROM offer o JOIN active_catalog a ON a.snapshot_id = o.snapshot_id
    """).fetchone()
    assert row == (136040, 44601), row
    assert connection.execute("SELECT count(*) FROM catalog_snapshot").fetchone() == (1,)

ready = get("/ready")
for query in ("AnyDesk", "КриптоПро CSP 5.0"):
    result = get("/search?" + urlencode({"q": query}))
    assert result["catalog_version"] == ready["catalog_version"], result
    assert result["search_mode"] == "lexical", result
    assert result["results"], query
    assert len(result["results"]) <= 4, result
    if query.startswith("КриптоПро"):
        for card in result["results"]:
            assert re.search(r"(?<![\w.])5\.0(?![\w.])", card["attributes"].get("Версия") or card["name"])
print("Полный каталог и ответы API проверены")
