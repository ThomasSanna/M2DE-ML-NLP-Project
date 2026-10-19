import asyncio
import json

import httpx
import pytest

from app.models import EMBEDDING_DIM
from app.services.embeddings import EmbeddingError, OpenAIEmbeddingClient

BASE_URL = "http://embeddings.test/v1"


MODEL = "BAAI/bge-multilingual-gemma2"


def _embed(handler, texts: list[str], instruction: str | None = None) -> list[list[float]]:
    async def _run() -> list[list[float]]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url=BASE_URL, transport=transport) as http:
            return await OpenAIEmbeddingClient(http, MODEL, instruction).embed(texts)

    return asyncio.run(_run())


def test_calls_openai_endpoint_and_orders_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/embeddings"
        assert json.loads(request.content) == {"model": MODEL, "input": ["a", "b"]}
        # Réponse volontairement dans le désordre.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [1.0] * EMBEDDING_DIM},
                    {"index": 0, "embedding": [0.0] * EMBEDDING_DIM},
                ]
            },
        )

    vectors = _embed(handler, ["a", "b"])
    assert vectors[0][0] == 0.0
    assert vectors[1][0] == 1.0


def test_instruction_is_prefixed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["input"] == ["<instruct>Trouver\n<query>wifi"]
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.0] * EMBEDDING_DIM}]}
        )

    _embed(handler, ["wifi"], instruction="Trouver")


def test_http_error_raises_embedding_error() -> None:
    with pytest.raises(EmbeddingError):
        _embed(lambda request: httpx.Response(503), ["a"])


def test_wrong_dimension_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 768}]})

    with pytest.raises(EmbeddingError, match="Dimension"):
        _embed(handler, ["a"])
