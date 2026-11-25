from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

import httpx


class LLMError(RuntimeError):
    """The language model server is unreachable or returned an unexpected response."""


# Required by the OpenAI `json_schema` format; a short fixed name, only informative for vLLM.
RESPONSE_FORMAT_NAME = "agent_step"


class TextPart(TypedDict):
    type: Literal["text"]
    text: str


class ImageUrl(TypedDict):
    url: str  # a `data:` URI: the model server cannot fetch from GLPI


class ImagePart(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrl


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    # A list of parts only for a message carrying images.
    content: str | list[TextPart | ImagePart]


@dataclass(frozen=True)
class Completion:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    # Prompt tokens served from vLLM's prefix cache; None when the server does not report it.
    cached_tokens: int | None = None


class ChatModel(Protocol):
    async def complete(
        self, messages: list[ChatMessage], schema: dict[str, object]
    ) -> Completion: ...


class VLLMChatClient:
    """Client for the university LLM, served by vLLM (`POST /v1/chat/completions`).

    The output is constrained by a JSON grammar through the OpenAI `response_format`
    `{"type": "json_schema", ...}`. TGI's own `{"type": "json", "value": schema}` is rejected
    by vLLM with a 400, and TGI rejects the OpenAI form with a 422: use `TGIChatClient` there.
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
                    "response_format": self._response_format(schema),
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
                cached_tokens=_cached_tokens(body["usage"]),
            )
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Language model call failed: {exc!r}") from exc

        if not isinstance(content, str):
            raise LLMError("Language model returned no text content")
        return completion

    def _response_format(self, schema: dict[str, object]) -> dict[str, object]:
        return {
            "type": "json_schema",
            "json_schema": {"name": RESPONSE_FORMAT_NAME, "schema": schema},
        }


class TGIChatClient(VLLMChatClient):
    """Same OpenAI chat route, served by Hugging Face TGI (the guard server). Only the grammar
    differs: TGI takes `{"type": "json", "value": schema}` and rejects `json_schema`."""

    def _response_format(self, schema: dict[str, object]) -> dict[str, object]:
        return {"type": "json", "value": schema}


def _cached_tokens(usage: dict[str, object]) -> int | None:
    # Reported only when vLLM runs with --enable-prompt-tokens-details. A metric, not part
    # of the answer: an odd value gives None instead of failing the call.
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    return cached if isinstance(cached, int) and not isinstance(cached, bool) else None
