import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Uuid, false, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TicketAnswer(Base):
    """A bot followup posted to a GLPI ticket: an answer and the memory entries it cited,
    or a handover to a technician.

    The only link from a later satisfaction survey of the ticket back to those entries.
    """

    __tablename__ = "ticket_answer"

    id: Mapped[int] = mapped_column(primary_key=True)
    glpi_ticket_id: Mapped[int] = mapped_column(Integer, index=True)
    glpi_followup_id: Mapped[int] = mapped_column(Integer)
    # No foreign key on an array element: entries deleted since are skipped by the survey sync.
    knowledge_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    # A handed-over ticket belongs to the support: the bot never answers it again.
    escalated: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
