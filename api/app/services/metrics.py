"""Metrics of the answer runs, appended to CSV files for offline comparison of configurations.

`calls.v1.csv` holds one row per remote call, `runs.v1.csv` one row per answer run; both
carry the run id. No text from the thread, the answer or the searches is written.
"""

import csv
import dataclasses
import hashlib
import logging
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.core.config import Settings
from app.schemas.answer import ThreadMessage

logger = logging.getLogger(__name__)

# The version changes with the columns: new files start, the old ones stay readable.
RUNS_FILE = "runs.v1.csv"
CALLS_FILE = "calls.v1.csv"

Source = Literal["api", "ticket_stream", "worker"]
Outcome = Literal["answered", "escalated", "embedding_error", "reranker_error", "llm_error"]
CallKind = Literal["embed", "vector_search", "rerank", "llm", "web_search"]


@dataclass
class CallRecord:
    """One remote call. The field order is the column order of `calls.v1.csv`."""

    run_id: str
    seq: int
    kind: CallKind
    step: int | None
    start_ms: float
    duration_ms: float | None = None
    status: str = "ok"
    items: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    finish_reason: str | None = None
    action: str | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class RunRow:
    """One answer run. The field order is the column order of `runs.v1.csv`."""

    run_id: str
    started_at: str
    source: Source
    ticket_id: int | None
    label: str | None
    query_hash: str
    outcome: Outcome
    llm_model: str
    agent_max_steps: int
    llm_max_tokens: int
    retrieval_candidates: int
    retrieval_top_k: int
    rerank_threshold: float
    web_search_enabled: bool
    total_ms: float
    retrieval_ms: float | None
    agent_ms: float | None
    llm_ms: float
    web_ms: float
    steps: int
    invalid_outputs: int
    searches: int
    searches_failed: int
    searches_duplicate: int
    searches_unavailable: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int | None
    memory_candidates: int | None
    memory_kept: int | None
    memory_top_score: float | None
    cited_knowledge: int | None
    cited_web: int | None
    dropped_refs: int | None
    thread_messages: int
    prior_replies: int
    thread_chars: int
    answer_chars: int | None
    web_cost_usd: float | None


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


class RunRecorder:
    """Times the remote calls of one run and keeps what no call row shows."""

    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.started_at = datetime.now(UTC)
        self._origin = time.perf_counter()
        self.calls: list[CallRecord] = []
        self.phase_ms: dict[str, float] = {}
        self.searches_duplicate = 0
        self.searches_unavailable = 0
        self.dropped_refs: int | None = None
        self.memory_candidates: int | None = None
        self.memory_kept: int | None = None
        self.memory_top_score: float | None = None

    def elapsed_ms(self) -> float:
        return _ms(time.perf_counter() - self._origin)

    @contextmanager
    def call(self, kind: CallKind, step: int | None = None) -> Iterator[CallRecord]:
        start = time.perf_counter()
        record = CallRecord(self.run_id, len(self.calls) + 1, kind, step, _ms(start - self._origin))
        self.calls.append(record)
        try:
            yield record
        except Exception as exc:
            record.status = type(exc).__name__
            raise
        finally:
            record.duration_ms = _ms(time.perf_counter() - start)

    @contextmanager
    def phase(self, name: Literal["retrieval", "agent"]) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.phase_ms[name] = _ms(time.perf_counter() - start)


def build_run_row(
    recorder: RunRecorder,
    settings: Settings,
    *,
    source: Source,
    ticket_id: int | None,
    query: str,
    messages: list[ThreadMessage],
    web_search_enabled: bool,
    outcome: Outcome,
    answer: str | None,
    cited_refs: list[str] | None,
) -> RunRow:
    llm = [call for call in recorder.calls if call.kind == "llm"]
    web = [call for call in recorder.calls if call.kind == "web_search"]
    cached = [call.cached_tokens for call in llm if call.cached_tokens is not None]
    costs = [call.cost_usd for call in web if call.cost_usd is not None]
    return RunRow(
        run_id=recorder.run_id,
        started_at=recorder.started_at.isoformat(timespec="milliseconds"),
        source=source,
        ticket_id=ticket_id,
        label=settings.metrics_label,
        query_hash=hashlib.sha256(query.encode()).hexdigest()[:16],
        outcome=outcome,
        llm_model=settings.llm_model,
        agent_max_steps=settings.agent_max_steps,
        llm_max_tokens=settings.llm_max_tokens,
        retrieval_candidates=settings.retrieval_candidates,
        retrieval_top_k=settings.retrieval_top_k,
        rerank_threshold=settings.rerank_threshold,
        web_search_enabled=web_search_enabled,
        total_ms=recorder.elapsed_ms(),
        retrieval_ms=recorder.phase_ms.get("retrieval"),
        agent_ms=recorder.phase_ms.get("agent"),
        llm_ms=_total_ms(llm),
        web_ms=_total_ms(web),
        steps=len(llm),
        invalid_outputs=sum(call.action == "invalid" for call in llm),
        searches=sum(call.status == "ok" for call in web),
        searches_failed=sum(call.status == "WebSearchError" for call in web),
        searches_duplicate=recorder.searches_duplicate,
        searches_unavailable=recorder.searches_unavailable,
        prompt_tokens=sum(call.prompt_tokens or 0 for call in llm),
        completion_tokens=sum(call.completion_tokens or 0 for call in llm),
        cached_tokens=sum(cached) if cached else None,
        memory_candidates=recorder.memory_candidates,
        memory_kept=recorder.memory_kept,
        memory_top_score=recorder.memory_top_score,
        cited_knowledge=None if cited_refs is None else sum(r.startswith("K") for r in cited_refs),
        cited_web=None if cited_refs is None else sum(r.startswith("W") for r in cited_refs),
        dropped_refs=recorder.dropped_refs,
        thread_messages=len(messages),
        prior_replies=sum(message.role == "assistant" for message in messages),
        thread_chars=sum(len(message.content) for message in messages),
        answer_chars=None if answer is None else len(answer),
        # Rounded so that float noise does not show in the CSV.
        web_cost_usd=round(sum(costs), 6) if costs else None,
    )


def _total_ms(calls: list[CallRecord]) -> float:
    return round(sum(call.duration_ms or 0.0 for call in calls), 1)


class CsvMetricsWriter:
    """Appends run and call rows. The API runs one process, so appends do not interleave."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._runs = directory / RUNS_FILE
        self._calls = directory / CALLS_FILE
        # Creates both files with their header now: a wrong volume or permission stops the
        # startup instead of losing every row later.
        _append(self._runs, RunRow, [])
        _append(self._calls, CallRecord, [])

    def write(self, run: RunRow, calls: list[CallRecord]) -> None:
        try:
            _append(self._runs, RunRow, [run])
            _append(self._calls, CallRecord, calls)
        except OSError:
            # Raising would fail an answer already computed and, in the worker, skip posting
            # it to GLPI: metrics come second to answering.
            logger.exception("Metrics write failed for run %s", run.run_id)


def _append(
    path: Path,
    row_type: type[RunRow] | type[CallRecord],
    rows: Sequence[RunRow] | Sequence[CallRecord],
) -> None:
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if file.tell() == 0:
            writer.writerow(field.name for field in dataclasses.fields(row_type))
        writer.writerows(dataclasses.astuple(row) for row in rows)
