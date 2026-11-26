from alembic import op

revision = "0003_family_typos"
down_revision = "0002_semantic"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE INDEX category_name_typo_idx ON category
        USING gist (lower(name) gist_trgm_ops(siglen=32))
    """)


def downgrade():
    op.drop_index("category_name_typo_idx", table_name="category")
