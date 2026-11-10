"""Progress of the answers the bot writes, as the GLPI plugin displays it.

The board lives in the API process: the worker records what the agent is doing,
`GET /api/v1/tickets/{id}/progress` reads it back for the plugin, and nothing is
persisted. A finished run is dropped `ttl` after it finished.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.services.websearch import WebResult

DEFAULT_TTL = timedelta(minutes=5)
TERMINAL = frozenset({"done", "failed"})


class TicketProgress(Protocol):
    """What the agent reports while it works on a ticket."""

    def thinking(self) -> None: ...

    def searching(self, query: str) -> None: ...

    def found(self, sources: list[WebResult]) -> None: ...


@dataclass(frozen=True)
class ProgressView:
    """Snapshot of a run, copied so the caller cannot corrupt the board."""

    status: str
    steps: list[dict[str, Any]]
    followup_id: int | None
    updated_at: datetime


def domain_of(url: str) -> str:
    """Host of a URL without the `www.` prefix, as the sources panel shows it.

    A URL the parser rejects (`http://[::1`) has no domain rather than an error:
    a broken source must not abort the answer.
    """
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return ""
    return (host or "").removeprefix("www.")


def _source(result: WebResult) -> dict[str, str]:
    return {"title": result.title, "url": result.url, "domain": domain_of(result.url)}


def _copy(step: dict[str, Any]) -> dict[str, Any]:
    copied = dict(step)
    if "sources" in copied:
        copied["sources"] = [dict(source) for source in copied["sources"]]
    return copied


@dataclass
class _Run:
    status: str
    updated_at: datetime
    steps: list[dict[str, Any]] = field(default_factory=list)
    followup_id: int | None = None


class _Handle:
    """Handle of one run, returned by `ProgressBoard.start`."""

    def __init__(self, board: "ProgressBoard", state: _Run) -> None:
        self._board = board
        self._state = state

    @property
    def _finished(self) -> bool:
        return self._state.status in TERMINAL

    def thinking(self) -> None:
        if self._finished:
            return
        self._state.steps.append({"kind": "thinking"})
        self._state.status = "thinking"
        self._board._touch(self._state)

    def searching(self, query: str) -> None:
        if self._finished:
            return
        self._state.steps.append({"kind": "search", "query": query, "sources": []})
        self._state.status = "searching"
        self._board._touch(self._state)

    def found(self, sources: list[WebResult]) -> None:
        if self._finished:
            return
        step = next(
            (step for step in reversed(self._state.steps) if step["kind"] == "search"), None
        )
        if step is None:
            return
        step["sources"] = [_source(source) for source in sources]
        self._board._touch(self._state)


class ProgressBoard:
    """Latest run of each ticket, kept in this process only."""

    def __init__(
        self,
        *,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._runs: dict[int, _Run] = {}

    def start(self, ticket_id: int) -> TicketProgress:
        """Forget the previous run of the ticket and open the new one."""
        self._purge()
        state = _Run(status="thinking", updated_at=self._clock())
        self._runs[ticket_id] = state
        return _Handle(self, state)

    def done(self, ticket_id: int, followup_id: int) -> None:
        """The answer was posted; `followup_id` is its timeline entry."""
        state = self._runs.get(ticket_id)
        if state is None:
            return
        state.status = "done"
        state.followup_id = followup_id
        self._touch(state)

    def failed(self, ticket_id: int) -> None:
        """The run stopped on an error; a technician takes over."""
        state = self._runs.get(ticket_id)
        if state is None:
            return
        state.status = "failed"
        self._touch(state)

    def get(self, ticket_id: int) -> ProgressView | None:
        self._purge()
        state = self._runs.get(ticket_id)
        if state is None:
            return None
        return ProgressView(
            status=state.status,
            steps=[_copy(step) for step in state.steps],
            followup_id=state.followup_id,
            updated_at=state.updated_at,
        )

    def _touch(self, state: _Run) -> None:
        state.updated_at = self._clock()

    def _purge(self) -> None:
        now = self._clock()
        for ticket_id, state in list(self._runs.items()):
            if state.status in TERMINAL and now - state.updated_at > self._ttl:
                del self._runs[ticket_id]
