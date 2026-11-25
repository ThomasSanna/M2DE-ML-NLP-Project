import math
import uuid
from dataclasses import dataclass

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models import Feedback, Knowledge, KnowledgeStatus
from app.schemas.knowledge import KnowledgeCreate
from app.services.embeddings import Embedder
from app.services.metrics import RunRecorder
from app.services.reranker import Reranker


@dataclass(frozen=True)
class FeedbackWeighting:
    """How the feedback on an entry, and its reuse, reorder the entries the reranker kept."""

    # Share of the feedback score in the ranking score; 0 ranks on the reranker alone.
    weight: float = 0.0
    # Pseudo-ratings at 0.5 added to every entry, so one rating cannot swing it alone.
    prior: float = 2.0
    # Age at which a rating counts half; None keeps every rating at full weight.
    half_life_days: float | None = None
    # Share of the reuse score: how often the entry was cited in answers posted to GLPI.
    reuse_weight: float = 0.0
    # Citations at which the reuse score reaches 0.5; it saturates so no entry runs away.
    reuse_half: float = 5.0


@dataclass(frozen=True)
class MemoryMatch:
    knowledge: Knowledge
    relevance: float  # reranker score in [0, 1], the one the threshold applies to
    feedback: float | None  # smoothed feedback score in [0, 1], None without any rating
    score: float  # ranking score: relevance blended with the feedback and the reuse


def reuse_score(reuse_count: int, half: float) -> float:
    """Citations mapped to [0, 1): 0 for a new entry, 0.5 at `half` citations."""
    return reuse_count / (reuse_count + half)


def feedback_score(weighted_sum: float, weight_total: float, prior: float) -> float:
    """Mean rating pulled towards a neutral 0.5 by `prior` pseudo-ratings."""
    return (0.5 * prior + weighted_sum) / (prior + weight_total)


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

    Raw similarity only, for the search route: the answer engine uses `retrieve`, which
    reranks and weights by feedback.
    """
    [embedding] = await embedder.embed([query])
    return await _nearest(session, embedding, top_k)


async def _nearest(
    session: AsyncSession, embedding: list[float], top_k: int
) -> list[tuple[Knowledge, float]]:
    distance = Knowledge.embedding.cosine_distance(embedding)
    rows = await session.execute(
        select(Knowledge, (1 - distance).label("similarity"))
        # The 3584-dim vector is most of each row and is never read back.
        .options(defer(Knowledge.embedding, raiseload=True))
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
    recorder: RunRecorder,
    weighting: FeedbackWeighting | None = None,
) -> list[MemoryMatch]:
    """Vector candidates reranked by the cross-encoder, then ordered by feedback, best first.

    The reranker sees the question and the answer: a cross-encoder judges a passage
    against the query, and the answer often holds the words the user typed. The
    embedding still covers the question only (see `search`).

    The threshold applies to the reranker score alone: feedback reorders relevant entries
    and can push a poorly rated one past `top_k`, but never lets an off-topic one in.
    """
    weighting = weighting or FeedbackWeighting()
    with recorder.call("embed") as call:
        [embedding] = await embedder.embed([text])
    call.items = 1
    with recorder.call("vector_search") as call:
        hits = await _nearest(session, embedding, candidates)
    call.items = recorder.memory_candidates = len(hits)
    if not hits:
        recorder.memory_kept = 0
        return []
    with recorder.call("rerank") as call:
        logits = await reranker.rerank(
            text, [f"{knowledge.question}\n{knowledge.answer}" for knowledge, _ in hits]
        )
    call.items = len(hits)
    scored = [
        (knowledge, _sigmoid(logit)) for (knowledge, _), logit in zip(hits, logits, strict=True)
    ]
    # Before the threshold: a run with no kept entry still shows how close the best one was.
    recorder.memory_top_score = max(score for _, score in scored)
    relevant = [(knowledge, score) for knowledge, score in scored if score >= threshold]
    feedback = (
        await _feedback_scores(session, [knowledge.id for knowledge, _ in relevant], weighting)
        if relevant and weighting.weight > 0
        else {}
    )
    matches = [
        MemoryMatch(
            knowledge=knowledge,
            relevance=relevance,
            feedback=feedback.get(knowledge.id),
            score=(1 - weighting.weight - weighting.reuse_weight) * relevance
            + weighting.weight * feedback.get(knowledge.id, 0.5)
            + weighting.reuse_weight * reuse_score(knowledge.reuse_count, weighting.reuse_half),
        )
        for knowledge, relevance in relevant
    ]
    kept = sorted(matches, key=lambda match: (match.score, match.relevance), reverse=True)[:top_k]
    recorder.memory_kept = len(kept)
    return kept


async def _feedback_scores(
    session: AsyncSession, knowledge_ids: list[uuid.UUID], weighting: FeedbackWeighting
) -> dict[uuid.UUID, float]:
    """Smoothed feedback score of each entry that has at least one rating."""
    if weighting.half_life_days is None:
        decay = literal(1.0)
    else:
        age_days = func.extract("epoch", func.now() - Feedback.created_at) / 86400
        decay = func.power(0.5, age_days / weighting.half_life_days)
    rows = await session.execute(
        select(Feedback.knowledge_id, func.sum(Feedback.score * decay), func.sum(decay))
        .where(Feedback.knowledge_id.in_(knowledge_ids))
        .group_by(Feedback.knowledge_id)
    )
    return {
        knowledge_id: feedback_score(float(weighted_sum), float(weight_total), weighting.prior)
        for knowledge_id, weighted_sum, weight_total in rows
    }


def _sigmoid(logit: float) -> float:
    # Logits are unbounded and math.exp overflows past ~709: only ever exponentiate a
    # non-positive number.
    if logit >= 0:
        return 1 / (1 + math.exp(-logit))
    exp = math.exp(logit)
    return exp / (1 + exp)
