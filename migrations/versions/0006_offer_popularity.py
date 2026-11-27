from alembic import op

revision = "0006_offer_popularity"
down_revision = "0005_purchase_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE offer_visit (
            visit_id uuid NOT NULL,
            offer_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (visit_id, offer_id)
        )
    """)
    op.execute("CREATE INDEX offer_visit_period_idx ON offer_visit (created_at, offer_id)")


def downgrade():
    op.drop_table("offer_visit")
