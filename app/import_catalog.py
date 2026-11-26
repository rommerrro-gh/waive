import argparse
import hashlib
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.feed import open_feed, read_feed

INDEX_OFFERS = """
    INSERT INTO offer_search
    SELECT s.snapshot_id, s.id, lower(s.name),
        setweight(to_tsvector('simple', s.name || ' ' || s.vendor || ' ' ||
            coalesce(g.name, '')), 'A') ||
        setweight(to_tsvector('russian', s.name || ' ' || coalesce(c.name, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(s.vendor_code, '') || ' ' ||
            coalesce(s.attributes->>'Версия', '')), 'B') ||
        setweight(to_tsvector('russian', left(s.description, 3500)), 'D')
    FROM offer s
    LEFT JOIN category g ON g.snapshot_id = s.snapshot_id AND g.id = s.group_id
    LEFT JOIN category c ON c.snapshot_id = s.snapshot_id AND c.id = s.category_id
    WHERE s.snapshot_id = %s
"""


def import_catalog(connection, path):
    with open_feed(path) as source:
        fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(71269940)")
        snapshot = cursor.execute(
            "SELECT id FROM catalog_snapshot WHERE content_hash = %s", (fingerprint,)
        ).fetchone()
        if snapshot:
            return snapshot[0], False
        with open_feed(path) as source:
            events = read_feed(source)
            first = next(events, None)
            snapshot_id = cursor.execute(
                "INSERT INTO catalog_snapshot (content_hash, feed_date) VALUES (%s, %s) RETURNING id",
                (fingerprint, first[1] if first else None),
            ).fetchone()[0]
            categories = []
            count = 0
            with cursor.copy("COPY offer FROM STDIN") as copy:
                for kind, value in events:
                    if kind == "category":
                        categories.append((snapshot_id, *value))
                    elif kind == "offer":
                        copy.write_row((snapshot_id, *value[:-1], Jsonb(value[-1])))
                        count += 1
            if not count or not categories:
                raise ValueError("Фид не содержит предложений или категорий")
            with cursor.copy("COPY category FROM STDIN") as copy:
                for category in categories:
                    copy.write_row(category)
        cursor.execute(INDEX_OFFERS, (snapshot_id,))
        if cursor.rowcount != count:
            raise ValueError("Число поисковых документов отличается от числа предложений")
        cursor.execute("""
            INSERT INTO active_catalog VALUES (true, %s)
            ON CONFLICT (singleton) DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id
        """, (snapshot_id,))
        return snapshot_id, True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Загрузить XML-каталог Softline")
    parser.add_argument("feed", type=Path)
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        snapshot_id, created = import_catalog(connection, parser.parse_args().feed)
    print(f"Снимок {snapshot_id}: {'опубликован' if created else 'уже загружен'}")
