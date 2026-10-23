import html
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx


class GlpiError(RuntimeError):
    """The GLPI REST API is unreachable or rejected the request."""


class Author(StrEnum):
    REQUESTER = "requester"
    BOT = "bot"
    OTHER = "other"  # a technician, or any user who is neither a requester nor the bot


@dataclass(frozen=True)
class GlpiFollowup:
    id: int
    author: Author
    content: str  # plain text


@dataclass(frozen=True)
class GlpiTicket:
    id: int
    title: str
    content: str  # plain text (GLPI's HTML is stripped)
    # Public followups with text, oldest first: the requester never sees the private ones,
    # so the bot must not answer from them.
    followups: tuple[GlpiFollowup, ...] = ()


class Glpi(Protocol):
    async def get_ticket(self, ticket_id: int) -> GlpiTicket | None: ...

    async def add_followup(self, ticket_id: int, text: str, *, private: bool) -> int: ...


# Ticket_User.type of a requester (2 is an assigned technician, 3 an observer).
_REQUESTER = 1
# The latest 49: with the ticket itself, the 50 messages an answer thread can hold.
_FOLLOWUPS = {"sort": "id", "order": "DESC", "range": "0-48"}

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
    async def _session(self) -> AsyncIterator[tuple[dict[str, str], int]]:
        """The session headers and the bot's user id (from the full session, about 5 kB)."""
        try:
            response = await self._http.get(
                "initSession",
                headers=self._auth | self._app,
                params={"get_full_session": "true"},
            )
            response.raise_for_status()
            data = response.json()
            headers = {"Session-Token": data["session_token"]} | self._app
            user_id = int(data["session"]["glpiID"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise GlpiError(f"Cannot open a GLPI session: {exc!r}") from exc
        try:
            yield headers, user_id
        finally:
            # On failure, the session expires on its own.
            with suppress(httpx.HTTPError):
                await self._http.get("killSession", headers=headers)

    async def get_ticket(self, ticket_id: int) -> GlpiTicket | None:
        async with self._session() as (headers, bot_id):
            try:
                response = await self._http.get(f"Ticket/{ticket_id}", headers=headers)
                if response.status_code == httpx.codes.NOT_FOUND:
                    return None
                response.raise_for_status()
                data = response.json()
                users = await self._get_list(f"Ticket/{ticket_id}/Ticket_User", headers)
                followups = await self._get_list(
                    f"Ticket/{ticket_id}/ITILFollowup", headers, _FOLLOWUPS
                )
                requesters = {int(u["users_id"]) for u in users if u["type"] == _REQUESTER}
                return GlpiTicket(
                    id=int(data["id"]),
                    title=html_to_text(data.get("name") or ""),
                    content=html_to_text(data.get("content") or ""),
                    followups=tuple(
                        GlpiFollowup(int(f["id"]), _author(f, requesters, bot_id), text)
                        for f in reversed(followups)
                        if not f["is_private"] and (text := html_to_text(f["content"] or ""))
                    ),
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise GlpiError(f"Cannot read ticket {ticket_id}: {exc!r}") from exc

    async def _get_list(
        self, path: str, headers: dict[str, str], params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        response = await self._http.get(path, headers=headers, params=params)
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            raise ValueError(f"{path}: a list was expected")
        return items

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
        async with self._session() as (headers, _):
            try:
                response = await self._http.post("ITILFollowup", headers=headers, json=payload)
                response.raise_for_status()
                return int(response.json()["id"])
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise GlpiError(f"Cannot add a followup to ticket {ticket_id}: {exc!r}") from exc


def _author(followup: dict[str, Any], requesters: set[int], bot_id: int) -> Author:
    user_id = int(followup["users_id"])
    if user_id == bot_id:
        return Author.BOT
    return Author.REQUESTER if user_id in requesters else Author.OTHER
