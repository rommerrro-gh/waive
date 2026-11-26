import re

WORDS = re.compile(r"[^\W_]+", re.UNICODE)
NUMBERS = re.compile(r"\d+(?:[.,]\d+)*")
LATIN_ROWS = ("qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,.")
RUSSIAN_ROWS = ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")
TO_RUSSIAN = str.maketrans("".join(LATIN_ROWS), "".join(RUSSIAN_ROWS))
TO_LATIN = str.maketrans("".join(RUSSIAN_ROWS), "".join(LATIN_ROWS))
HOMOGRAPHS = str.maketrans("aceopxykmbht", "асеорхуктвнт")
TRANSLITERATION = str.maketrans(dict(zip(
    "абвгдежзийклмнопрстуфхцчшщъыьэюя",
    ("a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"),
    strict=True,
)))
POSITIONS = {
    key: (row, column)
    for rows in (LATIN_ROWS, RUSSIAN_ROWS)
    for row, keys in enumerate(rows)
    for column, key in enumerate(keys)
}


def words(text):
    return WORDS.findall(text.casefold().replace("ё", "е"))


def spelling_budget(length):
    return 0 if length < 4 else 100 if length <= 6 else 200 if length <= 8 else 300


def edit_cost(left, right):
    previous = list(range(0, (len(right) + 1) * 100, 100))
    earlier = previous
    for i, a in enumerate(left, 1):
        current = [i * 100]
        for j, b in enumerate(right, 1):
            substitute = 0 if a == b else 100
            if substitute and a in POSITIONS and b in POSITIONS and a.isascii() == b.isascii():
                ay, ax = POSITIONS[a]
                by, bx = POSITIONS[b]
                if abs(ay - by) + abs(ax - bx) == 1:
                    substitute = 75
            cost = min(previous[j] + 100, current[j - 1] + 100, previous[j - 1] + substitute)
            if i > 1 and j > 1 and a == right[j - 2] and left[i - 2] == b:
                cost = min(cost, earlier[j - 2] + 75)
            current.append(cost)
        earlier, previous = previous, current
    return previous[-1]


def rank_spellings(query, candidates):
    source = " ".join(words(query))
    budget = spelling_budget(len(source.replace(" ", "")))
    if not budget:
        return []
    variants = {source: 0}
    letters = {letter.isascii() for letter in source if letter.isalpha()}
    # Смешанную раскладку проверяем целиком, сохраняя исходные цифры и пробелы.
    if True in letters:
        variants.setdefault(source.translate(TO_RUSSIAN), 30)
    if False in letters:
        variants.setdefault(source.translate(TO_LATIN), 30)
        variants.setdefault(source.translate(TRANSLITERATION), 50)
    if len(letters) > 1:
        variants.setdefault(source.translate(HOMOGRAPHS), 25)
    ranked = {}
    for label, spelling in candidates:
        if NUMBERS.findall(query) != NUMBERS.findall(spelling):
            continue
        target = " ".join(words(spelling))
        if not target or abs(len(source) - len(target)) * 100 > budget + 100:
            continue
        cost = min(edit_cost(variant, target) + extra for variant, extra in variants.items()
                   if abs(len(variant) - len(target)) * 100 <= budget + 100)
        if cost <= budget and cost / max(len(source), len(target)) <= 31:
            key = label.casefold()
            score = cost / max(len(source), len(target))
            if key not in ranked or score < ranked[key][0]:
                ranked[key] = (score, label)
    return sorted(ranked.values(), key=lambda entry: (entry[0], len(entry[1]), entry[1]))[:6]


def clear_winner(ranked):
    return bool(ranked and ranked[0][0] <= 25 and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] >= 7))
