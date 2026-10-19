import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.knowledge import Knowledge, str_enum


class FeedbackSource(StrEnum):
    USER = "user"  # réponse de l'utilisateur, notée par le modèle de notation
    TECHNICIAN = "technician"  # révision différée par un technicien
    IMPLICIT = "implicit"  # signal implicite (ticket rouvert, pas de relance…)


class Feedback(Base):
    """Un retour sur une connaissance. Historique brut : la pondération se calcule dessus."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[FeedbackSource] = mapped_column(str_enum(FeedbackSource))
    # Note normalisée dans [0, 1].
    score: Mapped[float] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)
    glpi_ticket_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    knowledge: Mapped[Knowledge] = relationship(back_populates="feedbacks")

    __table_args__ = (CheckConstraint("score >= 0 AND score <= 1", name="score_range"),)
