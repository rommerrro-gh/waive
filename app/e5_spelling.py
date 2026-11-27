"""Catalog spelling suggestions scored by the same E5 encoder as semantic search."""

import logging
from threading import Lock

from app.embeddings import vectors
from app.spelling import edit_cost, words

logger = logging.getLogger(__name__)
BATCH_SIZE = 32
TIMEOUT = 0.4


def cosine(first, second):
    # The inference service returns unit vectors.
    return sum(a * b for a, b in zip(first, second, strict=True))


class E5Spelling:
    def __init__(self, aliases, revision, embed=vectors):
        self.aliases = tuple((brand, name) for brand, variants in aliases.items()
                             for name in dict.fromkeys((brand, *variants)))
        self.revision = revision
        self.embed = embed
        self._alias_vectors = None
        self._initializing = Lock()
        # The VM serves a single E5 call at a time.
        self._encoding = Lock()

    def _encode(self, names):
        revision, values = self.embed("query", names, TIMEOUT)
        if revision != self.revision:
            raise ValueError("Версия модели опечаток не совпадает с конфигурацией")
        return values

    def prepare(self):
        if self._alias_vectors is not None or not self._initializing.acquire(blocking=False):
            return
        try:
            self._alias_vectors = tuple(vector for start in range(0, len(self.aliases), BATCH_SIZE)
                                         for vector in self._encode([
                                             name for _, name in self.aliases[start:start + BATCH_SIZE]
                                         ]))
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.debug("Не удалось подготовить словарь E5 (%s)", type(error).__name__)
        finally:
            self._initializing.release()

    def rank_candidates(self, head, candidates, *, minimum=.85):
        if not candidates or not self._encoding.acquire(blocking=False):
            return []
        try:
            source, *embeddings = self._encode([head, *candidates])
            scored = [(cosine(source, vector), candidate)
                      for candidate, vector in zip(candidates, embeddings, strict=True)]
            return sorted((item for item in scored if item[0] >= minimum), reverse=True)
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.debug("Не удалось оценить варианты через E5 (%s)", type(error).__name__)
            return []
        finally:
            self._encoding.release()

    def suggest(self, query, labels):
        leading, separator, suffix = query.partition(" ")
        aliases_allowed = 6 <= len(leading) <= 22 and leading.isalpha()
        if not labels and not aliases_allowed:
            return []
        if not self._encoding.acquire(blocking=False):
            return []
        try:
            if aliases_allowed:
                self.prepare()
            use_aliases = aliases_allowed and self._alias_vectors is not None
            same_input = leading == query
            names = [query, *([leading] if use_aliases and not same_input else []), *labels]
            query_vector, *encoded = self._encode(names)
            leading_vector = query_vector if same_input else (encoded.pop(0) if use_aliases else None)
            scored = {label: cosine(query_vector, vector) for label, vector in zip(labels, encoded, strict=True)}
            if use_aliases:
                normalized = " ".join(words(leading))
                for (brand, alias), vector in zip(self.aliases, self._alias_vectors, strict=True):
                    target = " ".join(words(alias))
                    if not target or normalized.isascii() != target.isascii() or abs(len(normalized) - len(target)) > 3:
                        continue
                    distance = edit_cost(normalized, target) / (100 * max(len(normalized), len(target)))
                    similarity = cosine(leading_vector, vector)
                    if distance <= .45 and similarity >= .88:
                        label = brand + separator + suffix
                        scored[label] = max(scored.get(label, 0), similarity - .10 * distance)
            return sorted(scored, key=scored.__getitem__, reverse=True)[:6]
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.debug("Исправление запроса обслужено правилами (%s)", type(error).__name__)
            return []
        finally:
            self._encoding.release()
