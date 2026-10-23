import html
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Protocol

import httpx


class GlpiError(RuntimeError):
    """The GLPI REST API is unreachable or rejected the request."""


@dataclass(frozen=True)
class GlpiTicket:
    id: int
    title: str
    content: str  # plain text (GLPI's HTML is stripped)


class Glpi(Protocol):
    async def get_ticket(self, ticket_id: int) -> GlpiTicket | None: ...

    async def add_followup(self, ticket_id: int, text: str, *, private: bool) -> int: ...


_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6])>|<br\s*/?>", re.IGNORECASE)


def html_to_text(value: str) -> str:
    """GLPI rich content to text. GLPI may return escaped HTML (`&lt;p&gt;`)."""
    value = html.unescape(value)
    value = _BLOCK_END.sub("\n", value)
    value = html.unescape(_TAG.sub("", value))
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def text_to_html(value: str) -> str:
    paragraphs = [p for p in re.split(r"\n\s*\n", value.strip()) if p.strip()]
    return "".join(
        "<p>" + html.escape(p.strip()).replace("\n", "<br>") + "</p>" for p in paragraphs
    )


class GlpiRestClient:
    """Client of the GLPI REST API v1 (`/apirest.php`), authenticated with a User-Token.

    One GLPI session per operation (`initSession` ... `killSession`): a few more requests,
    but no session token to keep in memory or renew.
    """

    def __init__(self, http: httpx.AsyncClient, user_token: str, app_token: str | None) -> None:
        self._http = http
        self._auth = {"Authorization": f"user_token {user_token}"}
        self._app = {"App-Token": app_token} if app_token else {}

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[dict[str, str]]:
        try:
            response = await self._http.get("initSession", headers=self._auth | self._app)
            response.raise_for_status()
            headers = {"Session-Token": response.json()["session_token"]} | self._app
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise GlpiError(f"Cannot open a GLPI session: {exc!r}") from exc
        try:
            yield headers
        finally:
            # On failure, the session expires on its own.
            with suppress(httpx.HTTPError):
                await self._http.get("killSession", headers=headers)

    async def get_ticket(self, ticket_id: int) -> GlpiTicket | None:
        async with self._session() as headers:
            try:
                response = await self._http.get(f"Ticket/{ticket_id}", headers=headers)
                if response.status_code == httpx.codes.NOT_FOUND:
                    return None
                response.raise_for_status()
                data = response.json()
                return GlpiTicket(
                    id=int(data["id"]),
                    title=html_to_text(data.get("name") or ""),
                    content=html_to_text(data.get("content") or ""),
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise GlpiError(f"Cannot read ticket {ticket_id}: {exc!r}") from exc

    async def add_followup(self, ticket_id: int, text: str, *, private: bool) -> int:
        """Add a followup to the ticket. Public: GLPI notifies the requester by email."""
        payload = {
            "input": {
                "itemtype": "Ticket",
                "items_id": ticket_id,
                "content": text_to_html(text),
                "is_private": int(private),
            }
        }
        async with self._session() as headers:
            try:
                response = await self._http.post("ITILFollowup", headers=headers, json=payload)
                response.raise_for_status()
                return int(response.json()["id"])
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise GlpiError(f"Cannot add a followup to ticket {ticket_id}: {exc!r}") from exc
