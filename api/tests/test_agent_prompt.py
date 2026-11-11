import pytest
from pydantic import ValidationError

from app.services.agent_prompt import (
    NO_MEMORY,
    THREAD_MAX_CHARS,
    AnswerAction,
    EscalateAction,
    SearchAction,
    decision_schema,
    initial_messages,
    parse_action,
    render_memory,
    render_thread,
    render_web_results,
)
from app.services.websearch import WebResult


def test_decision_schema() -> None:
    assert decision_schema(True, ["K1", "W1"]) == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "answer", "escalate"]},
            "query": {"type": "string", "minLength": 1},
            "answer": {"type": "string", "minLength": 1},
            "reason": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string", "enum": ["K1", "W1"]}},
        },
        "required": ["action"],
    }


def test_answer_only_schema_requires_answer() -> None:
    assert decision_schema(False, ["K1"]) == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["answer", "escalate"]},
            "answer": {"type": "string", "minLength": 1},
            "reason": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string", "enum": ["K1"]}},
        },
        "required": ["action", "answer"],
    }


def test_schema_without_refs_has_no_sources() -> None:
    # An empty `enum` matches nothing: the field is left out instead.
    assert "sources" not in decision_schema(True, [])["properties"]
    assert "sources" not in decision_schema(False, [])["properties"]


def test_parse_action() -> None:
    assert parse_action('{"action": "search", "query": " eduroam "}') == SearchAction(
        action="search", query="eduroam"
    )
    assert parse_action('{"action": "answer", "answer": "Voir K1"}') == AnswerAction(
        action="answer", answer="Voir K1", sources=[]
    )


def test_parse_escalate_action() -> None:
    content = '{"action": "escalate", "answer": " Transmis. ", "reason": " hors sujet "}'
    assert parse_action(content) == EscalateAction(
        action="escalate", answer="Transmis.", reason="hors sujet"
    )
    # The grammar cannot require `reason`: a handover without one must not fail the run.
    assert parse_action('{"action": "escalate", "answer": "Transmis."}') == EscalateAction(
        action="escalate", answer="Transmis.", reason=""
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"action": "search"}',
        '{"action": "search", "query": "   "}',
        '{"action": "answer", "answer": ""}',
        '{"action": "escalate"}',
        '{"action": "escalate", "answer": " "}',
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

    long_thread = render_thread(
        [("user", "y" * (THREAD_MAX_CHARS + 1000)), ("assistant", "dernier message")]
    )
    assert len(long_thread) <= len("Fil :\n") + THREAD_MAX_CHARS
    assert long_thread.startswith("Fil :\nUtilisateur : yyy")
    assert long_thread.endswith("Support : dernier message")


def test_render_thread_keeps_a_long_thread_whole() -> None:
    # A pasted log or a long back-and-forth fits: the 12B server has a 32768-token context.
    thread = render_thread([("user", "y" * 5000), ("assistant", "z" * 2000), ("user", "Et ?")])

    assert (
        thread == f"Fil :\nUtilisateur : {'y' * 5000}\nSupport : {'z' * 2000}\nUtilisateur : Et ?"
    )


def test_render_thread_strips_refs_from_earlier_answers() -> None:
    thread = render_thread(
        [
            ("user", "Le wifi eduroam ne marche pas"),
            ("assistant", r"Supprimez le profil [K1] \[W2\], puis reconnectez-vous [k1, W3]."),
            ("user", "Sous W10 ça ne marche pas"),
        ]
    )

    assert thread == (
        "Fil :\nUtilisateur : Le wifi eduroam ne marche pas\n"
        "Support : Supprimez le profil, puis reconnectez-vous.\n"
        "Utilisateur : Sous W10 ça ne marche pas"
    )


def test_render_thread_keeps_the_opening_message() -> None:
    thread = render_thread(
        [
            ("user", "L'imprimante du bâtiment B n'imprime plus"),
            ("assistant", "z" * (THREAD_MAX_CHARS + 1000)),
            ("user", "Toujours pareil"),
        ]
    )

    assert len(thread) <= len("Fil :\n") + THREAD_MAX_CHARS
    assert thread.startswith("Fil :\nUtilisateur : L'imprimante du bâtiment B n'imprime plus\n…")
    assert thread.endswith("\nUtilisateur : Toujours pareil")


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
