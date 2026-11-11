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
    MetricsDep,
    ProgressDep,
    RerankerDep,
    SessionDep,
    SettingsDep,
    WebSearchDep,
    require_api_key,
)
from app.schemas.ticket import AnswerSourcesRead, ProgressRead, SourceRead
from app.services import answer, survey
from app.services.embeddings import EmbeddingError
from app.services.glpi import GlpiError
from app.services.llm import LLMError
from app.services.progress import domain_of
from app.services.reranker import RerankerError

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
    metrics: MetricsDep,
    settings: SettingsDep,
    progress: ProgressDep,
) -> StreamingResponse:
    """Answer a GLPI ticket's thread, then post the answer as a followup of the ticket.

    Events: `answer` (an `AnswerResult`, or an `EscalationResult` when the bot hands the
    ticket over), then `done`
    (`{"followup_id": int}`) or `error`
    when GLPI rejects the followup. A missing ticket, a thread the support side wrote last
    or that was handed over, or a failing model is an HTTP error returned before the stream
    opens.
    """
    ticket = await glpi.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GLPI ticket not found")
    try:
        result = await answer.answer_ticket(
            session,
            embedder,
            reranker,
            model,
            web_search,
            metrics,
            settings,
            ticket,
            source="ticket_stream",
            board=progress,
        )
    except (EmbeddingError, RerankerError, LLMError):
        progress.failed(ticket_id)
        raise
    if result is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Nothing to answer: the support side wrote last or the ticket was handed to a"
            " technician",
        )

    async def events() -> AsyncIterator[str]:
        yield _sse("answer", result.model_dump_json())
        try:
            followup_id = await glpi.add_followup(
                ticket_id, answer.followup_text(result), private=settings.glpi_followup_private
            )
        except GlpiError as exc:
            # The 200 status is already sent: the failure can only go in the stream.
            progress.failed(ticket_id)
            logger.error("%s", exc)
            yield _sse("error", json.dumps({"detail": "GLPI unavailable"}))
            return
        # `done` reloads the plugin's page, which then reads the sources from this row. The
        # followup is posted: the run is done even if the recording of it fails.
        try:
            await survey.record_answer(session, ticket_id, followup_id, result)
        finally:
            progress.done(ticket_id, followup_id)
        await answer.post_handover_note(glpi, ticket_id, result)
        yield _sse("done", json.dumps({"followup_id": followup_id}))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # No cache and no proxy buffering, so each event reaches the client when sent.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{ticket_id}/progress")
async def ticket_progress(
    ticket_id: Annotated[int, Path(gt=0)], progress: ProgressDep
) -> ProgressRead:
    """Progress of the answer the bot writes for this ticket, `idle` if none."""
    view = progress.get(ticket_id)
    if view is None:
        return ProgressRead(status="idle")
    return ProgressRead.model_validate(
        {"status": view.status, "steps": view.steps, "followup_id": view.followup_id}
    )


@router.get("/{ticket_id}/answers")
async def ticket_answers(
    ticket_id: Annotated[int, Path(gt=0)], session: SessionDep
) -> list[AnswerSourcesRead]:
    """Web sources of each answer posted on the ticket, oldest first."""
    rows = await survey.list_web_sources(session, ticket_id)
    return [
        AnswerSourcesRead(
            followup_id=followup_id,
            sources=[
                SourceRead(
                    title=source.get("title", ""),
                    url=source.get("url", ""),
                    domain=domain_of(source.get("url", "")),
                )
                for source in sources
            ],
        )
        for followup_id, sources in rows
    ]
