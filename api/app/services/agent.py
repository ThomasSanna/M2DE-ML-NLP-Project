"""Bounded search, answer or escalate loop over the chat model.

Messages are append-only: each step adds the raw model output and one user message
with its result, so the server can reuse the cached prompt prefix.
"""

import logging
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.services.agent_prompt import (
    DUPLICATE_SEARCH,
    MAX_STEPS_NOTICE,
    REF_GROUP,
    SEARCH_UNAVAILABLE,
    AnswerAction,
    EscalateAction,
    SearchAction,
    decision_schema,
    invalid_output,
    parse_action,
    render_web_results,
)
from app.services.llm import ChatMessage, ChatModel, Completion, LLMError
from app.services.metrics import RunRecorder
from app.services.websearch import WebResult, WebSearch, WebSearchError

logger = logging.getLogger(__name__)

_REF = re.compile(r"\b[KW]\d+\b", re.IGNORECASE)


@dataclass(frozen=True)
class AgentRun:
    answer: str
    # Existing refs only, deduplicated, in citation order.
    cited_refs: list[str]
    # Queries the web search answered; failed calls are left out.
    searches: list[str]
    web_results: dict[str, WebResult]
    steps: int
    prompt_tokens: int
    completion_tokens: int
    # Set when the model handed the request to a technician: `answer` is then the message
    # to the requester and nothing is cited.
    escalation_reason: str | None = None


def _parse(completion: Completion) -> SearchAction | AnswerAction | EscalateAction | str:
    """The action, or a short error to feed back to the model."""
    if completion.finish_reason == "length":
        return "réponse coupée par la limite de longueur"
    try:
        return parse_action(completion.content)
    except ValidationError as exc:
        return exc.errors()[0]["msg"]


async def run_agent(
    model: ChatModel,
    web_search: WebSearch | None,
    messages: list[ChatMessage],
    memory_refs: set[str],
    max_steps: int,
    recorder: RunRecorder,
) -> AgentRun:
    messages = list(messages)
    searches: list[str] = []
    searched: set[str] = set()
    web_results: dict[str, WebResult] = {}
    prompt_tokens = completion_tokens = 0

    for step in range(1, max_steps + 1):
        last = step == max_steps
        if last:
            # Appended to the message not sent yet: roles keep alternating and the
            # cached prefix is unchanged. The grammar below then forbids `search`.
            final = messages[-1]
            messages[-1] = {
                "role": final["role"],
                "content": f"{final['content']}\n\n{MAX_STEPS_NOTICE}",
            }
        known = memory_refs | web_results.keys()
        with recorder.call("llm", step) as llm_call:
            completion = await model.complete(
                messages, decision_schema(web_search is not None and not last, sorted(known))
            )
        llm_call.prompt_tokens = completion.prompt_tokens
        llm_call.completion_tokens = completion.completion_tokens
        llm_call.cached_tokens = completion.cached_tokens
        llm_call.finish_reason = completion.finish_reason
        prompt_tokens += completion.prompt_tokens
        completion_tokens += completion.completion_tokens
        messages.append({"role": "assistant", "content": completion.content})
        action = _parse(completion)
        llm_call.action = "invalid" if isinstance(action, str) else action.action

        if isinstance(action, str):
            logger.info("agent step %d: invalid output (%s)", step, action)
            if last:
                raise LLMError(f"Invalid model output at the last step: {action}")
            feedback = invalid_output(action)
        elif isinstance(action, EscalateAction):
            logger.info(
                "agent step %d: escalate %r (prompt=%d, completion=%d)",
                step,
                action.reason,
                completion.prompt_tokens,
                completion.completion_tokens,
            )
            # A handover cites nothing: the message to the requester is not a source, so
            # no ref is dropped.
            recorder.dropped_refs = 0
            return AgentRun(
                answer=action.answer,
                cited_refs=[],
                searches=searches,
                web_results=web_results,
                steps=step,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                escalation_reason=action.reason,
            )
        elif isinstance(action, AnswerAction):
            logger.info(
                "agent step %d: answer (prompt=%d, completion=%d)",
                step,
                completion.prompt_tokens,
                completion.completion_tokens,
            )
            # Small models cite inline and leave `sources` empty: refs are read from both,
            # explicit sources first. In the answer only bracketed refs count: "W10" there
            # is Windows, not a source.
            inline = " ".join(REF_GROUP.findall(action.answer))
            refs = [ref.upper() for ref in _REF.findall(" ".join([*action.sources, inline]))]
            unknown = set(refs) - known
            recorder.dropped_refs = len(unknown)
            if unknown:
                logger.info("agent dropped unknown refs %s", sorted(unknown))
            return AgentRun(
                answer=action.answer,
                cited_refs=list(dict.fromkeys(ref for ref in refs if ref in known)),
                searches=searches,
                web_results=web_results,
                steps=step,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        else:
            logger.info(
                "agent step %d: search %r (prompt=%d, completion=%d)",
                step,
                action.query,
                completion.prompt_tokens,
                completion.completion_tokens,
            )
            if last:
                raise LLMError("Model searched at the last step despite the grammar")
            key = action.query.casefold()
            if web_search is None:
                recorder.searches_unavailable += 1
                feedback = SEARCH_UNAVAILABLE
            elif key in searched:
                recorder.searches_duplicate += 1
                feedback = DUPLICATE_SEARCH
            else:
                try:
                    with recorder.call("web_search", step) as web_call:
                        response = await web_search.search(action.query)
                except WebSearchError as exc:
                    # Not recorded: the model may retry the query, nothing was shown.
                    logger.warning("Web search failed: %s", exc)
                    feedback = SEARCH_UNAVAILABLE
                else:
                    results = response.results
                    web_call.items = len(results)
                    web_call.cost_usd = response.cost_usd
                    searched.add(key)
                    searches.append(action.query)
                    first_ref = len(web_results) + 1
                    for ref, result in enumerate(results, start=first_ref):
                        web_results[f"W{ref}"] = result
                    feedback = render_web_results(action.query, results, first_ref)
        messages.append({"role": "user", "content": feedback})

    raise LLMError("Agent stopped without an answer")
