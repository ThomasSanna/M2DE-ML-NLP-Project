import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, Text, Uuid, false, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.knowledge import str_enum


class RequesterVerdict(StrEnum):
    """What the requester said of the bot's answer, from the GLPI plugin's buttons."""

    RESOLVED = "resolved"  # the answer solved the problem: a technician can close the ticket
    TECHNICIAN = "technician"  # the requester asked for a technician: a handover


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
    # {title, url} of each web page the answer cited, once per URL.
    web_sources: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    # Set once, on the latest answer only: a newer answer gets its own buttons.
    verdict: Mapped[RequesterVerdict | None] = mapped_column(str_enum(RequesterVerdict))
    # The retrieval query and the text the requester read: a validated answer that cited no
    # memory entry becomes one (`services/growth.py`). Null on rows from before 0007.
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    # Resolved verdict, or a good survey of the ticket: the answer may enter the memory.
    validated: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # The entry it became; no foreign key, like `knowledge_ids`: set once, never redone.
    indexed_knowledge_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
