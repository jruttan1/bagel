"""Store internal evidence with each morning brief."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("morning_briefs")}
    if "evidence_data" not in columns:
        op.add_column(
            "morning_briefs",
            sa.Column(
                "evidence_data",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade() -> None:
    op.drop_column("morning_briefs", "evidence_data")
