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
                    title=_optional_str(item, "title") or _url(item),
                    url=_url(item),
                    published=_optional_str(item, "publishedDate"),
                    excerpt=" … ".join(_highlights(item))[:EXCERPT_MAX_CHARS],
                )
                for item in body["results"]
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WebSearchError(f"Web search call failed: {exc!r}") from exc

        # Only logged: an unexpected shape must not discard valid results.
        cost = body.get("costDollars")
        logger.info("Exa search cost %s USD", cost.get("total") if isinstance(cost, dict) else cost)
        return results


def _url(item: dict[str, object]) -> str:
    url = item["url"]
    if not isinstance(url, str):
        raise TypeError(f"Exa result url is {type(url).__name__}, not str")
    return url


def _optional_str(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"Exa result {key} is {type(value).__name__}, not str")
    return value


def _highlights(item: dict[str, object]) -> list[str]:
    highlights = item.get("highlights") or []
    if not isinstance(highlights, list) or not all(isinstance(part, str) for part in highlights):
        raise TypeError("Exa result highlights is not a list of str")
    return highlights
