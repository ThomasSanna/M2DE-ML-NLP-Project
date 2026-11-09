"""add ticket_answer and survey feedback

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 12:42:06.925784

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate does not detect CHECK changes: the feedbacksource constraint is rewritten here.
OLD_SOURCES = "source IN ('user', 'technician', 'implicit')"
NEW_SOURCES = "source IN ('user', 'technician', 'implicit', 'survey')"


def upgrade() -> None:
    op.create_table(
        "ticket_answer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("glpi_ticket_id", sa.Integer(), nullable=False),
        sa.Column("glpi_followup_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_answer")),
    )
    op.create_index(
        op.f("ix_ticket_answer_glpi_ticket_id"), "ticket_answer", ["glpi_ticket_id"], unique=False
    )
    op.drop_constraint(op.f("ck_feedback_feedbacksource"), "feedback", type_="check")
    op.create_check_constraint(op.f("ck_feedback_feedbacksource"), "feedback", NEW_SOURCES)
    op.create_index(
        "uq_feedback_survey",
        "feedback",
        ["glpi_ticket_id", "knowledge_id"],
        unique=True,
        postgresql_where=sa.text("source = 'survey'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_feedback_survey", table_name="feedback", postgresql_where=sa.text("source = 'survey'")
    )
    # The old constraint rejects survey rows: they go before it is restored.
    op.execute("DELETE FROM feedback WHERE source = 'survey'")
    op.drop_constraint(op.f("ck_feedback_feedbacksource"), "feedback", type_="check")
    op.create_check_constraint(op.f("ck_feedback_feedbacksource"), "feedback", OLD_SOURCES)
    op.drop_index(op.f("ix_ticket_answer_glpi_ticket_id"), table_name="ticket_answer")
    op.drop_table("ticket_answer")
