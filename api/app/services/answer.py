import base64
import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.answer import (
    THREAD_MESSAGE_MAX_CHARS,
    AnswerOutcome,
    AnswerRequest,
    AnswerResult,
    Citation,
    EscalationResult,
    KnowledgeCitation,
    MemoryHit,
    ThreadMessage,
    Usage,
    WebCitation,
)
from app.services import guard as input_guard
from app.services import memory, survey
from app.services.agent import run_agent
from app.services.agent_prompt import initial_messages, render_memory, render_thread, strip_refs
from app.services.embeddings import Embedder, EmbeddingError
from app.services.glpi import Author, Glpi, GlpiError, GlpiTicket
from app.services.llm import ChatModel, LLMError
from app.services.metrics import (
    CsvMetricsWriter,
    Outcome,
    RunRecorder,
    Source,
    build_run_row,
)
from app.services.progress import ProgressBoard, TicketProgress
from app.services.reranker import Reranker, RerankerError
from app.services.websearch import WebSearch

logger = logging.getLogger(__name__)

# The embeddings and reranker servers reject inputs over their max length: a pasted log
# must be cut, not fail the request. The model still reads the thread (truncated too).
RETRIEVAL_QUERY_MAX_CHARS = 1000

# Remote failures a run can end on. Any other exception is a bug: no metrics row.
_OUTCOMES: dict[type[RuntimeError], Outcome] = {
    EmbeddingError: "embedding_error",
    RerankerError: "reranker_error",
    LLMError: "llm_error",
}


async def answer_thread(
    session: AsyncSession,
    embedder: Embedder,
    reranker: Reranker,
    model: ChatModel,
    web_search: WebSearch | None,
    metrics: CsvMetricsWriter | None,
    settings: Settings,
    request: AnswerRequest,
    *,
    guard: ChatModel | None,
    source: Source,
    ticket_id: int | None = None,
    progress: TicketProgress | None = None,
    images: Sequence[str] = (),
) -> AnswerOutcome:
    """Answer the thread's last user message from the memory, then the web if needed.

    The `guard` model, when set, first reads the requester's text (the retrieval query) and
    hands an off-topic thread over to a technician before any other call. `images`
    (`data:` URIs, oldest first) go to the model with the thread; the retrieval reads the
    text only."""
    recorder = RunRecorder()
    text = _retrieval_query(request.messages)

    def record(outcome: Outcome, answer: str | None, cited_refs: list[str] | None) -> None:
        if metrics is None:
            return
        row = build_run_row(
            recorder,
            settings,
            source=source,
            ticket_id=ticket_id,
            query=text,
            messages=request.messages,
            web_search_enabled=web_search is not None,
            outcome=outcome,
            answer=answer,
            cited_refs=cited_refs,
        )
        metrics.write(row, recorder.calls)

    if guard is not None and not await input_guard.is_on_topic(guard, text, recorder):
        record("blocked", input_guard.BLOCKED_MESSAGE, [])
        return EscalationResult(
            message=input_guard.BLOCKED_MESSAGE,
            reason=input_guard.BLOCKED_REASON,
            memory=[],
            searches=[],
            steps=0,
            usage=Usage(prompt_tokens=0, completion_tokens=0),
        )

    try:
        with recorder.phase("retrieval"):
            hits = await memory.retrieve(
                session,
                embedder,
                reranker,
                text,
                settings.retrieval_candidates,
                settings.retrieval_top_k,
                settings.rerank_threshold,
                recorder,
                memory.FeedbackWeighting(
                    settings.feedback_weight,
                    settings.feedback_prior,
                    settings.feedback_half_life_days,
                    settings.reuse_weight,
                    settings.reuse_half,
                ),
            )
        # Ends the read-only transaction so the pooled connection is not held through the
        # model and web calls. Not rollback(): it would expire the loaded entries.
        await session.commit()
        knowledge = {f"K{index}": hit.knowledge for index, hit in enumerate(hits, start=1)}
        thread = [(message.role, message.content) for message in request.messages]
        logger.debug("answering thread %r", thread)

        with recorder.phase("agent"):
            run = await run_agent(
                model,
                web_search,
                initial_messages(
                    render_memory([(entry.question, entry.answer) for entry in knowledge.values()]),
                    render_thread(thread),
                    images,
                ),
                set(knowledge),
                settings.agent_max_steps,
                recorder,
                progress=progress,
            )
    except tuple(_OUTCOMES) as exc:
        record(next(o for error, o in _OUTCOMES.items() if isinstance(exc, error)), None, None)
        raise
    memory_hits = [
        MemoryHit(
            knowledge_id=hit.knowledge.id,
            score=hit.relevance,
            feedback=hit.feedback,
            weighted_score=hit.score,
            reuse_count=hit.knowledge.reuse_count,
        )
        for hit in hits
    ]
    usage = Usage(prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens)
    if run.escalation_reason is not None:
        # The reason sums up the thread: DEBUG only, like the thread itself.
        logger.debug("handed over to a technician: %s", run.escalation_reason)
        record("escalated", run.answer, [])
        return EscalationResult(
            message=run.answer,
            reason=run.escalation_reason,
            memory=memory_hits,
            searches=run.searches,
            steps=run.steps,
            usage=usage,
        )
    record("answered", run.answer, run.cited_refs)

    sources: list[Citation] = []
    for ref in run.cited_refs:
        if ref in knowledge:
            entry = knowledge[ref]
            sources.append(
                KnowledgeCitation(ref=ref, knowledge_id=entry.id, question=entry.question)
            )
        else:
            result = run.web_results[ref]
            sources.append(WebCitation(ref=ref, url=result.url, title=result.title))
    return AnswerResult(
        answer=run.answer,
        sources=sources,
        memory=memory_hits,
        searches=run.searches,
        steps=run.steps,
        usage=usage,
    )


async def answer_ticket(
    session: AsyncSession,
    embedder: Embedder,
    reranker: Reranker,
    model: ChatModel,
    web_search: WebSearch | None,
    metrics: CsvMetricsWriter | None,
    settings: Settings,
    glpi: Glpi,
    ticket: GlpiTicket,
    *,
    guard: ChatModel | None,
    source: Source,
    board: ProgressBoard | None = None,
) -> AnswerOutcome | None:
    """Answer a GLPI ticket's thread; None when the support side wrote last or the bot
    handed the ticket to a technician."""
    messages = ticket_messages(ticket)
    if messages[-1].role != "user" or await survey.is_escalated(session, ticket.id):
        return None
    # Opened only once the bot owes an answer: the followup the bot itself posts
    # comes back as a ticket_answer event and must not leave a stale state.
    progress = board.start(ticket.id) if board is not None else None
    images = await _ticket_images(glpi, ticket, settings.llm_max_images)
    request = AnswerRequest(messages=messages)
    return await answer_thread(
        session,
        embedder,
        reranker,
        model,
        web_search,
        metrics,
        settings,
        request,
        guard=guard,
        source=source,
        ticket_id=ticket.id,
        progress=progress,
        images=images,
    )


def ticket_messages(ticket: GlpiTicket) -> list[ThreadMessage]:
    """The ticket's thread as the model reads it: title and description, then the public
    followups, the requesters' as `user` and everyone else's as `assistant`."""
    return [
        ThreadMessage(
            role="user", content=f"{ticket.title}\n\n{ticket.content}"[:THREAD_MESSAGE_MAX_CHARS]
        )
    ] + [
        ThreadMessage(
            role="user" if followup.author is Author.REQUESTER else "assistant",
            content=followup.content[:THREAD_MESSAGE_MAX_CHARS],
        )
        for followup in ticket.followups
    ]


def ticket_question(ticket: GlpiTicket) -> str:
    """The text the memory was searched with for this ticket: the question a new entry
    learnt from the answer is stored under, so later searches match it the same way."""
    return _retrieval_query(ticket_messages(ticket))


async def _ticket_images(glpi: Glpi, ticket: GlpiTicket, limit: int) -> list[str]:
    """The requester's latest images as `data:` URIs, oldest first.

    A help, not the answer: a GLPI failure here is logged and the thread is answered from
    its text.
    """
    try:
        images = await glpi.get_images(ticket, limit)
    except GlpiError as exc:
        logger.warning("Images of ticket %d skipped: %s", ticket.id, exc)
        return []
    return [
        f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
        for image in reversed(images)
    ]


def followup_text(result: AnswerOutcome) -> str:
    """The answer as the requester reads it: refs mean nothing to them, web pages can help."""
    if isinstance(result, EscalationResult):
        return strip_refs(result.message)
    text = strip_refs(result.answer)
    # Keyed by URL: two searches can return the same page under two refs.
    pages = {s.url: s.title for s in result.sources if isinstance(s, WebCitation)}
    if not pages:
        return text
    lines = "\n".join(f"- {title} : {url}" for url, title in pages.items())
    return f"{text}\n\nSources :\n{lines}"


async def post_handover_note(glpi: Glpi, ticket_id: int, result: AnswerOutcome) -> None:
    """Tell the technicians, in a private followup, why the bot handed the ticket over.

    Call it after the public followup is recorded: the note is a help, not the answer, so a
    GLPI failure here is logged and must not fail the event (a retry would find the ticket
    handed over and post nothing).
    """
    if not isinstance(result, EscalationResult) or not result.reason.strip():
        return
    try:
        await glpi.add_followup(
            ticket_id, f"Passation à un technicien. Raison : {result.reason.strip()}", private=True
        )
    except GlpiError as exc:
        logger.warning("Handover note on ticket %d failed: %s", ticket_id, exc)


def _retrieval_query(messages: list[ThreadMessage]) -> str:
    # The first user message states the problem and the last one the current request: a
    # follow-up like "Oui, toujours pareil" matches nothing on its own. Middle turns would
    # dilute the embedding. The model still reads the whole thread.
    user = [message.content for message in messages if message.role == "user"]
    parts = list(dict.fromkeys([user[0], user[-1]]))
    limit = RETRIEVAL_QUERY_MAX_CHARS // len(parts)
    return "\n".join(part[:limit] for part in parts)
