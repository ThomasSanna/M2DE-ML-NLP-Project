import asyncio
import json

import httpx
import pytest

from app.services.llm import LLMError, OpenAIChatClient

BASE_URL = "http://llm.test/v1"
MODEL = "google/gemma3-12b-it"


def _chunk(content: str | None) -> str:
    delta = {} if content is None else {"content": content}
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]})


def _stream(handler) -> list[str]:
    async def _run() -> list[str]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url=BASE_URL, transport=transport) as http:
            client = OpenAIChatClient(http, MODEL)
            return [token async for token in client.stream([{"role": "user", "content": "q"}])]

    return asyncio.run(_run())


def test_yields_deltas_until_done() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["stream"] is True
        lines = [_chunk(None), _chunk("Bon"), "", ": keep-alive", _chunk("jour"), "data: [DONE]"]
        lines.append(_chunk("après la fin"))
        return httpx.Response(200, text="\n".join(lines) + "\n")

    assert _stream(handler) == ["Bon", "jour"]


def test_http_error_raises_llm_error() -> None:
    with pytest.raises(LLMError):
        _stream(lambda request: httpx.Response(503))


def test_malformed_chunk_raises_llm_error() -> None:
    with pytest.raises(LLMError):
        _stream(lambda request: httpx.Response(200, text="data: {pas du json}\n"))
