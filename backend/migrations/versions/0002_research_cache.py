"""Add persisted market research cache."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_cache",
        sa.Column("cache_key", sa.String(length=160), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_cache_expires_at", "research_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_research_cache_expires_at", table_name="research_cache")
    op.drop_table("research_cache")
