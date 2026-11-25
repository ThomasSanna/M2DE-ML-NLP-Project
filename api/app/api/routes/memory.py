import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import EmbedderDep, SessionDep, SettingsDep, require_api_key
from app.schemas.knowledge import (
    KnowledgeCreate,
    KnowledgeRead,
    ReviewDecision,
    ReviewItem,
    SearchHit,
    SearchRequest,
)
from app.services import memory, review

router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(require_api_key)])


@router.post("/knowledge", status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    data: KnowledgeCreate, session: SessionDep, embedder: EmbedderDep
) -> KnowledgeRead:
    """Add a question/answer pair to the memory (the question is embedded)."""
    knowledge = await memory.add_knowledge(session, embedder, data)
    return KnowledgeRead.model_validate(knowledge)


@router.get("/knowledge/{knowledge_id}")
async def read_knowledge(knowledge_id: uuid.UUID, session: SessionDep) -> KnowledgeRead:
    knowledge = await memory.get_knowledge(session, knowledge_id)
    if knowledge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge not found")
    return KnowledgeRead.model_validate(knowledge)


@router.post("/search")
async def search_knowledge(
    request: SearchRequest, session: SessionDep, embedder: EmbedderDep
) -> list[SearchHit]:
    """Top-k active knowledge entries closest to the question."""
    results = await memory.search(session, embedder, request.query, request.top_k)
    return [
        SearchHit(knowledge=KnowledgeRead.model_validate(knowledge), similarity=similarity)
        for knowledge, similarity in results
    ]


@router.get("/review")
async def list_review(session: SessionDep, settings: SettingsDep) -> list[ReviewItem]:
    """Entries the feedback sent to `to_review`, the longest waiting first."""
    weighting = memory.FeedbackWeighting(
        prior=settings.feedback_prior, half_life_days=settings.feedback_half_life_days
    )
    return [
        ReviewItem(
            knowledge=KnowledgeRead.model_validate(entry),
            feedback=stats.score if stats else None,
            ratings=stats.count if stats else 0,
        )
        for entry, stats in await review.list_under_review(session, weighting)
    ]


@router.post("/knowledge/{knowledge_id}/review")
async def review_knowledge(
    knowledge_id: uuid.UUID, decision: ReviewDecision, session: SessionDep, embedder: EmbedderDep
) -> KnowledgeRead:
    """A technician's decision on an entry under review: `reactivate`, `correct` (a new
    entry with the fixed answer replaces it) or `archive`."""
    try:
        entry = await review.review(
            session, embedder, knowledge_id, decision.action, decision.answer
        )
    except review.NotUnderReview as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Knowledge is not under review") from exc
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge not found")
    return KnowledgeRead.model_validate(entry)
