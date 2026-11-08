import csv
from pathlib import Path

from load import CALL_COLUMNS, RUN_COLUMNS

Row = dict[str, object]

_RUN: Row = {
    "run_id": "r1",
    "started_at": "2026-11-08T10:00:00.000+00:00",
    "source": "api",
    "outcome": "answered",
    "llm_model": "google/gemma-4-12B-it",
    "agent_max_steps": 3,
    "llm_max_tokens": 512,
    "retrieval_candidates": 20,
    "retrieval_top_k": 3,
    "rerank_threshold": 0.95,
    "web_search_enabled": True,
    "total_ms": 3000.0,
    "retrieval_ms": 1000.0,
    "agent_ms": 2000.0,
    "llm_ms": 1990.0,
    "web_ms": 0.0,
    "steps": 1,
    "invalid_outputs": 0,
    "searches": 0,
    "searches_failed": 0,
    "searches_duplicate": 0,
    "searches_unavailable": 0,
    "prompt_tokens": 900,
    "completion_tokens": 100,
    "memory_candidates": 20,
    "memory_kept": 3,
    "memory_top_score": 0.99,
    "cited_knowledge": 1,
    "cited_web": 0,
    "dropped_refs": 0,
    "thread_messages": 1,
    "prior_replies": 0,
    "thread_chars": 150,
    "answer_chars": 500,
}

# The columns a run that ended on a remote error leaves empty or at zero.
EMPTY_RESULT: Row = {
    "agent_ms": None,
    "llm_ms": 0.0,
    "steps": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "memory_candidates": None,
    "memory_kept": None,
    "memory_top_score": None,
    "cited_knowledge": None,
    "cited_web": None,
    "dropped_refs": None,
    "answer_chars": None,
}


def run(**overrides: object) -> Row:
    return {**_RUN, **overrides}


def call(
    run_id: str,
    seq: int,
    kind: str,
    step: int | None,
    start_ms: float,
    duration_ms: float,
    **fields: object,
) -> Row:
    row: Row = {
        "run_id": run_id,
        "seq": seq,
        "kind": kind,
        "step": step,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "status": "ok",
    }
    return {**row, **fields}


def calls_of(run_id: str) -> list[Row]:
    """The calls of a run answered at step 1 from the memory, matching run()'s durations."""
    return [
        call(run_id, 1, "embed", None, 0.0, 200.0, items=1),
        call(run_id, 2, "vector_search", None, 200.0, 50.0, items=20),
        call(run_id, 3, "rerank", None, 250.0, 750.0, items=20),
        call(
            run_id,
            4,
            "llm",
            1,
            1010.0,
            1990.0,
            prompt_tokens=900,
            completion_tokens=100,
            finish_reason="stop",
            action="answer",
        ),
    ]


def write(directory: Path, runs: list[Row], calls: list[Row]) -> Path:
    """Write both CSV files as the API does: v1 header, empty cell for a missing value."""
    for name, columns, rows in (
        ("runs.v1.csv", RUN_COLUMNS, runs),
        ("calls.v1.csv", CALL_COLUMNS, calls),
    ):
        with (directory / name).open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            writer.writerows(
                ["" if row.get(column) is None else row[column] for column in columns]
                for row in rows
            )
    return directory
