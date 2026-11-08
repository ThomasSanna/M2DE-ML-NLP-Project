from pathlib import Path

import pandas as pd
import pytest

from load import CALL_COLUMNS, RUN_COLUMNS, DataError, load
from tests.conftest import calls_of, run, write


def test_columns_match_the_api_v1_headers() -> None:
    assert ",".join(RUN_COLUMNS) == (
        "run_id,started_at,source,ticket_id,label,query_hash,outcome,llm_model,agent_max_steps,"
        "llm_max_tokens,retrieval_candidates,retrieval_top_k,rerank_threshold,web_search_enabled,"
        "total_ms,retrieval_ms,agent_ms,llm_ms,web_ms,steps,invalid_outputs,searches,"
        "searches_failed,searches_duplicate,searches_unavailable,prompt_tokens,completion_tokens,"
        "cached_tokens,memory_candidates,memory_kept,memory_top_score,cited_knowledge,cited_web,"
        "dropped_refs,thread_messages,prior_replies,thread_chars,answer_chars,web_cost_usd"
    )
    assert ",".join(CALL_COLUMNS) == (
        "run_id,seq,kind,step,start_ms,duration_ms,status,items,prompt_tokens,completion_tokens,"
        "cached_tokens,finish_reason,action,cost_usd"
    )


def test_load_reads_types_and_missing_values(tmp_path: Path) -> None:
    metrics = load(write(tmp_path, [run()], calls_of("r1")))
    assert metrics.runs["cached_tokens"].isna().all()
    assert metrics.runs["web_search_enabled"].tolist() == [True]
    assert str(metrics.runs["started_at"].dt.tz) == "UTC"
    assert metrics.calls["step"].isna().tolist() == [True, True, True, False]


def test_load_keeps_a_label_that_looks_like_a_missing_value(tmp_path: Path) -> None:
    metrics = load(write(tmp_path, [run(label="None")], []))
    assert metrics.runs["label"].tolist() == ["None"]


def test_load_derives_columns(tmp_path: Path) -> None:
    runs = [
        run(),
        run(run_id="r2", cached_tokens=450, cited_knowledge=0, cited_web=1),
        run(run_id="r3", cited_web=2),
        run(run_id="r4", cited_knowledge=0),
        run(run_id="r5", outcome="llm_error", cited_knowledge=None, cited_web=None),
    ]
    metrics = load(write(tmp_path, runs, calls_of("r1")))
    gaps = metrics.calls["gap_ms"]
    assert gaps.tolist()[:3] == [0.0, 0.0, 10.0]
    assert pd.isna(gaps.iloc[3])
    assert metrics.calls["tokens_per_s"].iloc[3] == pytest.approx(50.2513, abs=1e-4)
    assert metrics.calls["tokens_per_s"].iloc[:3].isna().all()
    assert metrics.runs["overhead_ms"].iloc[0] == pytest.approx(10.0)
    assert metrics.runs["overhead_ms"].iloc[1] == pytest.approx(3000.0)  # no call rows
    assert pd.isna(metrics.runs["cache_rate"].iloc[0])
    assert metrics.runs["cache_rate"].iloc[1] == pytest.approx(0.5)
    assert metrics.runs["config"].iloc[0] == (
        "(no label) · google/gemma-4-12B-it · steps 3 · max_tokens 512 · candidates 20"
        " · top_k 3 · threshold 0.95 · web on"
    )
    grounding = metrics.runs["grounding"].tolist()
    assert grounding[:4] == ["memory", "web", "memory+web", "none"]
    assert pd.isna(grounding[4])


def _break(directory: Path, setup: str) -> None:
    if setup == "no_calls_file":
        (directory / "calls.v1.csv").unlink()
        return
    runs_file = directory / "runs.v1.csv"
    lines = runs_file.read_text(encoding="utf-8").splitlines()
    if setup == "header_only":
        lines = lines[:1]
    elif setup == "extra_column":
        lines = [f"{line},extra" for line in lines]
    else:
        lines = [line.rsplit(",", 1)[0] for line in lines]
    runs_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("no_calls_file", "calls.v1.csv"),
        ("header_only", "no run"),
        ("extra_column", "columns"),
        ("missing_column", "columns"),
    ],
)
def test_load_rejects_bad_files(tmp_path: Path, setup: str, message: str) -> None:
    _break(write(tmp_path, [run()], calls_of("r1")), setup)
    with pytest.raises(DataError, match=message):
        load(tmp_path)


def test_config_ids_follow_answered_runs(tmp_path: Path) -> None:
    runs = [
        run(run_id="a1", label="a", outcome="llm_error"),
        run(run_id="b1", label="b"),
        run(run_id="a2", label="a"),
        run(run_id="b2", label="b"),
    ]
    metrics = load(write(tmp_path, runs, []))
    assert dict(zip(metrics.runs["run_id"], metrics.runs["config_id"], strict=True)) == {
        "a1": "C2",
        "b1": "C1",
        "a2": "C2",
        "b2": "C1",
    }


def test_only_keeps_one_configuration_and_its_calls(tmp_path: Path) -> None:
    runs = [run(), run(run_id="r2"), run(run_id="r3", label="b")]
    metrics = load(write(tmp_path, runs, [*calls_of("r1"), *calls_of("r3")]))
    only = metrics.only("C2")
    assert list(only.runs["run_id"]) == ["r3"]
    assert set(only.calls["run_id"]) == {"r3"}
    # Ids come from the whole data: C1 stays C1 when it is shown alone.
    assert list(metrics.only("C1").runs["config_id"]) == ["C1", "C1"]
