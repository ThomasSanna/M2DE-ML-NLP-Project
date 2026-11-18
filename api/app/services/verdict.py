"""The requester's verdict on the bot's latest answer, given with the GLPI plugin's buttons.

Only a technician closes a ticket: the verdict tells the technicians, in GLPI, whether the
answer solved the problem (the ticket is then set to Solved) or whether the requester wants
a technician (a handover: the bot never answers the ticket again).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RequesterVerdict, TicketAnswer
from app.services.glpi import Glpi, GlpiError

_NOTES = {
    RequesterVerdict.RESOLVED: "Le demandeur indique que la réponse de l'assistant a résolu son"
    " problème : le ticket peut être clos.",
    RequesterVerdict.TECHNICIAN: "Le demandeur demande l'intervention d'un technicien :"
    " l'assistant ne répondra plus sur ce ticket.",
}


async def give_verdict(
    session: AsyncSession, glpi: Glpi, ticket_id: int, verdict: RequesterVerdict
) -> bool:
    """Record the verdict on the latest answer and tell the technicians in GLPI.

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
    return True
