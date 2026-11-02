from typing import Protocol

import httpx


class RerankerError(RuntimeError):
    """The reranker server is unreachable or returned an unexpected response."""


class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class HttpRerankerClient:
    """Client for the university cross-encoder (BAAI/bge-reranker-v2-gemma, `POST /rerank`).

    Returns raw logits in document order; callers turn them into scores.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        try:
            response = await self._http.post(
                "rerank", json={"pairs": [[query, document] for document in documents]}
            )
            response.raise_for_status()
            scores = [float(score) for score in response.json()["scores"]]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise RerankerError(f"Reranker call failed: {exc!r}") from exc

        if len(scores) != len(documents):
            raise RerankerError(f"Received {len(scores)} scores for {len(documents)} documents")
        return scores
