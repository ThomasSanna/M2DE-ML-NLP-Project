import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Knowledge, KnowledgeStatus
from app.schemas.knowledge import KnowledgeCreate
from app.services.embeddings import Embedder


async def add_knowledge(
    session: AsyncSession, embedder: Embedder, data: KnowledgeCreate
) -> Knowledge:
    [embedding] = await embedder.embed([data.question])
    knowledge = Knowledge(**data.model_dump(), embedding=embedding)
    session.add(knowledge)
    await session.commit()
    await session.refresh(knowledge)
    return knowledge


async def get_knowledge(session: AsyncSession, knowledge_id: uuid.UUID) -> Knowledge | None:
    return await session.get(Knowledge, knowledge_id)


async def search(
    session: AsyncSession, embedder: Embedder, query: str, top_k: int
) -> list[tuple[Knowledge, float]]:
    """Top-k des connaissances actives par similarité cosinus de la question.

    Similarité brute uniquement : la pondération (feedback, fraîcheur, réutilisation…)
    viendra se greffer ici avec la pipeline.
    """
    [embedding] = await embedder.embed([query])
    distance = Knowledge.embedding.cosine_distance(embedding)
    rows = await session.execute(
        select(Knowledge, (1 - distance).label("similarity"))
        .where(Knowledge.status == KnowledgeStatus.ACTIVE)
        .order_by(distance)
        .limit(top_k)
    )
    return [(knowledge, similarity) for knowledge, similarity in rows.all()]
