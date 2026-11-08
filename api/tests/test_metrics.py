import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.answer import ThreadMessage
from app.services.embeddings import EmbeddingError
from app.services.llm import LLMError
from app.services.metrics import CsvMetricsWriter, RunRecorder, RunRow, build_run_row
from app.services.websearch import WebSearchError
from tests.conftest import csv_rows


def _llm(
    recorder: RunRecorder,
    step: int,
    action: str,
    prompt: int,
    completion: int,
    cached: int | None,
) -> None:
    with recorder.call("llm", step) as call:
        call.action = action
        call.prompt_tokens = prompt
        call.completion_tokens = completion
        call.cached_tokens = cached


def _minimal_run(settings: Settings) -> tuple[RunRow, RunRecorder]:
    recorder = RunRecorder()
    with recorder.call("embed") as call:
        call.items = 1
    row = build_run_row(
        recorder,
        settings,
        source="api",
        ticket_id=None,
        query="wifi",
        messages=[ThreadMessage(role="user", content="wifi")],
        web_search_enabled=False,
        outcome="answered",
        answer="ok",
        cited_refs=[],
    )
    return row, recorder


def test_call_records_order_duration_and_run_id() -> None:
    recorder = RunRecorder()
    with recorder.call("embed") as call:
        call.items = 1
    with recorder.call("llm", step=1):
        pass

    assert [(c.seq, c.kind, c.step, c.status) for c in recorder.calls] == [
        (1, "embed", None, "ok"),
        (2, "llm", 1, "ok"),
    ]
    assert all(c.duration_ms is not None and c.duration_ms >= 0 for c in recorder.calls)
    assert recorder.calls[0].start_ms <= recorder.calls[1].start_ms
    assert {c.run_id for c in recorder.calls} == {recorder.run_id}


def test_call_records_the_exception_class_and_reraises() -> None:
    recorder = RunRecorder()

    with pytest.raises(EmbeddingError), recorder.call("embed"):
        raise EmbeddingError("down")

    assert recorder.calls[0].status == "EmbeddingError"
    assert recorder.calls[0].duration_ms is not None


def test_phase_is_timed_when_it_raises() -> None:
    recorder = RunRecorder()

    with pytest.raises(LLMError), recorder.phase("agent"):
        raise LLMError("boom")

    assert recorder.phase_ms["agent"] >= 0
    assert "retrieval" not in recorder.phase_ms


def test_build_run_row_aggregates_the_calls(settings: Settings) -> None:
    labelled = settings.model_copy(update={"metrics_label": "baseline"})
    recorder = RunRecorder()
    _llm(recorder, 1, "search", 100, 10, 60)
    with recorder.call("web_search", 1) as call:
        call.items = 2
        call.cost_usd = 0.005
    with pytest.raises(WebSearchError), recorder.call("web_search", 2):
        raise WebSearchError("down")
    _llm(recorder, 2, "invalid", 120, 5, None)
    _llm(recorder, 3, "answer", 150, 20, 100)
    recorder.memory_candidates = 3
    recorder.memory_kept = 1
    recorder.memory_top_score = 0.97
    recorder.searches_duplicate = 1
    recorder.dropped_refs = 0

    row = build_run_row(
        recorder,
        labelled,
        source="worker",
        ticket_id=42,
        query="wifi eduroam",
        messages=[
            ThreadMessage(role="user", content="Le wifi"),
            ThreadMessage(role="assistant", content="Redémarrez"),
            ThreadMessage(role="user", content="Toujours pas"),
        ],
        web_search_enabled=True,
        outcome="answered",
        answer="Voir [K1] [W1]",
        cited_refs=["K1", "W1"],
    )

    assert (row.source, row.ticket_id, row.label, row.outcome) == (
        "worker",
        42,
        "baseline",
        "answered",
    )
    assert row.query_hash == "ab4fc2d1b4318b78"
    assert (row.steps, row.invalid_outputs, row.searches, row.searches_failed) == (3, 1, 1, 1)
    assert (row.searches_duplicate, row.searches_unavailable) == (1, 0)
    assert (row.prompt_tokens, row.completion_tokens, row.cached_tokens) == (370, 35, 160)
    assert (row.memory_candidates, row.memory_kept, row.memory_top_score) == (3, 1, 0.97)
    assert (row.cited_knowledge, row.cited_web, row.dropped_refs) == (1, 1, 0)
    assert (row.thread_messages, row.prior_replies, row.thread_chars, row.answer_chars) == (
        3,
        1,
        29,
        14,
    )
    assert row.web_cost_usd == 0.005
    assert (row.llm_model, row.agent_max_steps, row.rerank_threshold) == (
        settings.llm_model,
        settings.agent_max_steps,
        settings.rerank_threshold,
    )
    assert row.web_search_enabled is True
    assert row.total_ms >= row.llm_ms >= 0


def test_build_run_row_leaves_unreported_values_empty(settings: Settings) -> None:
    recorder = RunRecorder()
    with pytest.raises(EmbeddingError), recorder.call("embed"):
        raise EmbeddingError("down")

    row = build_run_row(
        recorder,
        settings,
        source="api",
        ticket_id=None,
        query="wifi",
        messages=[ThreadMessage(role="user", content="wifi")],
        web_search_enabled=False,
        outcome="embedding_error",
        answer=None,
        cited_refs=None,
    )

    assert (row.steps, row.prompt_tokens, row.llm_ms) == (0, 0, 0.0)
    assert row.cached_tokens is None and row.web_cost_usd is None
    assert row.memory_candidates is None and row.memory_top_score is None
    assert row.cited_knowledge is None and row.dropped_refs is None
    assert row.answer_chars is None and row.agent_ms is None


def test_writer_appends_rows_under_a_single_header(settings: Settings, tmp_path: Path) -> None:
    row, recorder = _minimal_run(settings)
    directory = tmp_path / "metrics"

    CsvMetricsWriter(directory).write(row, recorder.calls)
    # A second writer on the same directory is an API restart.
    CsvMetricsWriter(directory).write(row, recorder.calls)

    runs = csv_rows(directory / "runs.v1.csv")
    assert len(runs) == 2
    assert list(runs[0])[:3] == ["run_id", "started_at", "source"]
    assert runs[0]["cached_tokens"] == ""
    calls = csv_rows(directory / "calls.v1.csv")
    assert len(calls) == 2
    assert list(calls[0])[:3] == ["run_id", "seq", "kind"]


def test_writer_refuses_a_directory_that_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "metrics"
    blocker.write_text("")

    with pytest.raises(OSError):
        CsvMetricsWriter(blocker)


def test_writer_logs_write_errors_without_raising(
    settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    row, _ = _minimal_run(settings)
    directory = tmp_path / "metrics"
    writer = CsvMetricsWriter(directory)
    (directory / "runs.v1.csv").unlink()
    (directory / "runs.v1.csv").mkdir()

    writer.write(row, [])

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_startup_creates_the_metrics_files(settings: Settings, tmp_path: Path) -> None:
    directory = tmp_path / "metrics"

    with TestClient(create_app(settings.model_copy(update={"metrics_dir": directory}))):
        pass

    runs = (directory / "runs.v1.csv").read_text(encoding="utf-8")
    assert runs.startswith("run_id,started_at,source,")
    calls = (directory / "calls.v1.csv").read_text(encoding="utf-8")
    assert calls.startswith("run_id,seq,kind,")


def test_startup_fails_when_the_metrics_dir_is_a_file(settings: Settings, tmp_path: Path) -> None:
    blocker = tmp_path / "metrics"
    blocker.write_text("")
    app = create_app(settings.model_copy(update={"metrics_dir": blocker}))

    with pytest.raises(OSError), TestClient(app):
        pass
