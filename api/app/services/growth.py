"""The memory grows from the answers the requesters validated.

A validated answer that cited no memory entry (it came from the web or from the model)
becomes a new entry: an answer that cited one would only repeat it.
"""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Knowledge, KnowledgeSource, TicketAnswer
from app.services import memory
from app.services.embeddings import Embedder

logger = logging.getLogger(__name__)

# Answers indexed per call: one embeddings request each time.
BATCH = 20


async def validate_latest(session: AsyncSession, ticket_id: int) -> None:
    """Mark the latest answer of the ticket as validated (a good survey); does not commit."""
    latest = await session.scalar(
        select(TicketAnswer)
        .where(TicketAnswer.glpi_ticket_id == ticket_id, TicketAnswer.escalated.is_(False))
        .order_by(TicketAnswer.created_at.desc(), TicketAnswer.id.desc())
        .limit(1)
    )
    if latest is not None:
        latest.validated = True


async def index_validated(
    session: AsyncSession, embedder: Embedder, dedup_similarity: float = 1.0
) -> list[uuid.UUID]:
    """Turn validated answers not indexed yet into memory entries; the entries created.

    An answer whose question is at least `dedup_similarity` (cosine) from an active entry's
    is linked to that entry instead: the same problem solved twice must not give two
    entries. Entries under review or archived do not count, so a validated answer can take
    their place. Raises `EmbeddingError` with nothing written: the answers stay pending for
    the next call. Each answer is indexed once, even if its entry is deleted later.
    """
    pending = list(
        await session.scalars(
            select(TicketAnswer)
            .where(
                TicketAnswer.validated,
                TicketAnswer.indexed_knowledge_id.is_(None),
                TicketAnswer.escalated.is_(False),
                func.cardinality(TicketAnswer.knowledge_ids) == 0,
                func.length(func.trim(TicketAnswer.question)) > 0,
                func.length(func.trim(TicketAnswer.answer)) > 0,
            )
            .order_by(TicketAnswer.id)
            .limit(BATCH)
            # Two pollers at once: each answer goes to one of them.
            .with_for_update(skip_locked=True)
        )
    )
    if not pending:
        await session.commit()
        return []
    embeddings = await embedder.embed([row.question or "" for row in pending])
    created = []
    for row, embedding in zip(pending, embeddings, strict=True):
        # Sees the entries flushed earlier in this loop: two answers of a batch dedupe too.
        closest = await memory.closest_active(session, embedding)
        if closest is not None and closest[1] >= dedup_similarity:
            row.indexed_knowledge_id = closest[0].id
            logger.info(
                "Answer of ticket %d duplicates entry %s (similarity %.3f): not indexed",
                row.glpi_ticket_id,
                closest[0].id,
                closest[1],
            )
            continue
        entry = Knowledge(
            question=row.question,
            answer=row.answer,
            source=KnowledgeSource.LLM,
            glpi_ticket_id=row.glpi_ticket_id,
            embedding=embedding,
        )
        session.add(entry)
        await session.flush()
        row.indexed_knowledge_id = entry.id
        created.append(entry.id)
    await session.commit()
    if created:
        logger.info("Memory grew by %d validated answers: %s", len(created), created)
    return created
