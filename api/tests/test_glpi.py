import asyncio
import json

import httpx
import pytest

from app.services.glpi import (
    IMAGE_MAX_BYTES,
    Author,
    GlpiError,
    GlpiFollowup,
    GlpiImage,
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


# Documents by id (mime, content) and the documents each message links, as GLPI stores a
# file pasted inline or attached: linked to the ticket itself or to its followup.
DOCUMENTS = {
    1: ("image/png", b"png"),
    2: ("image/jpeg", b"x" * (IMAGE_MAX_BYTES + 1)),
    3: ("application/pdf", b"pdf"),
    4: ("image/webp", b"webp"),
    5: ("image/png", b"bot"),
}
DOCUMENT_LINKS = {
    "ITILFollowup/12": [{"id": 5, "documents_id": 3}],
    "ITILFollowup/11": [{"id": 6, "documents_id": 5}],
    "ITILFollowup/10": [{"id": 2, "documents_id": 1}, {"id": 4, "documents_id": 2}],
    "Ticket/42": [{"id": 1, "documents_id": 4}],
}
IMAGE_THREAD = GlpiTicket(
    id=42,
    title="Wifi",
    content="Mon wifi",
    followups=(
        GlpiFollowup(10, Author.REQUESTER, "Voici l'erreur"),
        GlpiFollowup(11, Author.BOT, "Supprimez le profil"),
        GlpiFollowup(12, Author.REQUESTER, "Le journal"),
    ),
)


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
        if path.endswith("/Document_Item"):
            return httpx.Response(200, json=DOCUMENT_LINKS[path.removesuffix("/Document_Item")])
        if path.startswith("Document/"):
            mime, content = DOCUMENTS[int(path.removeprefix("Document/"))]
            if request.headers.get("Accept") == "application/octet-stream":
                return httpx.Response(200, content=content)
            return httpx.Response(200, json={"mime": mime})
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


def test_mark_solved_sets_the_solved_status() -> None:
    server = FakeGlpiServer()
    _run(server, lambda glpi: glpi.mark_solved(42))
    put = next(r for r in server.requests if r.method == "PUT")
    assert put.url.path.endswith("/Ticket/42")
    assert json.loads(put.content) == {"input": {"status": 5}}


def test_mark_solved_of_a_missing_ticket_raises_glpi_error() -> None:
    with pytest.raises(GlpiError):
        _run(FakeGlpiServer(), lambda glpi: glpi.mark_solved(999))


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


def _document_requests(server: FakeGlpiServer) -> list[str]:
    paths = [r.url.path.removeprefix("/apirest.php/") for r in server.requests]
    return [path for path in paths if "Document" in path]


def test_get_images_reads_the_requester_messages_newest_first() -> None:
    server = FakeGlpiServer()

    images = _run(server, lambda glpi: glpi.get_images(IMAGE_THREAD, 2))

    # The PDF and the oversized JPEG are skipped; the bot's followup is never read.
    assert images == [GlpiImage("image/png", b"png"), GlpiImage("image/webp", b"webp")]
    assert "ITILFollowup/11/Document_Item" not in _document_requests(server)


def test_get_images_stops_at_the_limit() -> None:
    server = FakeGlpiServer()

    images = _run(server, lambda glpi: glpi.get_images(IMAGE_THREAD, 1))

    assert images == [GlpiImage("image/png", b"png")]
    assert "Ticket/42/Document_Item" not in _document_requests(server)


def test_get_images_with_no_limit_calls_nothing() -> None:
    server = FakeGlpiServer()

    assert _run(server, lambda glpi: glpi.get_images(IMAGE_THREAD, 0)) == []
    assert server.requests == []


def test_get_images_with_a_bad_token_raises_glpi_error() -> None:
    with pytest.raises(GlpiError):
        _run(FakeGlpiServer(), lambda glpi: glpi.get_images(IMAGE_THREAD, 1), user_token="wrong")
