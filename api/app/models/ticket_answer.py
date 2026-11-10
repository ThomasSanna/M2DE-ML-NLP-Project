import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Uuid, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TicketAnswer(Base):
    """A bot answer posted to a GLPI ticket, with the memory entries it cited.

    The only link from a later satisfaction survey of the ticket back to those entries.
    """

    __tablename__ = "ticket_answer"

    id: Mapped[int] = mapped_column(primary_key=True)
    glpi_ticket_id: Mapped[int] = mapped_column(Integer, index=True)
    glpi_followup_id: Mapped[int] = mapped_column(Integer)
    # No foreign key on an array element: entries deleted since are skipped by the survey sync.
    knowledge_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    # {title, url} of each web page the answer cited, once per URL.
    web_sources: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
