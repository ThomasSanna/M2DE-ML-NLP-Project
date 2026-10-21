import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

EXA_BASE_URL = "https://api.exa.ai"
EXCERPT_MAX_CHARS = 500


class WebSearchError(RuntimeError):
    """The web search API is unreachable or returned an unexpected response."""


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    published: str | None
    excerpt: str


class WebSearch(Protocol):
    async def search(self, query: str) -> list[WebResult]: ...


class ExaClient:
    """Client for Exa search (`POST /search`), returning query-relevant highlights.

    The `x-api-key` header is set on the injected `httpx.AsyncClient`.
    """

    def __init__(
        self, http: httpx.AsyncClient, num_results: int, include_domains: list[str]
    ) -> None:
        self._http = http
        self._num_results = num_results
        self._include_domains = include_domains

    async def search(self, query: str) -> list[WebResult]:
        payload: dict[str, object] = {
            "query": query,
            "type": "auto",
            "numResults": self._num_results,
            "contents": {"highlights": {"maxCharacters": EXCERPT_MAX_CHARS}},
        }
        if self._include_domains:
            payload["includeDomains"] = self._include_domains
        try:
            response = await self._http.post("search", json=payload)
            response.raise_for_status()
            body = response.json()
            results = [
                WebResult(
                    title=item.get("title") or item["url"],
                    url=item["url"],
                    published=item.get("publishedDate"),
                    excerpt=" … ".join(item.get("highlights") or [])[:EXCERPT_MAX_CHARS],
                )
                for item in body["results"]
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WebSearchError(f"Web search call failed: {exc!r}") from exc

        logger.info("Exa search cost %s USD", (body.get("costDollars") or {}).get("total"))
        return results
