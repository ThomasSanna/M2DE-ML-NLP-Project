from fastapi import APIRouter, Depends

from app.api.deps import (
    ChatModelDep,
    EmbedderDep,
    RerankerDep,
    SessionDep,
    SettingsDep,
    WebSearchDep,
    require_api_key,
)
from app.schemas.answer import AnswerRequest, AnswerResult
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
    settings: SettingsDep,
) -> AnswerResult:
    """Answer a ticket thread from the memory and, if needed, a web search, with sources."""
    return await answer.answer_thread(
        session, embedder, reranker, model, web_search, settings, data
    )
