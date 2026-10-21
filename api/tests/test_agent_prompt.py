import pytest
from pydantic import ValidationError

from app.services.agent_prompt import (
    NO_MEMORY,
    THREAD_MAX_CHARS,
    AnswerAction,
    SearchAction,
    decision_schema,
    initial_messages,
    parse_action,
    render_memory,
    render_thread,
    render_web_results,
)
from app.services.websearch import WebResult


def _schema(actions: list[str]) -> dict[str, object]:
    # Flat object on purpose: TGI rejects a root `oneOf`.
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": actions},
            "query": {"type": "string"},
            "answer": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["action"],
    }


def test_decision_schema() -> None:
    assert decision_schema(True) == _schema(["search", "answer"])
    assert decision_schema(False) == _schema(["answer"])


def test_parse_action() -> None:
    assert parse_action('{"action": "search", "query": " eduroam "}') == SearchAction(
        action="search", query="eduroam"
    )
    assert parse_action('{"action": "answer", "answer": "Voir K1"}') == AnswerAction(
        action="answer", answer="Voir K1", sources=[]
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"action": "search"}',
        '{"action": "search", "query": "   "}',
        '{"action": "answer", "answer": ""}',
    ],
)
def test_parse_action_rejects_invalid_output(content: str) -> None:
    with pytest.raises(ValidationError):
        parse_action(content)


def test_render_memory() -> None:
    assert render_memory([]) == f"Mémoire :\n{NO_MEMORY}"

    block = render_memory([("q1", "a1"), ("q2", "x" * 1000)])
    assert "[K1] Q : q1" in block
    assert "[K2] Q : q2" in block
    assert "x" * 799 + "…" in block
    assert "x" * 800 not in block


def test_render_thread() -> None:
    assert render_thread([("user", "a"), ("assistant", "b")]) == (
        "Fil :\nUtilisateur : a\nSupport : b"
    )

    long_thread = render_thread([("user", "y" * 5000), ("assistant", "dernier message")])
    assert len(long_thread) <= len("Fil :\n") + THREAD_MAX_CHARS
    assert long_thread.endswith("Support : dernier message")


def test_render_web_results() -> None:
    results = [WebResult("t1", "u1", None, "e1"), WebResult("t2", "u2", "2025-01-02", "e2")]

    block = render_web_results("q", results, first_ref=4)

    assert block.startswith("Résultats web pour « q » :")
    assert "[W4] t1 - u1" in block
    assert "[W5] t2 - u2" in block
    assert render_web_results("q", [], 1) == "Résultats web pour « q » : aucun résultat."


def test_initial_messages() -> None:
    messages = initial_messages("Mémoire : …", "Fil : …")
    other = initial_messages("Mémoire : autre", "Fil : autre")

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == "Mémoire : …\n\nFil : …"
    assert messages[0]["content"] == other[0]["content"]
