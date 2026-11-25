"""A technician's review of a memory entry the feedback sent to `to_review`."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models import Knowledge, KnowledgeSource, KnowledgeStatus
from app.schemas.knowledge import ReviewAction
from app.services import memory
from app.services.embeddings import Embedder


class NotUnderReview(Exception):
    """The entry is not `to_review`: someone already decided, or it never was."""


async def list_under_review(
    session: AsyncSession, weighting: memory.FeedbackWeighting
) -> list[tuple[Knowledge, memory.FeedbackStats | None]]:
    """Entries waiting for a review, the longest waiting first, with their feedback."""
    entries = list(
        await session.scalars(
            select(Knowledge)
            .options(defer(Knowledge.embedding, raiseload=True))
            .where(Knowledge.status == KnowledgeStatus.TO_REVIEW)
            .order_by(Knowledge.updated_at, Knowledge.id)
        )
    )
    stats = (
        await memory.feedback_stats(session, [entry.id for entry in entries], weighting)
        if entries
        else {}
    )
    return [(entry, stats.get(entry.id)) for entry in entries]


async def review(
    session: AsyncSession,
    embedder: Embedder,
    knowledge_id: uuid.UUID,
    action: ReviewAction,
    answer: str | None = None,
) -> Knowledge | None:
    """Apply the decision; the entry now in the memory (or archived), None if unknown.

    A correction archives the entry and adds a new one with the same question: the old
    ratings judged the wrong answer, and the survey sync would write them again if they
    were deleted. Raises `NotUnderReview` when the entry is not `to_review`.
    """
    entry = await session.get(
        Knowledge,
        knowledge_id,
        options=[defer(Knowledge.embedding, raiseload=True)],
        with_for_update=True,
    )
    if entry is None:
        return None
    if entry.status is not KnowledgeStatus.TO_REVIEW:
        await session.commit()
        raise NotUnderReview
    if action is ReviewAction.REACTIVATE:
        entry.status = KnowledgeStatus.ACTIVE
    elif action is ReviewAction.ARCHIVE:
        entry.status = KnowledgeStatus.ARCHIVED
    else:
        [embedding] = await embedder.embed([entry.question])
        entry.status = KnowledgeStatus.ARCHIVED
        entry = Knowledge(
            question=entry.question,
            answer=answer,
            category=entry.category,
            source=KnowledgeSource.TECHNICIAN,
            glpi_ticket_id=entry.glpi_ticket_id,
            embedding=embedding,
        )
        session.add(entry)
    await session.commit()
    await session.refresh(entry, attribute_names=["created_at", "updated_at", "reuse_count"])
    return entry
