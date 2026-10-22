"""Everything the model reads: system prompt, rendered context, feedback messages and the
JSON action protocol. Model-facing text is French; the rest of the code is English."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.services.llm import ChatMessage
from app.services.websearch import WebResult

# Caps keep the prompt near 2800 tokens with 3 memory entries and 2 searches, under the
# 4000-token input limit of the 4B server.
MEMORY_ANSWER_MAX_CHARS = 800
THREAD_MAX_CHARS = 2000
THREAD_HEAD_MAX_CHARS = 600

NO_MEMORY = "Aucune connaissance pertinente en mémoire."
DUPLICATE_SEARCH = "Déjà cherché, résultats ci-dessus. Réponds ou cherche autre chose."
SEARCH_UNAVAILABLE = "Recherche web indisponible, réponds avec la mémoire."
MAX_STEPS_NOTICE = "Nombre maximal d'étapes atteint : réponds maintenant."

SYSTEM_PROMPT = """\
Tu es l'assistant du support informatique de l'Université de Corse. \
Tu aides l'utilisateur à résoudre la demande décrite dans le fil.

Règles :
- Réponds en français, de façon concise et concrète.
- Appuie-toi uniquement sur la mémoire et les résultats web fournis. \
N'invente jamais d'URL, de procédure ni de contact.
- Cite entre crochets les identifiants des sources utilisées ([K1], [W2]) \
et liste-les dans "sources".
- Si la mémoire suffit, réponds directement. Sinon, lance une recherche web précise.

À chaque tour, réponds par un seul objet JSON :
- {"action": "search", "query": "<requête web>"} pour chercher sur le web ;
- {"action": "answer", "answer": "<réponse>", "sources": ["K1", "W2"]} pour répondre."""

_ROLE_LABELS = {"user": "Utilisateur", "assistant": "Support"}

# Refs cited the way the prompt asks, in brackets: "[K1]", "[K1, W2]". The 4B sometimes
# escapes them as Markdown: "\[K1\]".
REF_GROUP = re.compile(r"\\?\[\s*[KW]\d+(?:\s*[,;]\s*[KW]\d+)*\s*\\?\]", re.IGNORECASE)
_STALE_REFS = re.compile(rf"\s*{REF_GROUP.pattern}", re.IGNORECASE)


class SearchAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["search"]
    query: str = Field(min_length=1)


class AnswerAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["answer"]
    answer: str = Field(min_length=1)
    sources: list[str] = []


_ACTION = TypeAdapter(Annotated[SearchAction | AnswerAction, Field(discriminator="action")])


def decision_schema(allow_search: bool) -> dict[str, object]:
    """JSON grammar for one step. Flat on purpose: TGI rejects a root `oneOf`, so the
    per-action requirements are checked by `parse_action` instead. `minLength` is
    enforced by the grammar: without it the 4B emits empty strings and repeats them
    despite the feedback.

    Without search (last step, or web search disabled) there is a single action, so the
    grammar itself requires `answer`: an output missing it would fail the request."""
    answer = {"type": "string", "minLength": 1}
    sources = {"type": "array", "items": {"type": "string"}}
    if not allow_search:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["answer"]},
                "answer": answer,
                "sources": sources,
            },
            "required": ["action", "answer"],
        }
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "answer"]},
            "query": {"type": "string", "minLength": 1},
            "answer": answer,
            "sources": sources,
        },
        "required": ["action"],
    }


def parse_action(content: str) -> SearchAction | AnswerAction:
    return _ACTION.validate_json(content)


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def render_memory(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return f"Mémoire :\n{NO_MEMORY}"
    blocks = [
        f"[K{index}] Q : {question}\nR : {_truncate(answer, MEMORY_ANSWER_MAX_CHARS)}"
        for index, (question, answer) in enumerate(entries, start=1)
    ]
    return "Mémoire :\n" + "\n\n".join(blocks)


def render_thread(messages: list[tuple[str, str]]) -> str:
    # The thread is text, not chat roles: GLPI threads can hold two consecutive user
    # messages, which the Gemma template rejects. Refs in earlier bot answers pointed to
    # that run's sources: left in, the model copies them onto this run's entries.
    lines = [f"{_ROLE_LABELS[role]} : {_STALE_REFS.sub('', content)}" for role, content in messages]
    text = "\n".join(lines)
    if len(text) > THREAD_MAX_CHARS:
        # The first message states the problem and the tail holds the current request:
        # the middle is cut.
        head = _truncate(lines[0], THREAD_HEAD_MAX_CHARS)
        text = f"{head}\n…" + text[-(THREAD_MAX_CHARS - len(head) - 2) :]
    return f"Fil :\n{text}"


def render_web_results(query: str, results: list[WebResult], first_ref: int) -> str:
    if not results:
        return f"Résultats web pour « {query} » : aucun résultat."
    blocks = [
        f"[W{ref}] {result.title} - {result.url}\n{result.excerpt}"
        for ref, result in enumerate(results, start=first_ref)
    ]
    return f"Résultats web pour « {query} » :\n" + "\n\n".join(blocks)


def invalid_output(error: str) -> str:
    return f"Sortie invalide : {error}. Réponds avec un seul objet JSON valide."


def initial_messages(memory_block: str, thread_block: str) -> list[ChatMessage]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{memory_block}\n\n{thread_block}"},
    ]
