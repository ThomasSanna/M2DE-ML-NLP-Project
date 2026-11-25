import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import FeedbackSource, Knowledge, RequesterVerdict, TicketAnswer
from app.schemas.answer import (
    AnswerOutcome,
    AnswerResult,
    EscalationResult,
    KnowledgeCitation,
    WebCitation,
)
from app.services import feedback, growth
from app.services.embeddings import Embedder, EmbeddingError
from app.services.feedback import ReviewPolicy, TicketRating
from app.services.glpi import Glpi, GlpiError

logger = logging.getLogger(__name__)


def _web_sources(result: AnswerResult) -> list[dict[str, str]]:
    """Web pages the answer cited, once each, in citation order."""
    pages: dict[str, str] = {}
    for source in result.sources:
        if isinstance(source, WebCitation):
            pages.setdefault(source.url, source.title)
    return [{"title": title, "url": url} for url, title in pages.items()]


async def record_answer(
    session: AsyncSession,
    ticket_id: int,
    followup_id: int,
    result: AnswerOutcome,
    *,
    question: str | None = None,
    answer: str | None = None,
) -> None:
    """Remember a followup posted to the ticket: what it cited, or a handover, and the
    question and text a validated answer would enter the memory with."""
    if isinstance(result, EscalationResult):
        # A handover cites nothing: the survey has no entry to credit, the panel no page.
        cited: list[uuid.UUID] = []
        pages: list[dict[str, str]] = []
        escalated = True
    else:
        cited = [s.knowledge_id for s in result.sources if isinstance(s, KnowledgeCitation)]
        pages = _web_sources(result)
        escalated = False
    session.add(
        TicketAnswer(
            glpi_ticket_id=ticket_id,
            glpi_followup_id=followup_id,
            knowledge_ids=cited,
            escalated=escalated,
            web_sources=pages,
            question=question,
            answer=answer,
        )
    )
    if cited:
        # Counted when the answer reaches the requester, not on `POST /answer` (read-only).
        await session.execute(
            update(Knowledge)
            .where(Knowledge.id.in_(cited))
            .values(reuse_count=Knowledge.reuse_count + 1)
        )
    await session.commit()


async def is_escalated(session: AsyncSession, ticket_id: int) -> bool:
    """Whether the bot, or the requester, handed the ticket to a technician: the bot then
    never answers it again."""
    handover = exists().where(
        TicketAnswer.glpi_ticket_id == ticket_id,
        or_(TicketAnswer.escalated, TicketAnswer.verdict == RequesterVerdict.TECHNICIAN),
    )
    return bool(await session.scalar(select(handover)))


async def list_answers(session: AsyncSession, ticket_id: int) -> list[TicketAnswer]:
    """The followups the bot posted on the ticket, oldest first."""
    rows = await session.scalars(
        select(TicketAnswer)
        .where(TicketAnswer.glpi_ticket_id == ticket_id)
        .order_by(TicketAnswer.created_at, TicketAnswer.id)
    )
    return list(rows)


async def sync_surveys(
    session: AsyncSession,
    glpi: Glpi,
    review: ReviewPolicy | None = None,
    growth_min_score: float | None = None,
) -> int:
    """Turn the answered GLPI surveys into feedback on the entries the bot cited.

    Every entry cited by any bot answer on the ticket gets the survey's score: the survey
    rates the whole ticket, not one followup. Returns the feedback rows added or changed.
    """
    surveys = {survey.ticket_id: survey for survey in await glpi.list_answered_surveys()}
    if not surveys:
        return 0
    if growth_min_score is not None:
        for ticket_id, rated in surveys.items():
            if rated.score >= growth_min_score:
                await growth.validate_latest(session, ticket_id)
    rows = await session.execute(
        select(TicketAnswer.glpi_ticket_id, TicketAnswer.knowledge_ids).where(
            TicketAnswer.glpi_ticket_id.in_(surveys)
        )
    )
    cited: dict[int, set[uuid.UUID]] = defaultdict(set)
    for ticket_id, knowledge_ids in rows:
        cited[ticket_id].update(knowledge_ids)
    # The poll returns every answered survey again: only a new or edited survey writes a row.
    changed = await feedback.credit(
        session,
        FeedbackSource.SURVEY,
        [
            TicketRating(
                ticket_id,
                frozenset(knowledge_ids),
                surveys[ticket_id].score,
                surveys[ticket_id].comment,
            )
            for ticket_id, knowledge_ids in cited.items()
        ],
    )
    if review is not None:
        await feedback.flag_for_review(session, changed, review)
    await session.commit()
    return len(changed)


@dataclass(frozen=True)
class SurveySync:
    """Polls GLPI for answered satisfaction surveys: GLPI sends no webhook for them."""

    sessionmaker: async_sessionmaker[AsyncSession]
    glpi: Glpi
    poll_seconds: float
    review: ReviewPolicy | None = None
    # Set to grow the memory: validated answers are indexed after each sync.
    embedder: Embedder | None = None
    growth_min_score: float = 0.8

    async def run(self) -> None:
        """Sync until cancelled at shutdown."""
        while True:
            try:
                async with self.sessionmaker() as session:
                    changed = await sync_surveys(
                        session,
                        self.glpi,
                        self.review,
                        self.growth_min_score if self.embedder is not None else None,
                    )
                if changed:
                    logger.info("Survey sync: %d feedback rows added or updated", changed)
                if self.embedder is not None:
                    # Also retries the verdicts indexed while the embeddings server was down.
                    async with self.sessionmaker() as session:
                        await growth.index_validated(session, self.embedder)
            except (GlpiError, EmbeddingError) as exc:
                logger.warning("Survey sync: %s", exc)
            except Exception:
                # A database outage or a bug must not stop the sync until the next restart.
                logger.exception("Survey sync failed")
            await asyncio.sleep(self.poll_seconds)
