import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.services.websearch import EXA_BASE_URL, ExaClient, WebResult, WebSearchError


def _search(
    handler: Callable[[httpx.Request], httpx.Response],
    query: str = "eduroam mac",
    include_domains: list[str] | None = None,
) -> list[WebResult]:
    async def _run() -> list[WebResult]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url=EXA_BASE_URL, transport=transport) as http:
            return await ExaClient(http, 3, include_domains or []).search(query)

    return asyncio.run(_run())


def test_posts_search_with_highlights() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{EXA_BASE_URL}/search"
        assert json.loads(request.content) == {
            "query": "eduroam mac",
            "type": "auto",
            "numResults": 3,
            "contents": {"highlights": {"maxCharacters": 500}},
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Eduroam macOS",
                        "url": "https://a.fr",
                        "publishedDate": "2025-01-02",
                        "highlights": ["un", "deux"],
                    }
                ],
                "costDollars": {"total": 0.005},
            },
        )

    assert _search(handler) == [
        WebResult("Eduroam macOS", "https://a.fr", "2025-01-02", "un … deux")
    ]


def test_include_domains_is_sent_when_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["includeDomains"] == ["univ-corse.fr"]
        return httpx.Response(200, json={"results": []})

    assert _search(handler, include_domains=["univ-corse.fr"]) == []


def test_missing_title_falls_back_to_url_and_excerpt_is_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"title": None, "url": "https://b.fr", "highlights": ["x" * 800]}]},
        )

    [result] = _search(handler)
    assert result.title == "https://b.fr"
    assert result.published is None
    assert len(result.excerpt) == 500


@pytest.mark.parametrize(
    "response",
    [httpx.Response(401), httpx.Response(429), httpx.Response(200, json={"unexpected": []})],
)
def test_errors_raise_web_search_error(response: httpx.Response) -> None:
    with pytest.raises(WebSearchError):
        _search(lambda request: response)


@pytest.mark.parametrize(
    "body",
    [
        {"results": [{"title": "t", "url": None}]},
        {"results": [{"title": 123, "url": "https://a.fr"}]},
        {"results": [{"title": "t", "url": "https://a.fr", "publishedDate": 2025}]},
        {"results": [{"title": "t", "url": "https://a.fr", "highlights": "abc"}]},
        {"results": [{"title": "t", "url": "https://a.fr", "highlights": [1, 2]}]},
    ],
)
def test_unexpected_exa_fields_raise_web_search_error(body: dict[str, object]) -> None:
    with pytest.raises(WebSearchError):
        _search(lambda _: httpx.Response(200, json=body))


@pytest.mark.parametrize("cost", [0.005, "0.005", [1], None])
def test_unexpected_cost_keeps_the_results(cost: object) -> None:
    body = {"results": [{"title": "t", "url": "https://a.fr"}], "costDollars": cost}

    assert _search(lambda _: httpx.Response(200, json=body)) == [
        WebResult("t", "https://a.fr", None, "")
    ]
