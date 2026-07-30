"""Add durable agent memory."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("agent_memories"):
        op.create_table(
            "agent_memories",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("memory_key", sa.String(length=120), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("ticker", sa.String(length=32), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "memory_key"),
        )
    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("agent_memories")
    }
    for name, column in (
        ("ix_agent_memories_user_id", "user_id"),
        ("ix_agent_memories_category", "category"),
        ("ix_agent_memories_ticker", "ticker"),
    ):
        if name not in indexes:
            op.create_index(name, "agent_memories", [column])


def downgrade() -> None:
    op.drop_table("agent_memories")
