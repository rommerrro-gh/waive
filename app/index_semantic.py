import argparse
import os

import psycopg

from app.embeddings import vector_literal, vectors

DOCUMENTS = """
    SELECT DISTINCT ON (doc.identity) doc.identity, doc.group_id, doc.offer_id,
        concat_ws('. ', coalesce(family.name, o.name), o.vendor,
            category.name, parent.name,
            CASE WHEN family.id IS NULL THEN left(o.description, 300) END)
    FROM offer o
    LEFT JOIN category family ON (family.snapshot_id, family.id) = (o.snapshot_id, o.group_id)
    LEFT JOIN category category ON (category.snapshot_id, category.id) = (o.snapshot_id, o.category_id)
    LEFT JOIN category parent ON (parent.snapshot_id, parent.id) = (o.snapshot_id, category.parent_id)
    CROSS JOIN LATERAL (
        SELECT coalesce('g:' || family.id, 'o:' || o.id) AS identity,
            family.id AS group_id, CASE WHEN family.id IS NULL THEN o.id END AS offer_id
    ) doc
    WHERE o.snapshot_id = %s
    ORDER BY doc.identity, o.available DESC, o.id
"""


def build_index(connection):
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(71269941)")
        row = cursor.execute("SELECT snapshot_id FROM active_catalog").fetchone()
        if row is None:
            raise RuntimeError("Сначала загрузите каталог")
        snapshot = row[0]
        cursor.execute("DELETE FROM semantic_document WHERE snapshot_id = %s", (snapshot,))
        count = 0
        expected_revision = os.environ["MODEL_REVISION"]
        with connection.cursor(name="semantic_source") as source:
            source.execute(DOCUMENTS, (snapshot,))
            while rows := source.fetchmany(32):
                revision, embeddings = vectors("passage", [row[3] for row in rows], timeout=120)
                if revision != expected_revision:
                    raise RuntimeError("Версия модели не совпадает с конфигурацией индексации")
                cursor.executemany(
                    "INSERT INTO semantic_document VALUES (%s, %s, %s, %s, %s::vector)",
                    [(snapshot, *row[:3], vector_literal(vector))
                     for row, vector in zip(rows, embeddings, strict=True)],
                )
                count += len(rows)
        cursor.execute("""
            INSERT INTO semantic_catalog VALUES (%s, %s, %s)
            ON CONFLICT (snapshot_id) DO UPDATE
            SET model_revision = EXCLUDED.model_revision, document_count = EXCLUDED.document_count
        """, (snapshot, expected_revision, count))
    return snapshot, count


if __name__ == "__main__":
    argparse.ArgumentParser(description="Построить смысловой индекс активного каталога").parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        snapshot, count = build_index(connection)
    print(f"Снимок {snapshot}: проиндексировано {count} семейств и одиночных товаров")
