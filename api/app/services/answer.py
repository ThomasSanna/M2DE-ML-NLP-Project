import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.answer import (
    AnswerRequest,
    AnswerResult,
    Citation,
    KnowledgeCitation,
    MemoryHit,
    Usage,
    WebCitation,
)
from app.services import memory
from app.services.agent import run_agent
from app.services.agent_prompt import initial_messages, render_memory, render_thread
from app.services.embeddings import Embedder
from app.services.llm import ChatModel
from app.services.reranker import Reranker
from app.services.websearch import WebSearch

logger = logging.getLogger(__name__)


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
    # Retrieval uses the last message only: it holds the current request, and older
    # turns would dilute the embedding. The model still reads the whole thread.
    text = request.messages[-1].content
    hits = await memory.retrieve(
        session,
        embedder,
        reranker,
        text,
        settings.retrieval_candidates,
        settings.retrieval_top_k,
        settings.rerank_threshold,
    )
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
