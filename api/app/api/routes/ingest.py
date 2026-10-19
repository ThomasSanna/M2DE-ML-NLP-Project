import json
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import SessionDep, SettingsDep
from app.core.security import verify_glpi_signature
from app.models import GlpiEvent

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestAccepted(BaseModel):
    event_id: int


@router.post("/ticket", status_code=status.HTTP_202_ACCEPTED)
async def ingest_ticket(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    x_glpi_signature: Annotated[str | None, Header()] = None,
    x_glpi_timestamp: Annotated[str | None, Header()] = None,
) -> IngestAccepted:
    """Reçoit un webhook GLPI et le met en file pour la pipeline.

    Le corps est stocké tel quel : la pipeline relira le ticket via l'API GLPI.
    """
    body = await request.body()

    if settings.glpi_webhook_secret is not None and not (
        x_glpi_signature
        and x_glpi_timestamp
        and verify_glpi_signature(
            body,
            x_glpi_timestamp,
            x_glpi_signature,
            settings.glpi_webhook_secret.get_secret_value(),
        )
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signature GLPI invalide")

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Corps JSON invalide") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Objet JSON attendu")

    event = payload.get("event")
    glpi_event = GlpiEvent(event=event if isinstance(event, str) else None, payload=payload)
    session.add(glpi_event)
    await session.commit()
    return IngestAccepted(event_id=glpi_event.id)
