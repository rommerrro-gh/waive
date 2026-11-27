import os
import re
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import psycopg
from psycopg.rows import dict_row

from app.search import load_category_coverage, vpn_offer


def get(path, headers=None, body=None):
    request = Request("http://127.0.0.1:8000" + path, headers=headers or {}, data=body)
    with urlopen(request, timeout=5) as response:
        return response.status if response.status == 204 else json.load(response)


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

with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
    coverage = load_category_coverage(connection, get("/ready")["catalog_version"])
    assert len(coverage) == 335
    for category, rule in (("1145", "platform"), ("86563", "server"), ("70824", "usb"),
                           ("75936", "socket"), ("85741", "optics"), ("93995", "printer")):
        assert any(relation.check == rule for relation, _ in coverage[category]), category
    assert not coverage["82833"]

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

compact = get("/search?q=Kaspersky&semantic=false")
expanded = get("/search?q=Kaspersky&semantic=false&expanded=true")
assert len(expanded["results"]) > 12, expanded
assert expanded["results"][:4] == compact["results"] == expanded["top4"]
assert len({card["family_id"] for card in expanded["results"]}) == len(expanded["results"])
for query in ("касперский", "касперчкий", "каспер", "Kaspresky"):
    repaired = get("/search?" + urlencode({"q": query, "expanded": "true"}))
    assert repaired["corrected_query"] == "Kaspersky" and repaired["top4"] == expanded["top4"], repaired
    assert all("касперского" in card["vendor"].casefold() for card in repaired["results"])
for prefix in ("касп", "каспер", "касперчкий", "Kasper"):
    suggestions = get("/suggest?" + urlencode({"q": prefix}))["suggestions"]
    assert suggestions[0] == "Kaspersky" and 1 < len(suggestions) <= 6, suggestions
    assert len(suggestions) == len(set(suggestions)), suggestions
for prefix, fragment in (
    ("майкр", "Microsoft"), ("доктор веб", "Dr.Web"), ("анидеск", "AnyDesk"),
    ("винрар", "WinRAR"), ("астра", "Astra"), ("логитек", "Logitech"), ("Logitech", "Logitech"),
):
    suggestions = get("/suggest?" + urlencode({"q": prefix}))["suggestions"]
    assert any(fragment in option for option in suggestions), (prefix, suggestions)

for query, fragment in (
    ("анидеск", "anydesk"), ("фтнвуыл", "anydesk"), ("Any\u00a0Desk", "anydesk"),
    ("rfcgthcrbq", "kaspersky"), ("майкр", "microsoft"), ("майкрософт Visio", "visio"),
    ("винрар", "winrar"), ("логитек", "logitech"), ("920-009393", "920-009393"),
):
    query_params = urlencode({"q": query})
    result = get("/search?" + query_params)
    assert any(fragment in card["name"].casefold() for card in result["results"]), (query, result)
    assert get("/suggest?" + query_params)["suggestions"], query
assert any("Microsoft Office" in name for name in get("/suggest?q=" + office_id)["suggestions"])

for query in ("", " ", "а", "???", "x" * 201):
    try:
        get("/search?" + urlencode({"q": query}))
    except HTTPError as error:
        assert error.code == 422, (query, error.code)
    else:
        raise AssertionError("Принят некорректный запрос: " + repr(query))
for query in ("", " ", "а", "???", "zqjy939"):
    assert get("/suggest?" + urlencode({"q": query}))["suggestions"] == [], query
assert get("/search?q=zqjy939")["results"] == []
typo = "каспрсикй"
assert "Kaspersky" in get("/suggest?" + urlencode({"q": typo}))["suggestions"]
repaired = get("/search?" + urlencode({"q": typo}))
assert repaired["corrected_query"] == "Kaspersky" and repaired["results"]
assert all("касперского" in card["vendor"].casefold() for card in repaired["results"])
for query, label in (("качпеоский", "Kaspersky"), ("криппопро tls", "КриптоПро tls"),
                     ("Kaspresky VPN", "Kaspersky VPN"), ("Kaspresky впн", "Kaspersky VPN")):
    params = urlencode({"q": query})
    assert get("/suggest?" + params)["suggestions"][0].casefold() == label.casefold(), query
    repaired = get("/search?" + params)
    assert repaired["corrected_query"].casefold() == label.casefold() and repaired["results"], repaired
for query, label in (("мимкосотф", "Microsoft"), ("адинэсс", "AnyDesk"), ("вмнвэар", "VMware"),
                     ("криптограф", "КриптоПро"), ("логотека", "Logitech"), ("автодиски", "Autodesk")):
    params = urlencode({"q": query})
    repaired = get("/search?" + params)
    assert repaired["corrected_query"] is None and label in repaired["spelling_suggestions"], repaired
    assert get("/suggest?" + params)["suggestions"] == repaired["spelling_suggestions"], query
    assert get("/search?" + urlencode({"q": label}))["results"], label
for query in ("впн", "ВПН"):
    params = urlencode({"q": query, "semantic": "false"})
    result = get("/search?" + params)
    assert result["corrected_query"] == "VPN" and result["results"], result
    assert result["results"] == get("/search?q=VPN&semantic=false")["results"]
    suggestions = get("/suggest?" + params)["suggestions"]
    assert suggestions[0] == "VPN" and suggestions == get("/suggest?q=VPN")["suggestions"]
for query in ("впн", "VPN для бизнеса", "виртуальная частная сеть", "Kaspresky VPN"):
    response = get("/search?" + urlencode({"q": query, "expanded": "true"}))
    assert response["results"] and all(vpn_offer(item, response["corrected_query"] or query) for item in response["results"]), response
    assert not response["recommendations"]
    assert all(item["available"] for item in response["results"])
    assert all(item["offer_id"] != "176956" for item in response["top4"])
assert get("/search?q=176956")["results"][0]["offer_id"] == "176956"
for query, family in (("AnyDesk", "306539"), ("Microsoft Visio Standard 2024", "391350")):
    response = get("/search?" + urlencode({"q": query, "expanded": "true"}))
    assert {item["group_id"] for item in response["results"]} == {family}, response
typo_title = urlencode({"q": "Kaspresky Passwrod Manager"})
assert "Kaspersky Password Manager" in get("/suggest?" + typo_title)["suggestions"]
assert get("/search?" + typo_title)["corrected_query"] == "Kaspersky Password Manager"
weak_brand = get("/search?" + urlencode({"q": "майкрасавт"}))
assert weak_brand["corrected_query"] is None and "Microsoft" in weak_brand["spelling_suggestions"]

assert isinstance(get("/quote/config")["automatic_delivery"], bool)
try:
    get("/quote/request", {"Content-Type": "application/json"}, b"{}")
except HTTPError as error:
    assert error.code == 422
else:
    raise AssertionError("Форма КП принята без контактов")
office = get("/search?" + urlencode({"q": office_id, "semantic": "false"}))
assert office["results"][0]["offer_id"] == office_id, office
assert len(office["recommendations"]) > 0, office
assert len({item["family_id"] for item in office["top4"]}) == len(office["top4"]), office
assert all(item["compatibility"]["matched_platforms"] for item in office["recommendations"]), office
for offer_id, status in (("460304", "confirmed"), ("438239", "needs_confirmation"),
                         ("124285", "needs_confirmation"), ("533065", "not_applicable")):
    bundle_result = get("/bundle?" + urlencode({"offer_id": offer_id, "catalog_version": ready["catalog_version"]}))
    assert bundle_result["status"] == status, bundle_result
    if status == "confirmed":
        assert all(item["category_id"] == "82833" and item["compatibility"]["evidence"]
                   and item["compatibility"]["source_offer_id"] == offer_id for item in bundle_result["items"])
    else:
        assert not bundle_result["items"]

replacement = get("/search?q=125344")
assert replacement["results"][0]["offer_id"] == "125344" and not replacement["results"][0]["available"]
assert len(replacement["alternatives"]) == 4
assert replacement["top4"] == replacement["alternatives"]
assert all(item["available"] and item["category_id"] == "70824" for item in replacement["alternatives"])
assert not get("/search?q=413308")["alternatives"]
patrol = get("/search?q=max+patrol")
assert patrol["corrected_query"] == "MaxPatrol" and patrol["results"][0]["group_id"] == "437421"

strict = get("/search?" + urlencode({"q": "защита для Windows 11, на 50 рабочих мест, без подписки"}))
assert not strict["top4"] and "без подписки" in strict["filter_diagnostics"]["requirements_unmet"], strict
assert not get("/search?" + urlencode({"q": "КриптоПро CSP 9.9"}))["top4"]
astra = get("/search?q=Astra+Linux")["results"]
assert astra and all(item["category_id"] == "222320" and item["name"].startswith("Astra Linux") for item in astra)
print("Полный каталог, разные семейства и допродажи с совпадением платформы проверены")

priced = next(card for card in office["top4"] if card["available"] and card["price"])
amount = float(priced["price"])
lower, upper = int(amount), int(amount) + 1
filtered = get("/search?" + urlencode({
    "q": priced["offer_id"], "semantic": "false", "in_stock": "true", "price_min": lower, "price_max": upper,
}))
assert filtered["results"][0]["offer_id"] == priced["offer_id"], filtered
assert all(item["available"] and lower <= float(item["price"]) <= upper for item in filtered["top4"])
try:
    get("/search?q=Office&price_min=200&price_max=100")
except HTTPError as error:
    assert error.code == 422
else:
    raise AssertionError("Перевёрнутый диапазон цены принят")


from app.procurement import purchase_values

query = "антивирус для рабочих станций компании"
matched = get("/search?" + urlencode({
    "q": query, "platform": "windows", "term": "12", "track": "true", "demo": "true", "with_profile": "true",
}))
assert matched["results"], matched
for item in matched["results"]:
    values = purchase_values(item["attributes"])
    assert "windows" in values["platform"] and "12" in values["term"], item
miss = get("/search?" + urlencode({"q": query, "term": "119", "track": "true", "demo": "true"}))
assert not miss["results"], miss
assert miss["filter_diagnostics"]["conditions"]["term"]["would_restore"] > 0, miss

assert get("/analytics/events/click", {"Content-Type": "application/json"}, json.dumps({
    "request_id": matched["request_id"], "offer_id": matched["results"][0]["offer_id"],
}).encode()) == 204
try:
    get("/analytics/report")
except HTTPError as error:
    assert error.code == 403
else:
    raise AssertionError("Отчёт доступен без ключа")

headers = {"X-Analytics-Token": os.environ["ANALYTICS_TOKEN"]}
report = get("/analytics/report?demo=true", headers)
assert report["metrics"]["searches"] == 2 and report["metrics"]["empty"] == 1, report
assert report["metrics"]["direct_clicks"] == 1, report
assert any(row["reason"] == "filter.term" for row in report["groups"]), report
assert get("/analytics/report", headers)["metrics"]["searches"] == 0
print("Проверены закупочные условия, причины промахов и отдельная статистика демо")

assert get("/analytics/report?demo=true&with_profile=true", headers)["metrics"]["searches"] == 1
assert get("/analytics/report?demo=true&with_profile=false", headers)["metrics"]["empty"] == 1
bundle = get("/bundle?" + urlencode({"offer_id": office_id, "catalog_version": ready["catalog_version"]}))
assert bundle["anchor"]["offer_id"] == office_id and bundle["items"], bundle
for item in bundle["items"]:
    assert item["compatibility"]["source_offer_id"] == office_id, item
    assert item["compatibility"]["matched_platforms"], item
try:
    get("/bundle?" + urlencode({"offer_id": office_id, "catalog_version": ready["catalog_version"] + 1}))
except HTTPError as error:
    assert error.code == 409
else:
    raise AssertionError("Комплект должен собираться на версии каталога из выдачи")
