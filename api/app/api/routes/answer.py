from fastapi import APIRouter, Depends

from app.api.deps import (
    ChatModelDep,
    EmbedderDep,
    MetricsDep,
    RerankerDep,
    SessionDep,
    SettingsDep,
    WebSearchDep,
    require_api_key,
)
from app.schemas.answer import AnswerOutcome, AnswerRequest
from app.services import answer

router = APIRouter(tags=["answer"], dependencies=[Depends(require_api_key)])


@router.post("/answer")
async def create_answer(
    data: AnswerRequest,
    session: SessionDep,
    embedder: EmbedderDep,
    reranker: RerankerDep,
    model: ChatModelDep,
    web_search: WebSearchDep,
    metrics: MetricsDep,
    settings: SettingsDep,
) -> AnswerOutcome:
    """Answer a ticket thread from the memory and, if needed, a web search, with sources,
    or hand it over to a technician."""
    return await answer.answer_thread(
        session, embedder, reranker, model, web_search, metrics, settings, data, source="api"
    )
