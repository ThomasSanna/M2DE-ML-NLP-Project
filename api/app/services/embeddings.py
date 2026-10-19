from typing import Protocol

import httpx

from app.models import EMBEDDING_DIM


class EmbeddingError(RuntimeError):
    """Le serveur d'embeddings est injoignable ou a renvoyé une réponse inattendue."""


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingClient:
    """Client d'un serveur d'embeddings exposant `POST /v1/embeddings` (format OpenAI).

    C'est l'API du vLLM de l'université (BAAI/bge-multilingual-gemma2) : le modèle tourne
    sur leur serveur GPU, pas dans ce conteneur.
    """

    def __init__(self, http: httpx.AsyncClient, model: str, instruction: str | None = None) -> None:
        self._http = http
        self._model = model
        self._instruction = instruction

    def _format(self, text: str) -> str:
        # Format d'instruction de bge-multilingual-gemma2 (cf. fiche du modèle).
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
            raise EmbeddingError(f"Échec de l'appel au serveur d'embeddings : {exc!r}") from exc

        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(texts):
            raise EmbeddingError(f"{len(vectors)} embeddings reçus pour {len(texts)} textes")
        if any(len(vector) != EMBEDDING_DIM for vector in vectors):
            raise EmbeddingError(
                f"Dimension inattendue (attendu {EMBEDDING_DIM}) : vérifier EMBEDDING_MODEL"
            )
        return vectors
