"""add ticket_answer question, answer, validated and indexed_knowledge_id

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticket_answer", sa.Column("question", sa.Text(), nullable=True))
    op.add_column("ticket_answer", sa.Column("answer", sa.Text(), nullable=True))
    op.add_column(
        "ticket_answer",
        sa.Column("validated", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("ticket_answer", sa.Column("indexed_knowledge_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("ticket_answer", "indexed_knowledge_id")
    op.drop_column("ticket_answer", "validated")
    op.drop_column("ticket_answer", "answer")
    op.drop_column("ticket_answer", "question")
