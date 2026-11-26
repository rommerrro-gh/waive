import gzip
from contextlib import contextmanager
from decimal import Decimal

from defusedxml.ElementTree import iterparse

ATTRIBUTES = {
    "Версия", "Модель лицензирования", "Срок действия", "Тип продукта", "Тип поставки",
    "Лицензионная программа", "Совместимая архитектура/платформа", "Пользователь",
}
EMPTY_PARAMETER = "другое или отсутствует"


@contextmanager
def open_feed(path):
    with path.open("rb") as source:
        compressed = source.read(2) == b"\x1f\x8b"
        source.seek(0)
        if compressed:
            with gzip.GzipFile(fileobj=source) as xml:
                yield xml
        else:
            yield source


def read_feed(source):
    stack = []
    for event, element in iterparse(source, events=("start", "end")):
        if event == "start":
            stack.append(element)
            if len(stack) == 1:
                if element.tag != "yml_catalog":
                    raise ValueError("Ожидался корневой элемент yml_catalog")
                yield "date", element.get("date")
            continue
        if element.tag == "category":
            yield "category", (element.attrib["id"], element.get("parentId"), element.text or "")
        elif element.tag == "offer":
            yield "offer", parse_offer(element)
        if element.tag in {"category", "offer"}:
            stack[-2].remove(element)
        stack.pop()


def parse_offer(element):
    def field(tag):
        return element.findtext(tag, "").strip()

    attributes = {}
    for parameter in element.findall("param"):
        value = (parameter.text or "").strip()
        if value and value.casefold() != EMPTY_PARAMETER and parameter.get("name"):
            attributes[parameter.attrib["name"]] = value
    name = field("name")
    if not name:
        raise ValueError(f"Пустое название для предложения {element.get('id')}")
    return (
        element.attrib["id"], element.get("group_id") or field("group_id") or None,
        field("categoryId") or None, name, field("vendor"), field("vendorCode") or None,
        field("url") or None, Decimal(field("price")) if field("price") else None,
        field("currencyId") or None, element.get("available") == "true",
        field("description"), attributes,
    )
