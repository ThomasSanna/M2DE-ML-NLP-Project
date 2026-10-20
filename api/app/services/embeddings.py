from typing import Protocol

import httpx

from app.models import EMBEDDING_DIM


class EmbeddingError(RuntimeError):
    """The embeddings server is unreachable or returned an unexpected response."""


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingClient:
    """Client for an embeddings server exposing `POST /v1/embeddings` (OpenAI format).

    This is the university vLLM API (BAAI/bge-multilingual-gemma2): the model runs
    on their GPU server, not in this container.
    """

    def __init__(self, http: httpx.AsyncClient, model: str, instruction: str | None = None) -> None:
        self._http = http
        self._model = model
        self._instruction = instruction

    def _format(self, text: str) -> str:
        # Instruction format of bge-multilingual-gemma2 (see the model card).
        if self._instruction is None:
            return text
        return f"<instruct>{self._instruction}\n<query>{text}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._http.post(
                "embeddings",
                json={"model": self._model, "input": [self._format(text) for text in texts]},
            )
            response.raise_for_status()
            data = response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise EmbeddingError(f"Embeddings server call failed: {exc!r}") from exc

        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(texts):
            raise EmbeddingError(f"Received {len(vectors)} embeddings for {len(texts)} texts")
        if any(len(vector) != EMBEDDING_DIM for vector in vectors):
            raise EmbeddingError(
                f"Unexpected dimension (expected {EMBEDDING_DIM}): check EMBEDDING_MODEL"
            )
        return vectors
