import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.services.llm import ChatMessage, Completion, LLMError, TGIChatClient

BASE_URL = "http://llm.test/v1"
MODEL = "google/gemma3-12b-it"
MESSAGES: list[ChatMessage] = [
    {"role": "system", "content": "Tu es l'assistant."},
    {"role": "user", "content": "Le wifi ne marche pas"},
]
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["answer"]}},
    "required": ["action"],
}
USAGE = {"prompt_tokens": 10, "completion_tokens": 3}


def _complete(handler: Callable[[httpx.Request], httpx.Response]) -> Completion:
    async def _run() -> Completion:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url=BASE_URL, transport=transport) as http:
            return await TGIChatClient(http, MODEL, 512).complete(MESSAGES, SCHEMA)

    return asyncio.run(_run())


def test_sends_grammar_and_greedy_decoding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/chat/completions"
        assert json.loads(request.content) == {
            "model": MODEL,
            "messages": MESSAGES,
            "max_tokens": 512,
            "temperature": 0,
            "response_format": {"type": "json", "value": SCHEMA},
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"action":"answer"}'}, "finish_reason": "stop"}
                ],
                "usage": USAGE,
            },
        )

    assert _complete(handler) == Completion('{"action":"answer"}', "stop", 10, 3)


def test_http_error_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "Input validation error"})

    with pytest.raises(LLMError):
        _complete(handler)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": None}, "finish_reason": "stop"}], "usage": USAGE},
    ],
)
def test_missing_content_raises_llm_error(body: dict[str, object]) -> None:
    with pytest.raises(LLMError):
        _complete(lambda request: httpx.Response(200, json=body))
