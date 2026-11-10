import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.knowledge import Knowledge, str_enum


class FeedbackSource(StrEnum):
    USER = "user"  # user reply, scored by the scoring model
    TECHNICIAN = "technician"  # deferred review by a technician
    IMPLICIT = "implicit"  # implicit signal (ticket reopened, no follow-up...)
    SURVEY = "survey"  # GLPI satisfaction survey of a ticket the bot answered


class Feedback(Base):
    """A feedback on a knowledge entry. Raw history: the weighting is computed on it."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[FeedbackSource] = mapped_column(str_enum(FeedbackSource))
    # Score normalized to [0, 1].
    score: Mapped[float] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)
    glpi_ticket_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    knowledge: Mapped[Knowledge] = relationship(back_populates="feedbacks")

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        # A ticket has one survey: it rates each entry the bot cited on it once. A requester
        # who edits the survey updates that row instead of adding one.
        Index(
            "uq_feedback_survey",
            "glpi_ticket_id",
            "knowledge_id",
            unique=True,
            postgresql_where=text("source = 'survey'"),
        ),
    )
