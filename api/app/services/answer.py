import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.answer import (
    THREAD_MESSAGE_MAX_CHARS,
    AnswerRequest,
    AnswerResult,
    Citation,
    KnowledgeCitation,
    MemoryHit,
    ThreadMessage,
    Usage,
    WebCitation,
)
from app.services import memory
from app.services.agent import run_agent
from app.services.agent_prompt import initial_messages, render_memory, render_thread, strip_refs
from app.services.embeddings import Embedder
from app.services.glpi import Glpi
from app.services.llm import ChatModel
from app.services.reranker import Reranker
from app.services.websearch import WebSearch

logger = logging.getLogger(__name__)

# The embeddings and reranker servers reject inputs over their max length: a pasted log
# must be cut, not fail the request. The model still reads the thread (truncated too).
RETRIEVAL_QUERY_MAX_CHARS = 1000


async def answer_thread(
    session: AsyncSession,
    embedder: Embedder,
    reranker: Reranker,
    model: ChatModel,
    web_search: WebSearch | None,
    settings: Settings,
    request: AnswerRequest,
) -> AnswerResult:
    """Answer the thread's last user message from the memory, then the web if needed."""
    text = _retrieval_query(request.messages)
    hits = await memory.retrieve(
        session,
        embedder,
        reranker,
        text,
        settings.retrieval_candidates,
        settings.retrieval_top_k,
        settings.rerank_threshold,
    )
    # Ends the read-only transaction so the pooled connection is not held through the
    # model and web calls. Not rollback(): it would expire the loaded entries.
    await session.commit()
    knowledge = {f"K{index}": entry for index, (entry, _) in enumerate(hits, start=1)}
    thread = [(message.role, message.content) for message in request.messages]
    logger.debug("answering thread %r", thread)

    run = await run_agent(
        model,
        web_search,
        initial_messages(
            render_memory([(entry.question, entry.answer) for entry in knowledge.values()]),
            render_thread(thread),
        ),
        set(knowledge),
        settings.agent_max_steps,
    )

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
        memory=[MemoryHit(knowledge_id=entry.id, score=score) for entry, score in hits],
        searches=run.searches,
        steps=run.steps,
        usage=Usage(prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens),
    )


async def answer_ticket(
    session: AsyncSession,
    embedder: Embedder,
    reranker: Reranker,
    model: ChatModel,
    web_search: WebSearch | None,
    glpi: Glpi,
    settings: Settings,
    ticket_id: int,
) -> AnswerResult | None:
    """Answer a GLPI ticket from its title and description; None if it does not exist."""
    ticket = await glpi.get_ticket(ticket_id)
    if ticket is None:
        return None
    # A one-message thread: the ticket's followups are not read yet.
    content = f"{ticket.title}\n\n{ticket.content}"[:THREAD_MESSAGE_MAX_CHARS]
    request = AnswerRequest(messages=[ThreadMessage(role="user", content=content)])
    return await answer_thread(session, embedder, reranker, model, web_search, settings, request)


def followup_text(result: AnswerResult) -> str:
    """The answer as the requester reads it: refs mean nothing to them, web pages can help."""
    text = strip_refs(result.answer)
    # Keyed by URL: two searches can return the same page under two refs.
    pages = {s.url: s.title for s in result.sources if isinstance(s, WebCitation)}
    if not pages:
        return text
    lines = "\n".join(f"- {title} : {url}" for url, title in pages.items())
    return f"{text}\n\nSources :\n{lines}"


def _retrieval_query(messages: list[ThreadMessage]) -> str:
    # The first user message states the problem and the last one the current request: a
    # follow-up like "Oui, toujours pareil" matches nothing on its own. Middle turns would
    # dilute the embedding. The model still reads the whole thread.
    user = [message.content for message in messages if message.role == "user"]
    parts = list(dict.fromkeys([user[0], user[-1]]))
    limit = RETRIEVAL_QUERY_MAX_CHARS // len(parts)
    return "\n".join(part[:limit] for part in parts)
