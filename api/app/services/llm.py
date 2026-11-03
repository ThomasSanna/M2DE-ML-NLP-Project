from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

import httpx


class LLMError(RuntimeError):
    """The language model server is unreachable or returned an unexpected response."""


# Required by the OpenAI `json_schema` format; a short fixed name, only informative for vLLM.
RESPONSE_FORMAT_NAME = "agent_step"


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Completion:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class ChatModel(Protocol):
    async def complete(
        self, messages: list[ChatMessage], schema: dict[str, object]
    ) -> Completion: ...


class VLLMChatClient:
    """Client for the university LLM, served by vLLM (`POST /v1/chat/completions`).

    The output is constrained by a JSON grammar through the OpenAI `response_format`
    `{"type": "json_schema", ...}`. TGI's own `{"type": "json", "value": schema}` is rejected
    by vLLM with a 400, and TGI rejects the OpenAI form: this client no longer talks to TGI.
    The server checks the `model` field and answers 404 for a name it does not serve.
    """

    def __init__(self, http: httpx.AsyncClient, model: str, max_tokens: int) -> None:
        self._http = http
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, messages: list[ChatMessage], schema: dict[str, object]) -> Completion:
        try:
            response = await self._http.post(
                "chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    "max_tokens": self._max_tokens,
                    "temperature": 0,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": RESPONSE_FORMAT_NAME, "schema": schema},
                    },
                },
            )
            response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            completion = Completion(
                content=content,
                finish_reason=choice["finish_reason"],
                prompt_tokens=body["usage"]["prompt_tokens"],
                completion_tokens=body["usage"]["completion_tokens"],
            )
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Language model call failed: {exc!r}") from exc

        if not isinstance(content, str):
            raise LLMError("Language model returned no text content")
        return completion
