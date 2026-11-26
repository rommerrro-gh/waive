from alembic import op

revision = "0004_search_analytics"
down_revision = "0003_family_typos"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE search_event (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            demo boolean NOT NULL,
            query_group text NOT NULL,
            filters jsonb NOT NULL,
            reason text NOT NULL,
            direct_count smallint NOT NULL,
            direct_ids text[] NOT NULL,
            recommendation_ids text[] NOT NULL,
            direct_clicked boolean NOT NULL DEFAULT false,
            recommendation_clicked boolean NOT NULL DEFAULT false
        )
    """)
    op.execute("CREATE INDEX search_event_period_idx ON search_event (demo, created_at DESC)")


def downgrade():
    op.drop_table("search_event")
