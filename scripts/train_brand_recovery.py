"""Train the portable brand classifier locally. Requires NumPy for training only."""

import gzip
import json
import string
from pathlib import Path
from random import Random

import numpy as np

from app.brand_recovery import MODEL_PATH, query_features
from app.search import SPELLING_ALIASES
from app.spelling import POSITIONS, TO_LATIN, TO_RUSSIAN, words

CYRILLIC = "абвгдежзийклмнопрстуфхцчшщыьэюя"
NEGATIVES = """
антивирус сервер сервера лицензия подписка антивирусный облако облачный хранилище серверный
компьютер рабочий станция приложение оборудование система сетевой файлы защита файрвол
восстановление офис виртуальный браузер программа рабочая сканирование антивирусник обновление
астрология космос кассир кассировка каспер покупатель бухгалтерия резервирование айсберг тестирование
компания телефон клавиатура редактор очередь кабель диск драйвер драйвера приложение оплата поставка
договор семейство поддержка запрос поместье паспортизация windows desktop workstation security office
server backup antivirus recovery unknown docker Python linux sql cloud photoshop reviewer questions
analysis decoder compatible distribute developer toolkit device port store softline ноутбук монитор
маршрутизатор клавиша роутер настройка синхронизация обслуживание текстовый майка ноутбуки
кассы каспийский каспий кассовый кассирский каспиец каперский каперс каперсы
микроскоп микросхема микропроцессор микроскопия макросъемка логистика логичный
криптовалюта криптография криптология аспирин астрономия астрономический астероид
""".split()
NEIGHBORS = {
    key: tuple(other for other, (yy, xx) in POSITIONS.items() if other.isascii() == key.isascii()
               and abs(y - yy) + abs(x - xx) == 1)
    for key, (y, x) in POSITIONS.items()
}


def corrupt(text, edits, rng):
    chars = list(text)
    alphabet = CYRILLIC if not text.isascii() else string.ascii_lowercase
    for _ in range(edits):
        position = rng.randrange(len(chars))
        operation = rng.randrange(4)
        if operation == 0 and len(chars) > 3:
            chars.pop(position)
        elif operation == 1:
            chars.insert(position, rng.choice(alphabet))
        elif operation == 2 and position + 1 < len(chars):
            chars[position:position + 2] = reversed(chars[position:position + 2])
        else:
            nearby = NEIGHBORS.get(chars[position])
            chars[position] = rng.choice(nearby if nearby and rng.random() < .65 else alphabet)
    return "".join(chars)


def training_examples(rng, spellings):
    for label, variants in spellings.items():
        for _ in range(950):
            text = rng.choice(variants)
            if rng.random() < .17:
                text = text.translate(TO_RUSSIAN if text.isascii() else TO_LATIN)
            edits = rng.choices((0, 1, 2, 3, 4, 5), (1, 2, 4, 6, 6, 3))[0]
            yield label, corrupt(text, edits, rng)
    for _ in range(7500):
        text = rng.choice(NEGATIVES)
        if rng.random() < .26:
            alphabet = rng.choice((CYRILLIC, string.ascii_lowercase))
            text = "".join(rng.choices(alphabet, k=rng.randint(5, 15)))
        yield "", corrupt(text, rng.randrange(3), rng)


def train():
    rng = Random(486019)
    spellings = {label: sorted({" ".join(words(name)) for name in (label, *names)})
                 for label, names in SPELLING_ALIASES.items()}
    training = [(label, query_features(words(query), spellings))
                for label, query in training_examples(rng, spellings)]
    labels = sorted({label for label, _ in training})
    label_index = {label: index for index, label in enumerate(labels)}
    counts = {}
    for _, features in training:
        for feature in features:
            counts[feature] = counts.get(feature, 0) + 1
    names = sorted(name for name, count in counts.items() if count >= 18)
    feature_index = {name: index for index, name in enumerate(names)}
    data = np.zeros((len(training), len(names)), dtype=np.float32)
    targets = []
    for row, (label, features) in enumerate(training):
        targets.append(label_index[label])
        for feature, value in features.items():
            if feature in feature_index:
                data[row, feature_index[feature]] = value
    targets = np.asarray(targets)
    weights = np.zeros((len(names), len(labels)), dtype=np.float32)
    bias = np.zeros(len(labels), dtype=np.float32)
    random = np.random.default_rng(486019)
    for epoch in range(34):
        step = 1.8 / (1 + epoch * .04)
        for batch in np.array_split(random.permutation(len(data)), 48):
            inputs = data[batch]
            logits = inputs @ weights + bias
            softmax = np.exp(logits - logits.max(axis=1, keepdims=True))
            softmax /= softmax.sum(axis=1, keepdims=True)
            softmax[np.arange(len(batch)), targets[batch]] -= 1
            weights -= step * (inputs.T @ softmax / len(batch) + .00007 * weights)
            bias -= step * softmax.mean(axis=0)
    return {"labels": labels, "bias": bias.round(4).tolist(), "spellings": spellings,
            "weights": {name: row.round(4).tolist() for name, row in zip(names, weights, strict=True)}}


if __name__ == "__main__":
    payload = json.dumps(train(), ensure_ascii=False, separators=(",", ":")).encode()
    Path(MODEL_PATH).write_bytes(gzip.compress(payload, mtime=0))
