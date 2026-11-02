import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.services.reranker import HttpRerankerClient, RerankerError

BASE_URL = "http://reranker.test"


def _rerank(
    handler: Callable[[httpx.Request], httpx.Response], query: str, documents: list[str]
) -> list[float]:
    async def _run() -> list[float]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url=BASE_URL, transport=transport) as http:
            return await HttpRerankerClient(http).rerank(query, documents)

    return asyncio.run(_run())


def test_posts_pairs_and_returns_scores() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/rerank"
        assert json.loads(request.content) == {"pairs": [["wifi", "a"], ["wifi", "b"]]}
        return httpx.Response(200, json={"scores": [2.5, -7.0]})

    assert _rerank(handler, "wifi", ["a", "b"]) == [2.5, -7.0]


def test_score_count_mismatch_raises() -> None:
    with pytest.raises(RerankerError):
        _rerank(lambda request: httpx.Response(200, json={"scores": [1.0]}), "wifi", ["a", "b"])


def test_http_error_raises_reranker_error() -> None:
    with pytest.raises(RerankerError):
        _rerank(lambda request: httpx.Response(500), "wifi", ["a"])


def test_no_documents_skips_the_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    assert _rerank(handler, "wifi", []) == []
