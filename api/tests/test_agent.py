import asyncio
import json

import pytest

from app.services.agent import AgentRun, run_agent
from app.services.agent_prompt import (
    DUPLICATE_SEARCH,
    MAX_STEPS_NOTICE,
    SEARCH_UNAVAILABLE,
    initial_messages,
)
from app.services.llm import Completion, LLMError
from app.services.websearch import WebResult
from tests.conftest import FakeWebSearch, ScriptedChatModel

RESULTS = [
    WebResult("Eduroam macOS", "https://a.fr", None, "Supprimez le profil"),
    WebResult("CAT eduroam", "https://cat.eduroam.org", "2025-01-02", "Installeur"),
]


def _search(query: str) -> str:
    return json.dumps({"action": "search", "query": query})


def _answer(answer: str = "Réponse", sources: list[str] | None = None) -> str:
    return json.dumps({"action": "answer", "answer": answer, "sources": sources or []})


def _run(
    model: ScriptedChatModel,
    web: FakeWebSearch | None,
    max_steps: int = 3,
    memory_refs: set[str] | None = None,
) -> AgentRun:
    messages = initial_messages("Mémoire : …", "Fil : …")
    refs = {"K1"} if memory_refs is None else memory_refs
    return asyncio.run(run_agent(model, web, messages, refs, max_steps))


def _enum(schema: dict[str, object]) -> object:
    return json.loads(json.dumps(schema))["properties"]["action"]["enum"]


def _last_content(model: ScriptedChatModel, call: int) -> str:
    return model.calls[call][0][-1]["content"]


def test_direct_answer() -> None:
    model = ScriptedChatModel([_answer("Voir [K1]", ["K1"])])
    web = FakeWebSearch(RESULTS)

    run = _run(model, web)

    assert run.answer == "Voir [K1]"
    assert run.cited_refs == ["K1"]
    assert run.steps == 1
    assert run.searches == []
    assert web.queries == []
    assert (run.prompt_tokens, run.completion_tokens) == (100, 10)


def test_search_then_answer() -> None:
    model = ScriptedChatModel([_search("eduroam mac"), _answer("Voir [W1]", ["W1"])])
    web = FakeWebSearch(RESULTS)

    run = _run(model, web)

    assert web.queries == ["eduroam mac"]
    last = model.calls[1][0][-1]
    assert last["role"] == "user"
    assert last["content"].startswith("Résultats web pour « eduroam mac »")
    assert "[W1]" in last["content"]
    assert "[W2]" in last["content"]
    assert set(run.web_results) == {"W1", "W2"}
    assert run.searches == ["eduroam mac"]
    assert run.cited_refs == ["W1"]
    assert run.steps == 2
    assert (run.prompt_tokens, run.completion_tokens) == (200, 20)


def test_messages_are_append_only() -> None:
    model = ScriptedChatModel([_search("a"), _search("b"), _answer()])

    _run(model, FakeWebSearch(RESULTS))

    for before, after in zip(model.calls, model.calls[1:], strict=False):
        assert after[0][: len(before[0])] == before[0]
    roles = [message["role"] for message in model.calls[-1][0]]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]


def test_web_refs_continue_across_searches() -> None:
    model = ScriptedChatModel([_search("a"), _search("b"), _answer()])

    run = _run(model, FakeWebSearch(RESULTS))

    assert set(run.web_results) == {"W1", "W2", "W3", "W4"}


def test_duplicate_query_skips_web_search() -> None:
    model = ScriptedChatModel([_search("Eduroam Mac"), _search(" eduroam mac "), _answer()])
    web = FakeWebSearch(RESULTS)

    run = _run(model, web)

    assert web.queries == ["Eduroam Mac"]
    assert run.searches == ["Eduroam Mac"]
    assert DUPLICATE_SEARCH in _last_content(model, 2)


def test_empty_web_results_consume_no_ref() -> None:
    model = ScriptedChatModel([_search("introuvable"), _answer("Rien", ["W1"])])

    run = _run(model, FakeWebSearch([]))

    assert "aucun résultat" in _last_content(model, 1)
    assert run.web_results == {}
    assert run.cited_refs == []


def test_invalid_output_is_fed_back() -> None:
    model = ScriptedChatModel(["not json", _answer()])

    run = _run(model, FakeWebSearch(RESULTS))

    assert run.steps == 2
    assert _last_content(model, 1).startswith("Sortie invalide")


def test_truncated_output_is_fed_back() -> None:
    truncated = Completion(_answer(), "length", 100, 10)
    model = ScriptedChatModel([truncated, _answer("Complète")])

    run = _run(model, FakeWebSearch(RESULTS))

    assert run.answer == "Complète"
    assert _last_content(model, 1).startswith("Sortie invalide")


def test_last_step_allows_only_answer() -> None:
    model = ScriptedChatModel([_search("a"), _answer()])

    _run(model, FakeWebSearch(RESULTS), max_steps=2)

    assert _enum(model.calls[0][1]) == ["search", "answer"]
    assert _enum(model.calls[1][1]) == ["answer"]
    assert _last_content(model, 1).endswith(MAX_STEPS_NOTICE)


def test_single_step_notice_goes_on_initial_message() -> None:
    model = ScriptedChatModel([_answer()])

    _run(model, FakeWebSearch(RESULTS), max_steps=1)

    [(messages, schema)] = model.calls
    assert len(messages) == 2
    assert messages[1]["content"].endswith(MAX_STEPS_NOTICE)
    assert _enum(schema) == ["answer"]


def test_citations_are_filtered_and_deduplicated() -> None:
    model = ScriptedChatModel([_answer("Voir", ["K1", "K9", "K1", "W1"])])

    run = _run(model, FakeWebSearch(RESULTS))

    assert run.cited_refs == ["K1"]


def test_web_search_error_is_fed_back() -> None:
    model = ScriptedChatModel([_search("eduroam"), _answer("Avec la mémoire")])

    run = _run(model, FakeWebSearch(fail=True))

    assert SEARCH_UNAVAILABLE in _last_content(model, 1)
    assert run.answer == "Avec la mémoire"
    assert run.web_results == {}


def test_search_disabled_allows_only_answer() -> None:
    model = ScriptedChatModel([_answer()])

    _run(model, None)

    assert _enum(model.calls[0][1]) == ["answer"]


def test_invalid_last_step_raises_llm_error() -> None:
    with pytest.raises(LLMError):
        _run(ScriptedChatModel(["{"]), FakeWebSearch(RESULTS), max_steps=1)


def test_citations_tolerate_brackets_and_case() -> None:
    model = ScriptedChatModel([_answer("Voir [K1]", ["[K1]", " k1 "])])

    run = _run(model, FakeWebSearch(RESULTS))

    assert run.cited_refs == ["K1"]


# Outputs observed on the 4B server: inline refs with `sources` left empty, and a
# comma-separated list split into odd items.
@pytest.mark.parametrize(
    ("answer", "sources", "cited"),
    [
        ("Faites ceci [K2], puis cela [K1].", [], ["K2", "K1"]),
        ("Voir la procédure.", ["K1", ", K2"], ["K1", "K2"]),
        ("Voir [K2].", ["K1"], ["K1", "K2"]),
        ("Voir [K2, k1].", [], ["K2", "K1"]),
        ("K1 ne s'applique pas ici, voir [K2].", [], ["K2"]),
    ],
)
def test_citations_are_read_from_answer_and_sources(
    answer: str, sources: list[str], cited: list[str]
) -> None:
    model = ScriptedChatModel([_answer(answer, sources)])

    run = _run(model, None, memory_refs={"K1", "K2"})

    assert run.cited_refs == cited


def test_unbracketed_refs_in_answer_are_not_citations() -> None:
    results = [WebResult(f"t{index}", f"https://{index}.fr", None, "e") for index in range(11)]
    model = ScriptedChatModel([_search("wifi"), _answer("Sous W10 ou W11, voir [W1].")])

    run = _run(model, FakeWebSearch(results))

    assert {"W1", "W10", "W11"} <= set(run.web_results)
    assert run.cited_refs == ["W1"]
