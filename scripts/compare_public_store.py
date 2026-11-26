"""Снять позиции публичной выдачи Softline для 33 заранее известных запросов."""

import argparse
import collections
import gzip
import json
import math
import re
import statistics
import time
from pathlib import Path
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from benchmark_search import INTENTS
from evaluate import CASES

ORIGIN = "https://store.softline.ru/"
FAMILY_PATTERN = re.compile(r"family_id\s*:\s*['\"]?(\d+)")


class SearchPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = {}
        self.family_ids = {}
        self.heading = False
        self.headers = []

    def handle_starttag(self, tag, attributes):
        if tag == "h1":
            self.heading = True
        if tag != "a":
            return
        attributes = dict(attributes)
        href = urlsplit(urljoin(ORIGIN, attributes.get("href") or ""))
        params = parse_qs(href.query)
        position = params.get("position", [""])[0]
        if href.netloc != "store.softline.ru" or "search_request" not in params or position not in {"1", "2", "3", "4"}:
            return
        rank = int(position)
        if "b-product-item__image-box" in (attributes.get("class") or "").split():
            self.cards[rank] = urljoin(ORIGIN, href.path)
        family = FAMILY_PATTERN.search(attributes.get("onclick") or "")
        if family:
            self.family_ids[rank] = family.group(1)

    def handle_endtag(self, tag):
        if tag == "h1":
            self.heading = False

    def handle_data(self, data):
        if self.heading:
            self.headers.append(data.strip())


def catalog_categories(feed):
    mapping = collections.defaultdict(set)
    with gzip.open(feed, "rb") as source:
        for _, node in ET.iterparse(source):
            if node.tag == "offer":
                group = node.get("group_id") or node.findtext("group_id")
                if group:
                    mapping[group].add(node.findtext("categoryId"))
                node.clear()
    return mapping


def collect(query):
    url = urljoin(ORIGIN, "search/?" + urlencode({"query": query}))
    started = time.monotonic()
    with urlopen(Request(url, headers={"User-Agent": "Waive catalog comparison (one request at a time)"}), timeout=20) as response:
        if urlsplit(response.url).hostname != "store.softline.ru":
            raise ValueError("Переадресация за пределы магазина")
        html = response.read(3000000).decode("utf-8", "replace")
    delay = round((time.monotonic() - started) * 1000, 1)
    parser = SearchPage()
    parser.feed(html)
    heading = " ".join(parser.headers)
    headings_match = "по запросу" in heading.casefold() and query.casefold() in heading.casefold()
    empty = re.search(r"ничего не найдено|не удалось найти|нет результатов|найдено\s+0\b", heading, re.I)
    if not headings_match and not empty:
        raise ValueError("Не подтверждена страница результатов поиска")
    if not parser.cards and not empty:
        raise ValueError("Карточки не разобраны, пустая выдача не подтверждена")
    if parser.cards and sorted(parser.cards) != list(range(1, len(parser.cards) + 1)):
        raise ValueError("Не удалось восстановить непрерывный порядок карточек")
    return delay, [
        {"rank": rank, "url": parser.cards[rank], "public_family_id": parser.family_ids.get(rank)}
        for rank in sorted(parser.cards)
    ]


def classify(query, field, accepted, rows, categories):
    for card in rows:
        family = card["public_family_id"]
        path = urlsplit(card["url"]).path.casefold()
        if field == "group_id":
            good = family in accepted
        elif field == "vendor":
            good = path.startswith("/webinar/")
        else:
            good = bool(categories.get(family, set()).intersection(accepted))
            if query == "кибер бэкап":
                good = good and path.startswith("/cyberprotect/")
        if good:
            return card["rank"]
    return None


def measure(feed):
    categories = catalog_categories(feed)
    requests = [
        ("Эталоны Softline" if source == "эталон" else "Сценарии настройки", query, field,
         (expected,) if isinstance(expected, str) else expected)
        for source, query, field, expected in CASES
    ] + [("Пользовательские задачи", query, "category_id", expected) for query, expected in INTENTS]
    rows = []
    for index, (segment, query, field, expected) in enumerate(requests, 1):
        try:
            elapsed, cards = collect(query)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            code = getattr(error, "code", None)
            rows.append({"query": query, "segment": segment, "error": str(error)})
            print(f"{index}/{len(requests)}: выдача не подтверждена", flush=True)
            if code in (403, 429):
                break
        else:
            rank = classify(query, field, expected, cards, categories)
            rows.append({"query": query, "segment": segment, "latency_ms": elapsed,
                         "first_relevant": rank, "cards": cards})
            print(f"{index}/{len(requests)}: {len(cards)} карточек", flush=True)
        time.sleep(1.0)
    summaries = {}
    for segment in ("Все", "Эталоны Softline", "Пользовательские задачи", "Сценарии настройки"):
        relevant = [r for r in rows if "error" not in r and (segment == "Все" or r["segment"] == segment)]
        latencies = sorted(row["latency_ms"] for row in relevant)
        summaries[segment] = {
            "planned": sum(segment == "Все" or row[0] == segment for row in requests),
            "captured": len(relevant),
            "top1": sum(row["first_relevant"] == 1 for row in relevant),
            "top4": sum(row["first_relevant"] is not None for row in relevant),
            "mrr4": round(statistics.mean(1 / row["first_relevant"] if row["first_relevant"] else 0
                                          for row in relevant), 4) if relevant else None,
            "empty": sum(not row["cards"] for row in relevant),
            "unclassified_cards": sum(card["public_family_id"] is None
                                      for row in relevant for card in row["cards"]),
            "p95_ms_external": latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
        }
    return summaries, rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", type=Path, required=True)
    args = parser.parse_args()
    summary, rows = measure(args.feed)
    print(json.dumps(summary, ensure_ascii=False))
    Path("softline-live-results.json").write_text(
        json.dumps({"summary": summary, "queries": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
