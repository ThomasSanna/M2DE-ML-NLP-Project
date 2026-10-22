import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx


class LLMError(RuntimeError):
    """Le serveur LLM est injoignable ou a renvoyé un flux inattendu."""


class ChatModel(Protocol):
    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class OpenAIChatClient:
    """Client streaming de `POST /v1/chat/completions` (format OpenAI, `stream: true`).

    C'est l'API du vLLM de l'université (google/gemma3-12b-it). vLLM envoie un événement
    SSE par token généré : chaque `delta.content` non vide est renvoyé tel quel.
    """

    def __init__(self, http: httpx.AsyncClient, model: str) -> None:
        self._http = http
        self._model = model

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        body = {"model": self._model, "messages": messages, "stream": True}
        try:
            async with self._http.stream("POST", "chat/completions", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    choices = json.loads(data)["choices"]
                    delta = choices[0]["delta"].get("content") if choices else None
                    if delta:
                        yield delta
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"Échec de l'appel au serveur LLM : {exc!r}") from exc
