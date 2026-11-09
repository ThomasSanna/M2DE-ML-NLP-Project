import asyncio
import json

import httpx
import pytest

from app.services.glpi import (
    Author,
    GlpiError,
    GlpiFollowup,
    GlpiRestClient,
    GlpiSurvey,
    GlpiTicket,
    html_to_text,
    text_to_html,
)

BASE_URL = "http://glpi.test/apirest.php/"
BOT_ID = 9
# User 4 asked for help; user 5 is the technician assigned to the ticket.
TICKET_USERS = [{"users_id": 4, "type": 1}, {"users_id": 5, "type": 2}]
# As GLPI 11 returns them: a survey is created at closing, answered later (or never).
SURVEYS = [
    {
        "id": 4,
        "tickets_id": 45,
        "date_answered": None,
        "satisfaction": None,
        "satisfaction_scaled_to_5": None,
        "comment": None,
    },
    {
        "id": 3,
        "tickets_id": 44,
        "date_answered": "2026-11-09 11:40:44",
        "satisfaction": 0,
        "satisfaction_scaled_to_5": 0,
        "comment": "  ",
    },
    {
        "id": 2,
        "tickets_id": 43,
        "date_answered": "2026-11-08 09:00:00",
        "satisfaction": 4,
        "satisfaction_scaled_to_5": 4,
        "comment": " Clair ",
    },
]


class FakeGlpiServer:
    """Mimics the GLPI REST API v1 and records the requests it receives."""

    def __init__(self, followups: list[dict[str, object]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._followups = followups or []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/apirest.php/")
        if path == "initSession":
            if request.headers.get("Authorization") != "user_token bot-token":
                return httpx.Response(401, json=["ERROR_LOGIN_PARAMETERS_MISSING", ""])
            session = {"glpiID": BOT_ID}
            return httpx.Response(200, json={"session_token": "sess", "session": session})
        assert request.headers["Session-Token"] == "sess"
        assert request.headers["App-Token"] == "app-token"
        if path == "killSession":
            return httpx.Response(200, json=[])
        if path == "Ticket/42/Ticket_User":
            return httpx.Response(200, json=TICKET_USERS)
        if path == "Ticket/42/ITILFollowup":
            return httpx.Response(200, json=self._followups)
        if path == "Ticket/42":
            content = "&lt;p&gt;Mon wifi&lt;/p&gt;&lt;p&gt;ne marche &amp;amp; plus&lt;/p&gt;"
            return httpx.Response(200, json={"id": 42, "name": "Wifi", "content": content})
        if path.startswith("Ticket/"):
            return httpx.Response(404, json=["ERROR_ITEM_NOT_FOUND", ""])
        if path == "ITILFollowup":
            return httpx.Response(201, json={"id": 7, "message": ""})
        if path == "TicketSatisfaction":
            return httpx.Response(200, json=SURVEYS)
        return httpx.Response(400)


def _run(server, coro_factory, user_token: str = "bot-token"):
    async def _inner():
        async with httpx.AsyncClient(
            base_url=BASE_URL, transport=httpx.MockTransport(server)
        ) as http:
            return await coro_factory(GlpiRestClient(http, user_token, "app-token"))

    return asyncio.run(_inner())


def test_get_ticket_converts_html_and_closes_session() -> None:
    server = FakeGlpiServer()
    ticket = _run(server, lambda glpi: glpi.get_ticket(42))
    assert ticket == GlpiTicket(id=42, title="Wifi", content="Mon wifi\nne marche & plus")
    assert [r.url.path.rsplit("/", 1)[-1] for r in server.requests] == [
        "initSession",
        "42",
        "Ticket_User",
        "ITILFollowup",
        "killSession",
    ]


def test_get_ticket_keeps_public_followups_with_text_oldest_first() -> None:
    # Newest first: the client asks GLPI for the latest followups.
    server = FakeGlpiServer(
        [
            {"id": 14, "users_id": 6, "is_private": 0, "content": "&lt;p&gt;Je regarde&lt;/p&gt;"},
            {"id": 13, "users_id": 5, "is_private": 0, "content": "Je prends la main"},
            {"id": 12, "users_id": 5, "is_private": 1, "content": "Note interne"},
            {"id": 11, "users_id": 4, "is_private": 0, "content": "<p><img src='x'></p>"},
            {"id": 10, "users_id": BOT_ID, "is_private": 0, "content": "Réinstallez le profil"},
            {"id": 9, "users_id": 4, "is_private": 0, "content": "Toujours pas"},
        ]
    )

    ticket = _run(server, lambda glpi: glpi.get_ticket(42))

    assert ticket.followups == (
        GlpiFollowup(9, Author.REQUESTER, "Toujours pas"),
        GlpiFollowup(10, Author.BOT, "Réinstallez le profil"),
        GlpiFollowup(13, Author.OTHER, "Je prends la main"),
        GlpiFollowup(14, Author.OTHER, "Je regarde"),
    )
    request = next(r for r in server.requests if r.url.path.endswith("ITILFollowup"))
    assert dict(request.url.params) == {"sort": "id", "order": "DESC", "range": "0-48"}


def test_missing_ticket_is_none() -> None:
    assert _run(FakeGlpiServer(), lambda glpi: glpi.get_ticket(999)) is None


def test_add_followup_posts_html() -> None:
    server = FakeGlpiServer()
    followup_id = _run(server, lambda glpi: glpi.add_followup(42, "Ligne 1\nA < B", private=False))
    assert followup_id == 7
    post = next(r for r in server.requests if r.method == "POST")
    assert json.loads(post.content) == {
        "input": {
            "itemtype": "Ticket",
            "items_id": 42,
            "content": "<p>Ligne 1<br>A &lt; B</p>",
            "is_private": 0,
        }
    }


def test_bad_token_raises_glpi_error() -> None:
    with pytest.raises(GlpiError):
        _run(FakeGlpiServer(), lambda glpi: glpi.get_ticket(42), user_token="wrong")


def test_html_round_trip() -> None:
    assert html_to_text("<p>a<br>b</p><p>c</p>") == "a\nb\nc"
    assert text_to_html("a\n\nb") == "<p>a</p><p>b</p>"


def test_list_answered_surveys_skips_unanswered_ones() -> None:
    server = FakeGlpiServer()

    surveys = _run(server, lambda glpi: glpi.list_answered_surveys())

    assert surveys == [GlpiSurvey(44, 0.0, None), GlpiSurvey(43, 0.8, "Clair")]
    request = next(r for r in server.requests if r.url.path.endswith("TicketSatisfaction"))
    assert dict(request.url.params) == {"sort": "id", "order": "DESC", "range": "0-999"}


def test_list_surveys_with_a_bad_token_raises_glpi_error() -> None:
    with pytest.raises(GlpiError):
        _run(FakeGlpiServer(), lambda glpi: glpi.list_answered_surveys(), user_token="wrong")
