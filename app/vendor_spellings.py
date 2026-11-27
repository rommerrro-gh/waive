"""Transliterated manufacturer names used to shortlist candidates for E5."""

import re
from html import unescape

from app.spelling import TRANSLITERATION, words

# Каталожное указание на совместимость включает марку оборудования и модель с цифрами.
FIT_REFERENCE = (r"(?:для|совместим[а-я]* с)[[:space:]]+([A-Z][A-Z0-9-]{2,24})"
                 r"[[:space:]]+(?=[A-Z0-9 /-]*[0-9])")
LATIN_NAME = re.compile(r"[a-z\d]+(?: [a-z\d]+){0,2}")
VOWELS = re.compile(r"[aeiou]")
LEADING_CYRILLIC = re.compile(r"[а-яё]+(?: [а-яё]+){0,2}(?!\w)", re.IGNORECASE)


def spelling_key(name):
    value = " ".join(words(name)).translate(TRANSLITERATION)
    value = re.sub(r"c(?=[eiy])", "s", value)
    return (value.replace("ph", "f").replace("kh", "h").replace("c", "k")
            .replace("y", "i").replace("w", "v").replace("x", "ks"))


def vowel_keys(value):
    letters = value.replace(" ", "")
    vowels = list(VOWELS.finditer(letters))
    if len(letters) < 5 or len(letters) - len(vowels) < 3:
        return
    for vowel in VOWELS.finditer(value):
        yield value[:vowel.start()] + "*" + value[vowel.end():]


class VendorSpellings:
    def __init__(self, names, compatible_names=()):
        names = list(names)
        manufacturers = {unescape(name).strip().casefold() for name in names}
        self.compatible = {name.casefold(): name for name in compatible_names
                           if name.casefold() not in manufacturers}
        self.by_spelling = {}
        for name in (*names, *self.compatible.values()):
            label = unescape(name).strip()
            normalized = " ".join(words(label))
            if LATIN_NAME.fullmatch(normalized) and re.search("[a-z]", normalized):
                key = spelling_key(label)
                self.by_spelling.setdefault(key, {})[label.casefold()] = label
                first, separator, _ = key.partition(" ")
                if separator and len(first) >= 4:
                    self.by_spelling.setdefault(first, {})[label.casefold()] = label

        self.by_pronunciation = {}
        self.by_vowel = {}
        for key, labels in self.by_spelling.items():
            for variant in vowel_keys(key):
                self.by_vowel.setdefault(variant, {}).update(labels)
            if "th" in key:
                for letter in "zdt":
                    pronunciation = key.replace("th", letter)
                    for variant in (pronunciation, *vowel_keys(pronunciation)):
                        self.by_pronunciation.setdefault(variant, {}).update(labels)

    def candidates(self, query, *, complete_prefix=False):
        leading = LEADING_CYRILLIC.match(query)
        if not leading or len(leading[0].split()[0]) < 4:
            return "", "", [], False
        head = leading[0]
        while head:
            key = spelling_key(head)
            matches = self.by_spelling.get(key)
            if matches:
                return head, query[len(head):], list(matches.values())[:12], True
            head = head.rpartition(" ")[0]
        head = leading[0]
        while head:
            key = spelling_key(head)
            matches = {}
            for variant in (key, *vowel_keys(key)):
                matches.update(self.by_pronunciation.get(variant, {}))
            if query[len(head):].strip(" ,"):
                for variant in vowel_keys(key):
                    matches.update(self.by_vowel.get(variant, {}))
            if matches:
                return head, query[len(head):], list(matches.values())[:12], True
            head = head.rpartition(" ")[0]
        if complete_prefix and query == leading[0] and " " not in query:
            key = spelling_key(query)
            matches = [label for spelling, names in self.by_spelling.items()
                       if spelling.startswith(key) for label in names.values()]
            return query, "", sorted(matches, key=lambda label: (len(label), label.casefold()))[:12], False
        return "", "", [], False
