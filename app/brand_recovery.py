"""Learned character model for recovering a brand from a misspelling."""

import gzip
import json
import logging
import math
import zlib
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.spelling import edit_cost, words

MODEL_PATH = Path(__file__).with_name("brand_recovery.json.gz")
logger = logging.getLogger(__name__)


def character_features(tokens):
    text = "^" + " ".join(tokens) + "$"
    counts = Counter(
        text[index:index + size]
        for size in (1, 2, 3)
        for index in range(len(text) - size + 1)
    )
    counts.update(
        text[index] + "·" + text[index + gap]
        for gap in range(2, 6)
        for index in range(len(text) - gap)
    )
    scale = math.sqrt(sum(value * value for value in counts.values()))
    return {name: value / scale for name, value in counts.items()}


def query_features(tokens, spellings):
    features = character_features(tokens)
    text = " ".join(tokens)
    for label, variants in spellings.items():
        closest = max(
            (1 - edit_cost(text, variant) / (100 * max(len(text), len(variant)))
             for variant in variants if text.isascii() == variant.isascii()
             and abs(len(text) - len(variant)) <= 4),
            default=0,
        )
        features["match:" + label] = max(0, closest - .45)
        features["strong:" + label] = max(0, closest - .70) * 2
    return features


@lru_cache(maxsize=1)
def load_model():
    try:
        with gzip.open(MODEL_PATH, "rt", encoding="utf-8") as source:
            model = json.load(source)
        labels, bias, weights, spellings = (model[key] for key in ("labels", "bias", "weights", "spellings"))
        if not (
            isinstance(labels, list) and len(labels) >= 2 and all(isinstance(label, str) for label in labels)
            and "" in labels and len(set(labels)) == len(labels)
            and valid_vector(bias, len(labels))
            and isinstance(weights, dict) and weights
            and all(valid_vector(values, len(labels)) for values in weights.values())
            and isinstance(spellings, dict) and spellings.keys() == set(labels) - {""}
            and all(isinstance(aliases, list) and aliases and all(
                isinstance(alias, str) and alias for alias in aliases
            ) for aliases in spellings.values())
        ):
            raise ValueError("структура модели не соответствует формату")
        return labels, bias, weights, spellings
    except (OSError, EOFError, ValueError, TypeError, KeyError, OverflowError, zlib.error) as error:
        logger.warning("Модель опечаток недоступна, используются правила поиска (%s)", type(error).__name__)
        return None


def valid_vector(vector, size):
    return isinstance(vector, list) and len(vector) == size and all(
        type(value) in (int, float) and math.isfinite(value) for value in vector
    )


def brand_prediction(query):
    tokens = words(query)
    if not 1 <= len(tokens) <= 2 or any(not token.isalpha() for token in tokens):
        return None
    if not 6 <= sum(map(len, tokens)) <= 22:
        return None
    model = load_model()
    if model is None:
        return None
    labels, bias, weights, spellings = model
    scores = bias.copy()
    for name, value in query_features(tokens, spellings).items():
        if row := weights.get(name):
            for index, weight in enumerate(row):
                scores[index] += value * weight
    order = sorted(range(len(labels)), key=scores.__getitem__, reverse=True)
    best, runner_up = order[:2]
    total = sum(math.exp(score - scores[best]) for score in scores)
    return labels[best], 1 / total, math.exp(scores[runner_up] - scores[best]) / total


def recover_brand(query):
    prediction = brand_prediction(query)
    if prediction is None:
        return None
    label, score, second = prediction
    if label and score >= .62 and score - second >= .28:
        return label
    return None
