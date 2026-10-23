import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import GlpiEvent, GlpiEventStatus
from app.services import answer
from app.services.embeddings import Embedder, EmbeddingError
from app.services.glpi import Author, Glpi, GlpiError, GlpiTicket
from app.services.llm import ChatModel, LLMError
from app.services.reranker import Reranker, RerankerError
from app.services.websearch import WebSearch

logger = logging.getLogger(__name__)

POLL_SECONDS = 5.0
MAX_ATTEMPTS = 3
# The model servers fail for a minute or two at a time: an immediate retry fails too.
RETRY_DELAY = timedelta(minutes=2)


@dataclass(frozen=True)
class GlpiEventWorker:
    """Answers in a followup each new GLPI ticket or requester followup stored by the webhook."""

    sessionmaker: async_sessionmaker[AsyncSession]
    embedder: Embedder
    reranker: Reranker
    model: ChatModel
    web_search: WebSearch | None
    glpi: Glpi
    settings: Settings

    async def run(self) -> None:
        """Process events until cancelled at shutdown."""
        while True:
            try:
                while await self.process_next():
                    pass
            except Exception:
                # A database outage or a bug must not stop the worker until the next restart.
                # The event being processed stays `processing`: never answered twice.
                logger.exception("GLPI event worker failed")
            await asyncio.sleep(POLL_SECONDS)

    async def process_next(self) -> bool:
        """Process the oldest pending event; False when there is none."""
        async with self.sessionmaker() as session:
            event = await self._claim(session)
            if event is None:
                return False
            error = await self._answer(session, event)
            event.status = GlpiEventStatus.DONE if error is None else GlpiEventStatus.FAILED
            event.last_error = error
            event.processed_at = func.now()
            await session.commit()
            return True

    async def _claim(self, session: AsyncSession) -> GlpiEvent | None:
        retry = and_(
            GlpiEvent.status == GlpiEventStatus.FAILED,
            GlpiEvent.attempts < MAX_ATTEMPTS,
            GlpiEvent.processed_at < func.now() - RETRY_DELAY,
        )
        event = await session.scalar(
            select(GlpiEvent)
            .where(or_(GlpiEvent.status == GlpiEventStatus.RECEIVED, retry))
            .order_by(GlpiEvent.id)
            .limit(1)
            # Several API processes may poll: each event goes to one of them.
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return None
        event.status = GlpiEventStatus.PROCESSING
        event.attempts += 1
        await session.commit()
        return event

    async def _answer(self, session: AsyncSession, event: GlpiEvent) -> str | None:
        """Post the answer to the event's ticket; the reason it was not posted, or None."""
        if event.event != "new":
            return None
        target = _target(event.payload)
        if target is None:
            return "No ticket id in the payload"
        ticket_id, trigger_id = target
        try:
            ticket = await self.glpi.get_ticket(ticket_id)
            if ticket is None:
                return "GLPI ticket not found"
            result = (
                await answer.answer_ticket(
                    session,
                    self.embedder,
                    self.reranker,
                    self.model,
                    self.web_search,
                    self.settings,
                    ticket,
                )
                if _awaits_bot(ticket, trigger_id)
                else None
            )
            if result is None:
                logger.info("GLPI event %d: nothing to answer on ticket %d", event.id, ticket_id)
                return None
            followup_id = await self.glpi.add_followup(
                ticket_id,
                answer.followup_text(result),
                private=self.settings.glpi_followup_private,
            )
        except (EmbeddingError, RerankerError, LLMError, GlpiError) as exc:
            logger.error("GLPI event %d (ticket %d) failed: %s", event.id, ticket_id, exc)
            return str(exc)
        logger.info("GLPI event %d: followup %d on ticket %d", event.id, followup_id, ticket_id)
        return None


def _target(payload: dict[str, Any]) -> tuple[int, int | None] | None:
    """The ticket id, with the followup id for a followup webhook; None without a ticket."""
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    if "itemtype" not in item:
        ticket_id = item.get("id")
        return (ticket_id, None) if isinstance(ticket_id, int) else None
    # A followup webhook: the item is the followup, attached to the `items_id` ticket.
    ticket_id, followup_id = item.get("items_id"), item.get("id")
    if item["itemtype"] == "Ticket" and isinstance(ticket_id, int) and isinstance(followup_id, int):
        return ticket_id, followup_id
    return None


def _awaits_bot(ticket: GlpiTicket, trigger_id: int | None) -> bool:
    """Whether the event's message (the ticket itself when None) is the bot's to answer.

    Not once anyone but the requesters and the bot wrote a public followup: a technician
    took over. Not when a newer followup exists either: its own event answers the thread.
    """
    if any(followup.author is Author.OTHER for followup in ticket.followups):
        return False
    last_id = ticket.followups[-1].id if ticket.followups else None
    return last_id == trigger_id
