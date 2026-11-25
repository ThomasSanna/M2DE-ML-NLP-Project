"""Feedback on memory entries from what happened on a GLPI ticket the bot answered."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, FeedbackSource, Knowledge


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
