"""add verdict feedback

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate does not detect CHECK changes: the feedbacksource constraint is rewritten here.
OLD_SOURCES = "source IN ('user', 'technician', 'implicit', 'survey')"
NEW_SOURCES = "source IN ('user', 'technician', 'implicit', 'survey', 'verdict')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_feedback_feedbacksource"), "feedback", type_="check")
    op.create_check_constraint(op.f("ck_feedback_feedbacksource"), "feedback", NEW_SOURCES)
    op.create_index(
        "uq_feedback_verdict",
        "feedback",
        ["glpi_ticket_id", "knowledge_id"],
        unique=True,
        postgresql_where=sa.text("source = 'verdict'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_feedback_verdict", table_name="feedback", postgresql_where=sa.text("source = 'verdict'")
    )
    # The old constraint rejects verdict rows: they go before it is restored.
    op.execute("DELETE FROM feedback WHERE source = 'verdict'")
    op.drop_constraint(op.f("ck_feedback_feedbacksource"), "feedback", type_="check")
    op.create_check_constraint(op.f("ck_feedback_feedbacksource"), "feedback", OLD_SOURCES)
