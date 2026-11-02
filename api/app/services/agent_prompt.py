"""Everything the model reads: system prompt, rendered context, feedback messages and the
JSON action protocol. Model-facing text is French; the rest of the code is English."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.services.llm import ChatMessage
from app.services.websearch import WebResult

# Caps keep the prompt near 3000 tokens with 3 memory entries and 2 searches, under the
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
Tu réponds au dernier message de l'utilisateur, en tenant compte du reste du fil.

Ce que tu reçois :
- Mémoire : d'anciennes demandes résolues, souvent sans rapport avec le fil. \
Une entrée [K] ne sert que si elle traite du même problème.
- Fil : les messages de l'utilisateur et du support. Les messages « Support » sont des \
réponses déjà données : ce ne sont pas des sources, ne les répète pas.
- Résultats web [W] : ajoutés après chaque recherche.

Applique la première règle qui correspond :
1. L'utilisateur remercie ou dit que c'est résolu : réponds en une phrase.
2. Une entrée [K] ou un résultat [W] répond précisément au dernier message : réponds avec.
3. Sinon, cherche sur le web avec les mots-clés précis de la demande, par exemple \
« horaires bibliothèque Université de Corse ».

Ta réponse est concise et concrète, dans la langue du dernier message de l'utilisateur : \
s'il écrit en anglais, réponds en anglais. Chaque information vient d'une entrée [K] \
ou d'un résultat [W] : cite-les entre crochets ([K1], [W2]) et liste-les dans "sources". \
Si aucune source ne répond à la demande, dis-le clairement : ne devine jamais une URL, \
une date, une procédure ni un contact.

À chaque tour, réponds par un seul objet JSON :
- {"action": "search", "query": "<requête web>"} pour chercher sur le web ;
- {"action": "answer", "answer": "<réponse>", "sources": ["K1", "W2"]} pour répondre."""

_ROLE_LABELS = {"user": "Utilisateur", "assistant": "Support"}

# Refs cited the way the prompt asks, in brackets: "[K1]", "[K1, W2]". The 4B sometimes
# escapes them as Markdown: "\[K1\]".
REF_GROUP = re.compile(r"\\?\[\s*[KW]\d+(?:\s*[,;]\s*[KW]\d+)*\s*\\?\]", re.IGNORECASE)
# With the space before the group, so "profil [K1]." becomes "profil.".
_SPACED_REF_GROUP = re.compile(rf"\s*{REF_GROUP.pattern}", re.IGNORECASE)


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


def decision_schema(allow_search: bool, refs: list[str]) -> dict[str, object]:
    """JSON grammar for one step. Flat on purpose: TGI rejects a root `oneOf`, so the
    per-action requirements are checked by `parse_action` instead. `minLength` is
    enforced by the grammar: without it the 4B emits empty strings and repeats them
    despite the feedback.

    Without search (last step, or web search disabled) there is a single action, so the
    grammar itself requires `answer`: an output missing it would fail the request.

    `sources` only takes refs shown so far: with free strings the 4B stopped inside an
    item (`["W2","]`)."""
    properties: dict[str, object] = {
        "action": {"type": "string", "enum": ["search", "answer"] if allow_search else ["answer"]}
    }
    if allow_search:
        properties["query"] = {"type": "string", "minLength": 1}
    properties["answer"] = {"type": "string", "minLength": 1}
    if refs:
        properties["sources"] = {"type": "array", "items": {"type": "string", "enum": refs}}
    return {
        "type": "object",
        "properties": properties,
        "required": ["action"] if allow_search else ["action", "answer"],
    }


def parse_action(content: str) -> SearchAction | AnswerAction:
    return _ACTION.validate_json(content)


def strip_refs(text: str) -> str:
    return _SPACED_REF_GROUP.sub("", text).strip()


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
    lines = [f"{_ROLE_LABELS[role]} : {strip_refs(content)}" for role, content in messages]
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
