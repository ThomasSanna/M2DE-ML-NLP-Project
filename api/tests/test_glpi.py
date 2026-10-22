import asyncio
import json

import httpx
import pytest

from app.services.glpi import GlpiError, GlpiRestClient, GlpiTicket, html_to_text, text_to_html

BASE_URL = "http://glpi.test/apirest.php/"


class FakeGlpiServer:
    """Imite l'API REST v1 de GLPI et enregistre les requêtes reçues."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/apirest.php/")
        if path == "initSession":
            if request.headers.get("Authorization") != "user_token bot-token":
                return httpx.Response(401, json=["ERROR_LOGIN_PARAMETERS_MISSING", ""])
            return httpx.Response(200, json={"session_token": "sess"})
        assert request.headers["Session-Token"] == "sess"
        assert request.headers["App-Token"] == "app-token"
        if path == "killSession":
            return httpx.Response(200, json=[])
        if path == "Ticket/42":
            content = "&lt;p&gt;Mon wifi&lt;/p&gt;&lt;p&gt;ne marche &amp;amp; plus&lt;/p&gt;"
            return httpx.Response(200, json={"id": 42, "name": "Wifi", "content": content})
        if path.startswith("Ticket/"):
            return httpx.Response(404, json=["ERROR_ITEM_NOT_FOUND", ""])
        if path == "ITILFollowup":
            return httpx.Response(201, json={"id": 7, "message": ""})
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
        "killSession",
    ]


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
        _run(FakeGlpiServer(), lambda glpi: glpi.get_ticket(42), user_token="faux")


def test_html_round_trip() -> None:
    assert html_to_text("<p>a<br>b</p><p>c</p>") == "a\nb\nc"
    assert text_to_html("a\n\nb") == "<p>a</p><p>b</p>"
