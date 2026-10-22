import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Knowledge, KnowledgeStatus
from app.schemas.knowledge import KnowledgeCreate
from app.services.embeddings import Embedder
from app.services.reranker import Reranker


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
    """Top-k active knowledge entries by cosine similarity of the question.

    Raw similarity only: the weighting (feedback, freshness, reuse...) will be
    plugged in here with the pipeline.
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


async def retrieve(
    session: AsyncSession,
    embedder: Embedder,
    reranker: Reranker,
    text: str,
    candidates: int,
    top_k: int,
    threshold: float,
) -> list[tuple[Knowledge, float]]:
    """Vector candidates reranked by the cross-encoder, best first, scores in [0, 1].

    The reranker sees the question and the answer: a cross-encoder judges a passage
    against the query, and the answer often holds the words the user typed. The
    embedding still covers the question only (see `search`).
    """
    hits = await search(session, embedder, text, candidates)
    if not hits:
        return []
    logits = await reranker.rerank(
        text, [f"{knowledge.question}\n{knowledge.answer}" for knowledge, _ in hits]
    )
    scored = [
        (knowledge, _sigmoid(logit)) for (knowledge, _), logit in zip(hits, logits, strict=True)
    ]
    kept = sorted(
        ((knowledge, score) for knowledge, score in scored if score >= threshold),
        key=lambda hit: hit[1],
        reverse=True,
    )
    return kept[:top_k]


def _sigmoid(logit: float) -> float:
    # Logits are unbounded and math.exp overflows past ~709: only ever exponentiate a
    # non-positive number.
    if logit >= 0:
        return 1 / (1 + math.exp(-logit))
    exp = math.exp(logit)
    return exp / (1 + exp)
