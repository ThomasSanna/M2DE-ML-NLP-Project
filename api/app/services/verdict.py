"""The requester's verdict on the bot's latest answer, given with the GLPI plugin's buttons.

Only a technician closes a ticket: the verdict tells the technicians, in GLPI, whether the
answer solved the problem (the ticket is then set to Solved) or whether the requester wants
a technician (a handover: the bot never answers the ticket again).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FeedbackSource, RequesterVerdict, TicketAnswer
from app.services import feedback, growth
from app.services.embeddings import Embedder, EmbeddingError
from app.services.feedback import ReviewPolicy, TicketRating
from app.services.glpi import Glpi, GlpiError

logger = logging.getLogger(__name__)

_NOTES = {
    RequesterVerdict.RESOLVED: "Le demandeur indique que la réponse de l'assistant a résolu son"
    " problème : le ticket peut être clos.",
    RequesterVerdict.TECHNICIAN: "Le demandeur demande l'intervention d'un technicien :"
    " l'assistant ne répondra plus sur ce ticket.",
}
# Feedback on the entries the rated answer cited: "solved" is a success, asking for a
# technician says the answer did not help.
_SCORES = {RequesterVerdict.RESOLVED: 1.0, RequesterVerdict.TECHNICIAN: 0.0}


async def give_verdict(
    session: AsyncSession,
    glpi: Glpi,
    ticket_id: int,
    verdict: RequesterVerdict,
    review: ReviewPolicy | None = None,
    embedder: Embedder | None = None,
    dedup_similarity: float = 1.0,
) -> bool:
    """Record the verdict on the latest answer, tell the technicians in GLPI, then credit
    the entries that answer cited (feedback 1 for `resolved`, 0 for `technician`).

    False when no answer awaits a verdict: none was posted, the latest one is a handover, or
    it already has a verdict. On a GLPI failure the verdict is withdrawn, so the requester
    can click again.
    """
    latest = await session.scalar(
        select(TicketAnswer)
        .where(TicketAnswer.glpi_ticket_id == ticket_id)
        .order_by(TicketAnswer.created_at.desc(), TicketAnswer.id.desc())
        .limit(1)
        # Two clicks at once: the second waits, then finds the verdict set.
        .with_for_update()
    )
    if latest is None or latest.escalated or latest.verdict is not None:
        await session.commit()
        return False
    latest.verdict = verdict
    await session.commit()
    try:
        # The note first: the ticket is solved only once the technicians can read why.
        await glpi.add_followup(ticket_id, _NOTES[verdict], private=True)
        if verdict is RequesterVerdict.RESOLVED:
            await glpi.mark_solved(ticket_id)
    except GlpiError:
        latest.verdict = None
        await session.commit()
        raise
    # Only once GLPI took the verdict: a withdrawn one must leave no feedback behind.
    changed = await feedback.credit(
        session,
        FeedbackSource.VERDICT,
        [TicketRating(ticket_id, frozenset(latest.knowledge_ids), _SCORES[verdict])],
    )
    if review is not None:
        await feedback.flag_for_review(session, changed, review)
    if verdict is RequesterVerdict.RESOLVED and embedder is not None:
        latest.validated = True
    await session.commit()
    if latest.validated and embedder is not None:
        try:
            await growth.index_validated(session, embedder, dedup_similarity)
        except EmbeddingError as exc:
            # The answer stays validated: the survey sync indexes it on its next pass.
            logger.warning("Indexing the answer resolved on ticket %d failed: %s", ticket_id, exc)
    return True
