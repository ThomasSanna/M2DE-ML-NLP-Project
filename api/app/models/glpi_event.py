from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.knowledge import str_enum


class GlpiEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class GlpiEvent(Base):
    """Inbox of GLPI webhooks.

    The webhook is acknowledged as soon as it is stored; the LLM pipeline then
    consumes the `received` events (and replays the `failed` ones).
    """

    __tablename__ = "glpi_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[GlpiEventStatus] = mapped_column(
        str_enum(GlpiEventStatus), default=GlpiEventStatus.RECEIVED, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
