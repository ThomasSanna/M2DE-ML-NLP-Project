import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    ChatModelDep,
    EmbedderDep,
    GlpiDep,
    RerankerDep,
    SessionDep,
    SettingsDep,
    WebSearchDep,
    require_api_key,
)
from app.services import answer
from app.services.glpi import GlpiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["answer"], dependencies=[Depends(require_api_key)])


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


@router.post(
    "/{ticket_id}/answer/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_ticket_answer(
    ticket_id: Annotated[int, Path(gt=0)],
    session: SessionDep,
    embedder: EmbedderDep,
    reranker: RerankerDep,
    model: ChatModelDep,
    web_search: WebSearchDep,
    glpi: GlpiDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Answer a GLPI ticket, then post the answer as a followup of the ticket.

    Events: `answer` (an `AnswerResult`), then `done` (`{"followup_id": int}`) or `error`
    when GLPI rejects the followup. A missing ticket or a failing model is an HTTP error
    returned before the stream opens.
    """
    result = await answer.answer_ticket(
        session, embedder, reranker, model, web_search, glpi, settings, ticket_id
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GLPI ticket not found")

    async def events() -> AsyncIterator[str]:
        yield _sse("answer", result.model_dump_json())
        try:
            followup_id = await glpi.add_followup(
                ticket_id, result.answer, private=settings.glpi_followup_private
            )
        except GlpiError as exc:
            # The 200 status is already sent: the failure can only go in the stream.
            logger.error("%s", exc)
            yield _sse("error", json.dumps({"detail": "GLPI unavailable"}))
            return
        yield _sse("done", json.dumps({"followup_id": followup_id}))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # No cache and no proxy buffering, so each event reaches the client when sent.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
