"""Разбор смысловой выдачи по отдельным этапам на активном снимке каталога."""

import json
import os
import time

import psycopg
from psycopg.rows import dict_row

from app.embeddings import vector_literal, vectors
from app.search import SEMANTIC_OFFERS, assemble_results, lexical_candidates
from evaluate import CASES

NEAREST_TARGET = """
    WITH ranked AS MATERIALIZED (
        SELECT document_id, group_id, offer_id, embedding <=> %(embedding)s::vector AS distance,
            row_number() OVER (ORDER BY embedding <=> %(embedding)s::vector, document_id) AS rank
        FROM semantic_document
        WHERE snapshot_id = %(snapshot)s
    )
    SELECT rank, distance, document_id, example.name
    FROM ranked r
    JOIN LATERAL (
        SELECT offer.name
        FROM offer
        WHERE snapshot_id = %(snapshot)s AND category_id = ANY(%(categories)s)
            AND (group_id = r.group_id OR id = r.offer_id)
        ORDER BY available DESC, id
        LIMIT 1
    ) example ON true
    ORDER BY rank
    LIMIT 1
"""

STAGE_SAMPLE = 4


def evidence(rows, categories):
    return {
        "count": len(rows),
        "target_rank": next(
            (rank for rank, row in enumerate(rows, 1) if row["category_id"] in categories), None
        ),
        "first": [
            {"id": row["offer_id"], "category": row["category_id"], "name": row["name"]}
            for row in rows[:STAGE_SAMPLE]
        ],
    }


def classify(lexical, semantic, final, nearest):
    if final["target_rank"]:
        return "top4"
    if lexical["target_rank"] or semantic["target_rank"]:
        return "ranking_or_sku_filter"
    if nearest is None:
        return "target_family_not_indexed"
    if nearest["rank"] > 25:
        return "outside_dense_candidate_limit"
    if nearest["distance"] > 0.30:
        return "below_dense_similarity_cutoff"
    return "target_family_found_but_sku_not_selected"


def diagnose(connection, snapshot, query, categories):
    started = time.monotonic()
    lexical_rows = lexical_candidates(connection, snapshot, query)
    lexical_ms = (time.monotonic() - started) * 1000
    revision, embeddings = vectors("query", [query], timeout=10)
    if revision != os.environ["MODEL_REVISION"]:
        raise RuntimeError("Версия модели отличается от версии снимка")
    params = {
        "snapshot": snapshot, "embedding": vector_literal(embeddings[0]),
        "categories": categories,
    }
    dense_rows = connection.execute(SEMANTIC_OFFERS, params).fetchall()
    nearest = connection.execute(NEAREST_TARGET, params).fetchone()
    final_rows, _, _ = assemble_results(connection, snapshot, query, lexical_rows, dense_rows)
    lexical = evidence(lexical_rows, categories)
    semantic = evidence(dense_rows, categories)
    final = evidence(final_rows, categories)
    return {
        "query": query, "expected_categories": categories,
        "diagnosis": classify(lexical, semantic, final, nearest),
        "nearest_target": nearest, "lexical_ms": round(lexical_ms, 1),
        "lexical": lexical, "semantic": semantic, "final": final,
    }


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        snapshot = connection.execute("""
            SELECT a.snapshot_id
            FROM active_catalog a
            JOIN semantic_catalog s ON s.snapshot_id = a.snapshot_id
            WHERE s.model_revision = %s
        """, (os.environ["MODEL_REVISION"],)).fetchone()
        if snapshot is None:
            raise RuntimeError("Нет активного снимка с готовым смысловым индексом")
        for sample, query, field, expected in CASES:
            if sample != "смысл" or field != "category_id":
                continue
            categories = list(expected) if isinstance(expected, tuple) else [expected]
            print(json.dumps(diagnose(connection, snapshot["snapshot_id"], query, categories),
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
