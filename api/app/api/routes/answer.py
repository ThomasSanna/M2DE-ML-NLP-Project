import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    ChatModelDep,
    EmbedderDep,
    GlpiDep,
    SessionDep,
    SettingsDep,
    require_api_key,
)
from app.services import memory
from app.services.answer import build_messages, chunk_tokens
from app.services.glpi import GlpiError
from app.services.llm import LLMError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["answer"], dependencies=[Depends(require_api_key)])


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/{ticket_id}/answer/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_answer(
    ticket_id: int,
    session: SessionDep,
    embedder: EmbedderDep,
    llm: ChatModelDep,
    glpi: GlpiDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Génère la réponse au ticket en streaming SSE, puis la poste en suivi dans GLPI.

    Événements : `sources` (connaissances utilisées), `token` (N fois, `sse_chunk_tokens` tokens
    chacun), puis `done` (id du suivi GLPI) ou `error`. Le suivi n'est écrit qu'une fois, à la
    fin : un suivi public déclenche le mail GLPI au demandeur.
    """
    ticket = await glpi.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket GLPI introuvable")
    # Recherche faite avant le streaming : la session SQL n'est pas utilisée dans le flux.
    hits = await memory.search(
        session, embedder, f"{ticket.title}\n{ticket.content}", settings.answer_top_k
    )
    messages = build_messages(ticket, hits)

    async def events() -> AsyncIterator[str]:
        yield _sse(
            "sources",
            [{"id": str(k.id), "similarity": round(s, 4)} for k, s in hits],
        )
        parts: list[str] = []
        try:
            async for chunk in chunk_tokens(llm.stream(messages), settings.sse_chunk_tokens):
                parts.append(chunk)
                yield _sse("token", {"text": chunk})
            answer = "".join(parts).strip()
            if not answer:
                raise LLMError("réponse vide")
            followup_id = await glpi.add_followup(
                ticket_id, answer, private=settings.glpi_followup_private
            )
        except (LLMError, GlpiError) as exc:
            logger.error("Réponse au ticket %s : %s", ticket_id, exc)
            yield _sse("error", {"detail": "Génération ou envoi de la réponse impossible"})
            return
        yield _sse("done", {"followup_id": followup_id})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Pas de cache ni de mise en tampon par un proxy : sinon les tokens arrivent d'un bloc.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
