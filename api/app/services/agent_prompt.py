"""Everything the model reads: system prompt, rendered context, feedback messages and the
JSON action protocol. Model-facing text is French; the rest of the code is English."""

import re
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.services.llm import ChatMessage, ImagePart, TextPart
from app.services.websearch import WebResult

# Caps keep the prompt under 5000 tokens with 3 memory entries, a full thread and 2 searches.
# The context does not force them (the 12B server has 32768 tokens): they bound the cost and
# latency of each agent step. The thread cap is the loosest because cutting its middle loses
# the request's details (a pasted log, earlier attempts), while a memory entry is a summary.
MEMORY_ANSWER_MAX_CHARS = 800
THREAD_MAX_CHARS = 8000
THREAD_HEAD_MAX_CHARS = 600

NO_MEMORY = "Aucune connaissance pertinente en mémoire."
DUPLICATE_SEARCH = "Déjà cherché, résultats ci-dessus. Réponds ou cherche autre chose."
SEARCH_UNAVAILABLE = (
    "Recherche web indisponible : réponds avec la mémoire, pose une question à l'utilisateur"
    " ou, si rien ne t'y autorise, passe la main."
)
MAX_STEPS_NOTICE = (
    "Nombre maximal d'étapes atteint : réponds maintenant avec ce que tu as, ou pose une"
    " question à l'utilisateur. Ne passe la main que si la demande relève d'un technicien."
)
IMAGES_NOTICE = (
    "Images ci-dessus : jointes au ticket par l'utilisateur, de la plus ancienne à la plus"
    " récente. Sers-t'en pour comprendre la demande ; ce ne sont pas des sources."
)

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
2. Passe la main à un technicien seulement si la demande en relève : l'utilisateur demande \
à parler à une personne ; elle exige un accès que tu n'as pas (compte, mot de passe, \
messagerie, droits, dossier personnel, notes, données administratives ou RH) ou une \
intervention physique (poste, imprimante, salle, réseau) ; elle signale un incident de \
sécurité (compte piraté, hameçonnage) ; elle n'a rien à voir avec l'informatique ; ou \
l'utilisateur dit que ta solution n'a pas marché et il ne te reste rien d'autre à proposer.
3. Une entrée [K] ou un résultat [W] répond précisément au dernier message : réponds avec.
4. Il manque un détail sans lequel tu ne peux pas aider (système d'exploitation, message \
d'erreur exact, service ou logiciel concerné, ce qui a déjà été essayé) : pose-le à \
l'utilisateur, une ou deux questions précises dans un "answer" sans sources. Ne redemande \
pas ce que le fil contient déjà.
5. Sinon, cherche sur le web avec les mots-clés précis de la demande, par exemple \
« horaires bibliothèque Université de Corse » ou « opencode.ai installation ».

Une question informatique générale (logiciel, outil, service en ligne, message d'erreur, \
notion) n'a pas besoin d'être propre à l'université : réponds avec les résultats [W], \
même s'ils ne parlent pas de l'université. Seules les informations propres à l'université \
(procédure interne, contact, horaire, adresse web) doivent venir d'une entrée [K] ou d'un \
résultat [W] qui parle de l'université ; sans cela, ne les invente pas : pose une question \
ou passe la main.

Ta réponse est concise et concrète, dans la langue du dernier message de l'utilisateur : \
s'il écrit en anglais, réponds en anglais. Chaque information vient d'une entrée [K] \
ou d'un résultat [W] : cite-les entre crochets ([K1], [W2]) et liste-les dans "sources". \
Ne devine jamais une URL, une date, une procédure ni un contact.

Pour passer la main, écris à l'utilisateur, dans sa langue et en une ou deux phrases, \
que sa demande est transmise à un technicien, et résume dans "reason", en quelques mots, \
pourquoi tu passes la main : le support la lira, pas l'utilisateur. Ne passe pas la main \
parce qu'une recherche n'a rien donné de précis : reformule la recherche, pose une \
question, ou réponds avec ce que tu as trouvé en disant ce qui reste incertain.

À chaque tour, réponds par un seul objet JSON :
- {"action": "search", "query": "<requête web>"} pour chercher sur le web ;
- {"action": "answer", "answer": "<réponse ou question>", "sources": ["K1", "W2"]} pour \
répondre ou poser une question (sans sources dans ce cas) ;
- {"action": "escalate", "answer": "<message à l'utilisateur>", "reason": "<raison>"} \
pour passer la main à un technicien."""

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


class EscalateAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["escalate"]
    # The message to the requester.
    answer: str = Field(min_length=1)
    # For the support only. Optional: the grammar cannot require it for this action alone.
    reason: str = ""


_ACTION = TypeAdapter(
    Annotated[SearchAction | AnswerAction | EscalateAction, Field(discriminator="action")]
)


def decision_schema(allow_search: bool, refs: list[str]) -> dict[str, object]:
    """JSON grammar for one step. Flat on purpose: TGI rejects a root `oneOf`, so the
    per-action requirements are checked by `parse_action` instead. `minLength` is
    enforced by the grammar: without it the 4B emits empty strings and repeats them
    despite the feedback.

    Without search (last step, or web search disabled) both remaining actions carry
    `answer`, so the grammar itself requires it: an output missing it would fail the
    request.

    `sources` only takes refs shown so far: with free strings the 4B stopped inside an
    item (`["W2","]`)."""
    actions = ["search", "answer", "escalate"] if allow_search else ["answer", "escalate"]
    properties: dict[str, object] = {"action": {"type": "string", "enum": actions}}
    if allow_search:
        properties["query"] = {"type": "string", "minLength": 1}
    properties["answer"] = {"type": "string", "minLength": 1}
    properties["reason"] = {"type": "string"}
    if refs:
        properties["sources"] = {"type": "array", "items": {"type": "string", "enum": refs}}
    return {
        "type": "object",
        "properties": properties,
        "required": ["action"] if allow_search else ["action", "answer"],
    }


def parse_action(content: str) -> SearchAction | AnswerAction | EscalateAction:
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


def initial_messages(
    memory_block: str, thread_block: str, images: Sequence[str] = ()
) -> list[ChatMessage]:
    """The system prompt and the first user message. `images` are `data:` URIs, oldest
    first, placed before the text as in Gemma 4's examples."""
    text = f"{memory_block}\n\n{thread_block}"
    if not images:
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]
    parts: list[TextPart | ImagePart] = [
        {"type": "image_url", "image_url": {"url": url}} for url in images
    ]
    parts.append({"type": "text", "text": f"{IMAGES_NOTICE}\n\n{text}"})
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": parts}]
