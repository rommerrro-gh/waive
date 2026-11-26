from alembic import op

revision = "0005_purchase_profile"
down_revision = "0004_search_analytics"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE search_event ADD COLUMN with_profile boolean NOT NULL DEFAULT false")


def downgrade():
    op.drop_column("search_event", "with_profile")
