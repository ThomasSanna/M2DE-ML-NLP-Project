"""Feedback on memory entries from what happened on a GLPI ticket the bot answered."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Feedback, FeedbackSource, Knowledge, KnowledgeStatus
from app.services import memory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketRating:
    """One rating of a ticket, credited to each memory entry the bot cited on it."""

    ticket_id: int
    knowledge_ids: frozenset[uuid.UUID]
    score: float  # in [0, 1]
    comment: str | None = None


async def credit(
    session: AsyncSession, source: FeedbackSource, ratings: list[TicketRating]
) -> set[uuid.UUID]:
    """Write one feedback row per ticket and cited entry; the entries whose row changed.

    `source` must have a partial unique index on (glpi_ticket_id, knowledge_id), as
    `survey` and `verdict` do: a new rating of the same ticket updates the row, an
    identical one writes nothing. Entries deleted since the answer are skipped. Does not
    commit.
    """
    wanted = set().union(*(rating.knowledge_ids for rating in ratings))
    if not wanted:
        return set()
    existing = set(await session.scalars(select(Knowledge.id).where(Knowledge.id.in_(wanted))))
    values = [
        {
            "knowledge_id": knowledge_id,
            "source": source,
            "score": rating.score,
            "comment": rating.comment,
            "glpi_ticket_id": rating.ticket_id,
        }
        for rating in ratings
        for knowledge_id in sorted(rating.knowledge_ids & existing)
    ]
    if not values:
        return set()
    statement = insert(Feedback).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[Feedback.glpi_ticket_id, Feedback.knowledge_id],
        index_where=Feedback.source == source,
        set_={"score": statement.excluded.score, "comment": statement.excluded.comment},
        where=(Feedback.score != statement.excluded.score)
        | Feedback.comment.is_distinct_from(statement.excluded.comment),
    ).returning(Feedback.knowledge_id)
    return set((await session.scalars(statement)).all())


@dataclass(frozen=True)
class ReviewPolicy:
    """When feedback takes an entry out of the memory until a technician reviews it."""

    # Smoothed feedback score under which an entry goes to review; 0 never sends one.
    threshold: float
    # Ratings needed first: a few bad ones on a good entry must not hide it.
    min_ratings: int
    # Same smoothing and fading as the ranking, so both read the same score.
    prior: float
    half_life_days: float | None

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReviewPolicy":
        return cls(
            settings.feedback_review_threshold,
            settings.feedback_review_min_ratings,
            settings.feedback_prior,
            settings.feedback_half_life_days,
        )


async def flag_for_review(
    session: AsyncSession, knowledge_ids: set[uuid.UUID], policy: ReviewPolicy
) -> list[uuid.UUID]:
    """Send the active entries the feedback now rates poorly to review; those it sent.

    A `to_review` entry is no longer retrieved (the search keeps `active` ones): a poorly
    rated answer stops reaching requesters until a technician fixes it. Does not commit.
    """
    if not knowledge_ids or policy.threshold <= 0:
        return []
    weighting = memory.FeedbackWeighting(prior=policy.prior, half_life_days=policy.half_life_days)
    stats = await memory.feedback_stats(session, sorted(knowledge_ids), weighting)
    poor = [
        knowledge_id
        for knowledge_id, entry in stats.items()
        if entry.count >= policy.min_ratings and entry.score < policy.threshold
    ]
    if not poor:
        return []
    flagged = list(
        await session.scalars(
            update(Knowledge)
            .where(Knowledge.id.in_(poor), Knowledge.status == KnowledgeStatus.ACTIVE)
            .values(status=KnowledgeStatus.TO_REVIEW)
            .returning(Knowledge.id)
        )
    )
    if flagged:
        logger.info("Feedback sent %d memory entries to review: %s", len(flagged), flagged)
    return flagged
