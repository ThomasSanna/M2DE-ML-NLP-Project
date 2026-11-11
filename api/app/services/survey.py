import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Feedback, FeedbackSource, Knowledge, TicketAnswer
from app.schemas.answer import AnswerOutcome, EscalationResult, KnowledgeCitation
from app.services.glpi import Glpi, GlpiError

logger = logging.getLogger(__name__)


async def record_answer(
    session: AsyncSession, ticket_id: int, followup_id: int, result: AnswerOutcome
) -> None:
    """Remember a followup posted to the ticket: the memory entries it cited, or a handover."""
    if isinstance(result, EscalationResult):
        # A handover cites nothing: the survey has no entry to credit.
        cited: list[uuid.UUID] = []
        escalated = True
    else:
        cited = [s.knowledge_id for s in result.sources if isinstance(s, KnowledgeCitation)]
        escalated = False
    session.add(
        TicketAnswer(
            glpi_ticket_id=ticket_id,
            glpi_followup_id=followup_id,
            knowledge_ids=cited,
            escalated=escalated,
        )
    )
    await session.commit()


async def is_escalated(session: AsyncSession, ticket_id: int) -> bool:
    """Whether the bot handed the ticket to a technician: it then never answers it again."""
    handover = exists().where(TicketAnswer.glpi_ticket_id == ticket_id, TicketAnswer.escalated)
    return bool(await session.scalar(select(handover)))


async def sync_surveys(session: AsyncSession, glpi: Glpi) -> int:
    """Turn the answered GLPI surveys into feedback on the entries the bot cited.

    Every entry cited by any bot answer on the ticket gets the survey's score: the survey
    rates the whole ticket, not one followup. Returns the feedback rows added or changed.
    """
    surveys = {survey.ticket_id: survey for survey in await glpi.list_answered_surveys()}
    if not surveys:
        return 0
    rows = await session.execute(
        select(TicketAnswer.glpi_ticket_id, TicketAnswer.knowledge_ids).where(
            TicketAnswer.glpi_ticket_id.in_(surveys)
        )
    )
    cited: dict[int, set[uuid.UUID]] = defaultdict(set)
    for ticket_id, knowledge_ids in rows:
        cited[ticket_id].update(knowledge_ids)
    wanted = set().union(*cited.values())
    existing = set(
        await session.scalars(select(Knowledge.id).where(Knowledge.id.in_(wanted)))
        if wanted
        else []
    )
    values = [
        {
            "knowledge_id": knowledge_id,
            "source": FeedbackSource.SURVEY,
            "score": surveys[ticket_id].score,
            "comment": surveys[ticket_id].comment,
            "glpi_ticket_id": ticket_id,
        }
        for ticket_id, knowledge_ids in cited.items()
        for knowledge_id in sorted(knowledge_ids & existing)
    ]
    if not values:
        await session.commit()
        return 0
    statement = insert(Feedback).values(values)
    # The poll returns every answered survey again: only a new or edited survey writes a row.
    statement = statement.on_conflict_do_update(
        index_elements=[Feedback.glpi_ticket_id, Feedback.knowledge_id],
        index_where=Feedback.source == FeedbackSource.SURVEY,
        set_={"score": statement.excluded.score, "comment": statement.excluded.comment},
        where=(Feedback.score != statement.excluded.score)
        | Feedback.comment.is_distinct_from(statement.excluded.comment),
    ).returning(Feedback.id)
    changed = len((await session.execute(statement)).all())
    await session.commit()
    return changed


@dataclass(frozen=True)
class SurveySync:
    """Polls GLPI for answered satisfaction surveys: GLPI sends no webhook for them."""

    sessionmaker: async_sessionmaker[AsyncSession]
    glpi: Glpi
    poll_seconds: float

    async def run(self) -> None:
        """Sync until cancelled at shutdown."""
        while True:
            try:
                async with self.sessionmaker() as session:
                    changed = await sync_surveys(session, self.glpi)
                if changed:
                    logger.info("Survey sync: %d feedback rows added or updated", changed)
            except GlpiError as exc:
                logger.warning("Survey sync: %s", exc)
            except Exception:
                # A database outage or a bug must not stop the sync until the next restart.
                logger.exception("Survey sync failed")
            await asyncio.sleep(self.poll_seconds)
