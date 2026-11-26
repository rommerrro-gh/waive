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
    office_id = connection.execute("""
        SELECT id FROM offer JOIN active_catalog ON offer.snapshot_id = active_catalog.snapshot_id
        WHERE group_id = '391338' AND available ORDER BY id LIMIT 1
    """).fetchone()[0]

ready = get("/ready")
for query in ("AnyDesk", "КриптоПро CSP 5.0", "антивирус для рабочих станций компании"):
    result = get("/search?" + urlencode({"q": query}))
    assert result["catalog_version"] == ready["catalog_version"], result
    assert result["search_mode"] == "lexical", result
    assert result["results"], query
    top4 = result["top4"]
    families = {card["family_id"] for card in top4}
    assert len(top4) <= 4 and len(top4) == len(families), result
    if query.startswith("антивирус"):
        assert len(families) == 4, result
    for card in result["recommendations"]:
        assert card["match_type"] == "recommendation" and card["compatibility"]["matched_platforms"], card
    if query.startswith("КриптоПро"):
        for card in result["results"]:
            assert re.search(r"(?<![\w.])5\.0(?![\w.])", card["attributes"].get("Версия") or card["name"])
office = get("/search?" + urlencode({"q": office_id, "semantic": "false"}))
assert office["results"][0]["offer_id"] == office_id, office
assert len(office["recommendations"]) > 0, office
assert len({item["family_id"] for item in office["top4"]}) == len(office["top4"]), office
assert all(item["compatibility"]["matched_platforms"] for item in office["recommendations"]), office
print("Полный каталог, разные семейства и допродажи с совпадением платформы проверены")
