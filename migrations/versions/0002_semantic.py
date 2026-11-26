from alembic import op

revision = "0002_semantic"
down_revision = "0001_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX offer_category_idx ON offer (snapshot_id, category_id, available)")
    op.execute("""
        CREATE TABLE semantic_catalog (
            snapshot_id bigint PRIMARY KEY REFERENCES catalog_snapshot ON DELETE CASCADE,
            model_revision text NOT NULL,
            document_count integer NOT NULL CHECK (document_count > 0)
        )
    """)
    op.execute("""
        CREATE TABLE semantic_document (
            snapshot_id bigint NOT NULL REFERENCES catalog_snapshot ON DELETE CASCADE,
            document_id text NOT NULL,
            group_id text,
            offer_id text,
            embedding vector NOT NULL,
            CHECK ((group_id IS NOT NULL) <> (offer_id IS NOT NULL)),
            PRIMARY KEY (snapshot_id, document_id)
        )
    """)


def downgrade():
    op.drop_index("offer_category_idx", table_name="offer")
    op.drop_table("semantic_document")
    op.drop_table("semantic_catalog")
