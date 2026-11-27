"""Transliterated manufacturer names used to shortlist candidates for E5."""

import re
from html import unescape

from app.spelling import TRANSLITERATION, words

LATIN_NAME = re.compile(r"[a-z\d]+(?: [a-z\d]+){0,2}")
LEADING_CYRILLIC = re.compile(r"[а-яё]+(?: [а-яё]+){0,2}(?!\w)", re.IGNORECASE)


def spelling_key(name):
    value = " ".join(words(name)).translate(TRANSLITERATION)
    value = re.sub(r"c(?=[eiy])", "s", value)
    return (value.replace("ph", "f").replace("kh", "h").replace("c", "k")
            .replace("y", "i").replace("w", "v").replace("x", "ks"))


class VendorSpellings:
    def __init__(self, names):
        self.by_spelling = {}
        for name in names:
            label = unescape(name).strip()
            normalized = " ".join(words(label))
            if LATIN_NAME.fullmatch(normalized) and re.search("[a-z]", normalized):
                key = spelling_key(label)
                self.by_spelling.setdefault(key, {})[label.casefold()] = label
                first, separator, _ = key.partition(" ")
                if separator and len(first) >= 4:
                    self.by_spelling.setdefault(first, {})[label.casefold()] = label

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
        if complete_prefix and query == leading[0] and " " not in query:
            key = spelling_key(query)
            matches = [label for spelling, names in self.by_spelling.items()
                       if spelling.startswith(key) for label in names.values()]
            return query, "", sorted(matches, key=lambda label: (len(label), label.casefold()))[:12], False
        return "", "", [], False
