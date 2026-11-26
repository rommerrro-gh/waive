from alembic import op

revision = "0001_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""
        CREATE TABLE catalog_snapshot (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            content_hash text NOT NULL UNIQUE,
            feed_date text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE active_catalog (
            singleton boolean PRIMARY KEY CHECK (singleton),
            snapshot_id bigint NOT NULL REFERENCES catalog_snapshot
        )
    """)
    op.execute("""
        CREATE TABLE category (
            snapshot_id bigint NOT NULL REFERENCES catalog_snapshot ON DELETE CASCADE,
            id text NOT NULL,
            parent_id text,
            name text NOT NULL,
            PRIMARY KEY (snapshot_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE offer (
            snapshot_id bigint NOT NULL REFERENCES catalog_snapshot ON DELETE CASCADE,
            id text NOT NULL,
            group_id text,
            category_id text,
            name text NOT NULL,
            vendor text NOT NULL,
            vendor_code text,
            url text,
            price numeric CHECK (price >= 0),
            currency text,
            available boolean NOT NULL,
            description text NOT NULL,
            attributes jsonb NOT NULL,
            PRIMARY KEY (snapshot_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE offer_search (
            snapshot_id bigint NOT NULL,
            offer_id text NOT NULL,
            name_normalized text NOT NULL,
            document tsvector NOT NULL,
            PRIMARY KEY (snapshot_id, offer_id),
            FOREIGN KEY (snapshot_id, offer_id) REFERENCES offer ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX offer_search_document_idx ON offer_search USING gin (document)")
    op.execute("CREATE INDEX offer_search_name_idx ON offer_search USING gin (name_normalized gin_trgm_ops)")
    op.execute("CREATE INDEX offer_code_idx ON offer (snapshot_id, lower(vendor_code))")
    op.execute("CREATE INDEX offer_group_idx ON offer (snapshot_id, group_id)")


def downgrade():
    for table in ("offer_search", "offer", "category", "active_catalog", "catalog_snapshot"):
        op.drop_table(table)
