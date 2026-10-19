"""initial schema : mémoire (knowledge, feedback) et boîte de réception GLPI

Revision ID: 0001
Revises:
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "glpi_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'done', 'failed')",
            name=op.f("ck_glpi_event_glpieventstatus"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glpi_event")),
    )
    op.create_index(op.f("ix_glpi_event_status"), "glpi_event", ["status"], unique=False)

    op.create_table(
        "knowledge",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("glpi_ticket_id", sa.Integer(), nullable=True),
        sa.Column("reuse_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.HALFVEC(dim=3584), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('glpi_history', 'llm', 'technician')",
            name=op.f("ck_knowledge_knowledgesource"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'to_review', 'archived')",
            name=op.f("ck_knowledge_knowledgestatus"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge")),
    )
    op.create_index(
        "ix_knowledge_embedding_hnsw",
        "knowledge",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )
    op.create_index(
        op.f("ix_knowledge_glpi_ticket_id"), "knowledge", ["glpi_ticket_id"], unique=False
    )
    op.create_index(op.f("ix_knowledge_status"), "knowledge", ["status"], unique=False)

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("knowledge_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("glpi_ticket_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('user', 'technician', 'implicit')",
            name=op.f("ck_feedback_feedbacksource"),
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name=op.f("ck_feedback_score_range")),
        sa.ForeignKeyConstraint(
            ["knowledge_id"],
            ["knowledge.id"],
            name=op.f("fk_feedback_knowledge_id_knowledge"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
    )
    op.create_index(op.f("ix_feedback_knowledge_id"), "feedback", ["knowledge_id"], unique=False)


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("knowledge")
    op.drop_table("glpi_event")
