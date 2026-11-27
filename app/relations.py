"""Category rules and offer-level evidence for recommendations."""

import re
from dataclasses import dataclass
from functools import lru_cache

from app.requirements import catalog_os, os_matches


@dataclass(frozen=True)
class Relation:
    source: str
    target: str
    check: str
    label: str


WORKSTATION = r"(?:^| / )(?:Ноутбуки|Моноблоки|Системные блоки|Рабочие станции)$"
DESKTOP_SOFTWARE = r"^Офисные программы(?: / |$)|^Операционные системы(?: / |$)"
INFRASTRUCTURE = r"(?:^Облачные решения \(IaaS\)|серверы$|серверное по$|виртуализац[^/]*$|системы хранения данных$|хранилища \(nas\)$)"

RELATIONS = (
    Relation(DESKTOP_SOFTWARE + "|" + WORKSTATION, r"^650$", "platform", "Защита рабочих станций"),
    Relation(DESKTOP_SOFTWARE + "|" + WORKSTATION, r"^1033$", "platform", "Резервное копирование"),
    Relation(INFRASTRUCTURE, r"^1033$", "server", "Резервное копирование инфраструктуры"),
    Relation(INFRASTRUCTURE, r"мониторинг|техническая поддержка", "product", "Мониторинг или поддержка выбранной системы"),
    Relation(WORKSTATION, r"^82833$", "usb", "Устройства ввода"),
    Relation(r"Ноутбуки$", r"^84382$", "dock", "Док-станция"),
    Relation(r"материнские платы", r"процессоры", "socket", "Процессор"),
    Relation(r"материнские платы", r"^76963$", "memory", "Оперативная память"),
    Relation(r"материнские платы$|серверы$|" + WORKSTATION, r"^78997$|^79574$", "drive", "Накопитель"),
    Relation(r"коммутаторы$|маршрутизаторы$", r"трансивер|медиаконвертер", "optics", "Сетевой модуль"),
    Relation(r"принтеры(?: и мфу)?$|3d.*принтер", r"картридж|тонер|расходные материалы|^74002$|^98141$", "printer", "Расходные материалы"),
    Relation(r"ИБП \(Источники бесперебойного питания\)$", r"^82014$", "product", "Сменная батарея"),
    Relation(r"Ноутбуки$", r"^84326$|^84437$|^83844$", "product", "Аксессуар для выбранной модели"),
    Relation(r"смартфоны$|планшеты$", r"чехлы для смартфонов|чехлы для планшетов|зарядные устройства", "product", "Аксессуар для выбранной модели"),
    Relation(r"серверы$|коммутаторы$|маршрутизаторы$|межсетевые экраны$|системы хранения данных$|хранилища \(nas\)$",
             r"опции|аксессуар|поддержка оборудования", "product", "Опции или сервис для выбранной модели"),
    Relation(r"сапр|офисные программы|операционн|удаленного доступа|безопасност|облачн|программ|обучен|crm|erp|бухгалтер",
             r"расширения|модули|техническая поддержка программного|обучение", "product", "Расширение, обучение или поддержка продукта"),
)

INTERFACE = re.compile(
    r"(?<!\w)(?:thunderbolt\s*[34]?|usb\s*4|displayport\s+alt\s+mode|usb(?:-c| type-c| 2\.0| 3\.\d)?|"
    r"sfp28|qsfp28|qsfp\+|sfp\+|qsfp|sfp|(?:m\.2|m2)\s*(?:22\d{2})?|sata\s*(?:iii|[123])?|sas|nvme)(?![\w+])", re.I,
)
COMPATIBILITY_FIELDS = ("Совместимые модели", "Совместимые устройства", "Модель принтера",
                        "Совместимые продукты", "Поддерживаемые модели", "Модель совместимого устройства")
UNKNOWN = {"", "не указано", "не определено", "другое или отсутствует", "универсальный"}


@lru_cache(maxsize=64)
def expression(pattern):
    return re.compile(pattern, re.I)


def clean(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def values(card, fields):
    return {clean(card["attributes"].get(field)) for field in fields} - UNKNOWN


def interfaces(card):
    attrs = card["attributes"]
    text = " ".join(f"{name} {value}" for name, value in attrs.items()
                    if (re.search(r"интерфейс|раз[ъь][её]м|(?:^|\s)порт|контроллер сис\. хранения", name, re.I)
                        and clean(value) not in {"0", "нет", "no", "-", ""}))
    matches = set()
    for item in INTERFACE.finditer(text):
        key = item[0].lower().replace(" ", "").replace("type-", "-")
        if key == "usb4":
            matches.add(key)
        elif key.startswith("sata"):
            matches.add("sata")
        elif key in {"usb", "usb2.0", "usb3.0", "usb3.1", "usb3.2"}:
            matches.add("usb")
        else:
            matches.add(key)
    if any(clean(attrs.get(field)) not in {"", "0", "нет", "no", "-"} for field in ("Разъемы Type-C", "Разъёмы Type-C")):
        matches.add("usb-c")
    return matches


def named_match(anchor, offer, fields):
    names = values(anchor, ("Модель", "Модель принтера", "Серия", "Название продукта"))
    if anchor.get("vendor_code"):
        names.add(clean(anchor["vendor_code"]))
    title = clean(anchor["name"])
    for declaration in sorted(values(offer, fields)):
        for term in re.split(r"[,;/]", declaration):
            term = term.strip()
            if len(term) >= 4 and (
                term in names or ((any(char.isdigit() for char in term) or (len(term) >= 8 and " " in term))
                                  and re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", title))
            ):
                return term
    return None


def compatibility_evidence(anchor, offer, check, required_os=()):
    if check in ("platform", "server"):
        anchor_os = catalog_os(anchor, anchor["attributes"])
        current = {label for label in required_os if os_matches(label, anchor_os)} if required_os else anchor_os
        supported = catalog_os(offer, offer["attributes"])
        if check == "server":
            current &= {"linux", "astra linux", "alt linux", "ubuntu", "debian", "centos",
                        "red hat enterprise linux", "freebsd", "vmware esxi"} | {
                item for item in current if item.startswith("windows server")
            }
        shared = sorted(item for item in current if item in supported or (
            item not in {"linux", "windows", "windows server"} and os_matches(item, supported)
        ))
        if shared:
            return {"matched_platforms": shared, "evidence": ["Поддержка ОС: " + ", ".join(shared)]}
        return None

    if check in ("printer", "product"):
        match = named_match(anchor, offer, COMPATIBILITY_FIELDS)
        if match:
            return {"evidence": ["В каталоге указана модель: " + match]}
        return None

    if check == "socket":
        def sockets(card):
            return {re.sub(r"\s+", "", re.sub(r"^(?:socket|сокет)\s+", "", value))
                    for value in values(card, ("Сокет",))}

        common = sockets(anchor) & sockets(offer)
        return {"evidence": ["Совпадает сокет: " + min(common)]} if common else None

    if check == "memory":
        memory = values(anchor, ("Тип памяти",)) & values(offer, ("Тип памяти",))
        form = values(anchor, ("Форм-фактор памяти", "Тип модуля")) & values(
            offer, ("Форм-фактор памяти", "Тип модуля", "Форм-фактор"))
        return {"evidence": [f"Совпадают тип памяти и формат: {min(memory)}, {min(form)}"]} if memory and form else None

    shared = interfaces(anchor) & interfaces(offer)
    if check == "usb":
        shared &= {"usb", "usb-c"}
    elif check == "dock":
        shared &= {"thunderbolt3", "thunderbolt4", "usb4", "displayportaltmode"}
    elif check == "optics":
        shared &= {"sfp", "sfp+", "sfp28", "qsfp", "qsfp+", "qsfp28"}
        # Одного физического порта для совместимости оптического модуля недостаточно.
        if not named_match(anchor, offer, COMPATIBILITY_FIELDS):
            return None
    elif check == "drive":
        shared &= {"sata", "sas", "nvme"}
        formats = values(anchor, ("Форм-фактор накопителя", "Поддерживаемый форм-фактор накопителя")) & values(
            offer, ("Форм-фактор", "Форм-фактор накопителя"))
        if not formats:
            return None
    if shared:
        return {"evidence": ["Совпадает интерфейс: " + ", ".join(sorted(shared))]}
    return None


def comparable_offer(anchor, offer):
    """An unavailable SKU supplies reference facts for possible equivalents."""
    target_os = catalog_os(offer, offer["attributes"])
    if any(not os_matches(label, target_os) for label in catalog_os(anchor, anchor["attributes"])):
        return False
    for fields in (
        ("Форм-фактор",), ("Сокет",), ("Тип памяти",), ("Модель принтера",),
        ("Тип поставки",), ("Тип лицензии",), ("Редакция",), ("Тип организации",), ("Назначение",),
    ):
        expected = values(anchor, fields)
        if expected and not expected & values(offer, fields):
            return False
    if ("программ" in clean(anchor["attributes"].get("Тип продукта"))
            and not values(anchor, ("Назначение",))):
        # В одном листе каталога встречаются разные программные продукты.
        vendor = set(re.findall(r"[^\W\d_]+", clean(anchor.get("vendor"))))
        ignored = vendor | {"лицензия", "лицензии", "программа", "расширение", "пакет", "комплект"}
        signature = [token for token in re.findall(r"[^\W\d_]+", clean(anchor["name"]))
                     if len(token) >= 3 and token not in ignored][:2]
        if len(signature) < 2 or not set(signature) <= set(re.findall(r"[^\W\d_]+", clean(offer["name"]))):
            return False
    anchor_interfaces = interfaces(anchor)
    return not anchor_interfaces or anchor_interfaces <= interfaces(offer)


def alternative_difference(anchor, offer):
    if anchor["vendor"] and offer["vendor"] and clean(anchor["vendor"]) != clean(offer["vendor"]):
        return "Другой производитель: " + offer["vendor"]
    for field in ("Редакция", "Версия", "Модель", "Семейство", "Срок действия",
                  "Модель процессора", "Диагональ экрана", "Ёмкость диска"):
        original = anchor["attributes"].get(field)
        replacement = offer["attributes"].get(field)
        if clean(original) not in UNKNOWN and clean(replacement) not in UNKNOWN and clean(original) != clean(replacement):
            return field + ": " + str(replacement) + " вместо " + str(original)
    return "Другой артикул: " + offer["vendor_code"] if offer.get("vendor_code") else ""


def category_coverage(categories, used_ids):
    """Resolve inherited source rules for every category containing an offer."""
    def path(category):
        names, seen = [], set()
        while category in categories and category not in seen:
            seen.add(category)
            name, category = categories[category]
            names.append(name)
        return " / ".join(reversed(names))

    paths = {category: path(category) for category in used_ids}
    targets = {
        relation: tuple(category for category, label in sorted(paths.items())
                        if expression(relation.target).search(category)
                        or expression(relation.target).search(label.rsplit(" / ", 1)[-1]))
        for relation in RELATIONS
    }
    return {
        category: tuple((relation, targets[relation]) for relation in RELATIONS
                        if targets[relation] and expression(relation.source).search(label))
        for category, label in paths.items()
    }
