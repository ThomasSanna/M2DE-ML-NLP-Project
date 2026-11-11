"""add ticket_answer escalated

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10 18:53:28.576058

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticket_answer",
        sa.Column("escalated", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ticket_answer", "escalated")
