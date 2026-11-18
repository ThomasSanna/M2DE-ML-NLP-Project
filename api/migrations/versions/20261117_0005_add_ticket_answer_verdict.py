"""add ticket_answer verdict

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticket_answer", sa.Column("verdict", sa.String(length=32), nullable=True))
    # Autogenerate misses the CHECK of a non-native enum.
    op.create_check_constraint(
        op.f("ck_ticket_answer_requesterverdict"),
        "ticket_answer",
        "verdict IN ('resolved', 'technician')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_ticket_answer_requesterverdict"), "ticket_answer", type_="check")
    op.drop_column("ticket_answer", "verdict")
