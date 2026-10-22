from collections.abc import AsyncIterator

from app.models import Knowledge
from app.services.glpi import GlpiTicket

SYSTEM_PROMPT = (
    "Tu es l'assistant du support informatique de l'université. Réponds en français, "
    "simplement et correctement, à la demande de l'utilisateur. Appuie-toi sur les réponses "
    "déjà validées fournies si elles s'appliquent ; sinon, dis honnêtement que la demande "
    "est transmise à un technicien. N'invente ni procédure, ni lien, ni contact."
)


def build_messages(ticket: GlpiTicket, hits: list[tuple[Knowledge, float]]) -> list[dict[str, str]]:
    """Prompt : demande du ticket + connaissances les plus proches de la mémoire."""
    if hits:
        memory = "\n\n".join(
            f"[{i}] Question : {knowledge.question}\nRéponse : {knowledge.answer}"
            for i, (knowledge, _) in enumerate(hits, start=1)
        )
    else:
        memory = "(aucune réponse similaire en mémoire)"
    user = (
        f"Réponses déjà validées :\n{memory}\n\n"
        f"Demande de l'utilisateur :\nTitre : {ticket.title}\n{ticket.content}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


async def chunk_tokens(tokens: AsyncIterator[str], size: int) -> AsyncIterator[str]:
    """Regroupe les tokens par paquets de `size` : moins d'événements SSE, affichage fluide."""
    buffer: list[str] = []
    async for token in tokens:
        buffer.append(token)
        if len(buffer) >= size:
            yield "".join(buffer)
            buffer.clear()
    if buffer:
        yield "".join(buffer)
