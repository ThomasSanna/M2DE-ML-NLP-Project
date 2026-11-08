from pathlib import Path

import configs
import latency
import overview
from load import Metrics, load
from stats import fmt_ms, fmt_pct, note
from tests.conftest import calls_of, run, write


def test_formats() -> None:
    assert (fmt_ms(1990.0), fmt_ms(250.0), fmt_pct(0.6667)) == ("2.0 s", "250 ms", "67%")
    assert fmt_ms(999.7) == "1.0 s"
    assert (note(19), note(20)) == ("n=19, indicative", "n=20")


def _overview_data(tmp_path: Path) -> Metrics:
    runs = [
        run(),
        run(run_id="r2", source="worker", started_at="2026-11-08T11:00:00.000+00:00"),
        run(run_id="r3", outcome="llm_error", started_at="2026-11-08T12:00:00.000+00:00"),
    ]
    return load(write(tmp_path, runs, []))


def test_overview_findings(tmp_path: Path) -> None:
    assert overview.build(_overview_data(tmp_path)).findings == [
        "3 runs between 2026-11-08 10:00 and 2026-11-08 12:00 UTC.",
        "2 of 3 runs answered (67%).",
        "Sources: api 2, worker 1.",
    ]


def test_overview_of_a_single_run(tmp_path: Path) -> None:
    findings = overview.build(load(write(tmp_path, [run()], []))).findings
    assert findings[0] == "1 run at 2026-11-08 10:00 UTC."


def test_configurations_compare_against_the_most_frequent(tmp_path: Path) -> None:
    a = [run(run_id=f"a{i}") for i in range(10)]
    b = [
        run(
            run_id=f"b{i}",
            label="b",
            total_ms=4500.0,
            prompt_tokens=1000,
            started_at="2026-11-08T11:00:00.000+00:00",
        )
        for i in range(10)
    ]
    findings = configs.build(load(write(tmp_path, a + b, []))).findings
    assert findings[0].startswith("Baseline: (no label) · google/gemma-4-12B-it")
    assert findings[-1] == (
        "b · google/gemma-4-12B-it · steps 3 · max_tokens 512 · candidates 20 · top_k 3"
        " · threshold 0.95 · web on: median total 4.5 s (+50% vs baseline),"
        " mean tokens 1100 (+10%) (n=10, indicative)."
    )


def test_configurations_without_enough_runs_are_not_compared(tmp_path: Path) -> None:
    data = load(write(tmp_path, [run(), run(run_id="r2", label="b")], []))
    assert configs.build(data).findings[-1] == (
        "No configuration other than the baseline has 10 answered runs:"
        " differences are not interpreted."
    )


def test_latency_findings(tmp_path: Path) -> None:
    assert latency.build(load(write(tmp_path, [run()], calls_of("r1")))).findings == [
        "The run closest to the median total (3.0 s) spends: llm 66%, rerank 25%, embed 7%,"
        " vector_search 2%, other 0%.",
        "Slowest call kind by median duration: llm, 2.0 s (n=1, indicative).",
        "Median gap between consecutive calls: 0 ms (n=3, indicative).",
        "Median generation speed: 50 tokens/s, prefill included (n=1, indicative).",
    ]


def test_indicative_tag_stops_at_twenty_runs(tmp_path: Path) -> None:
    runs = [run(run_id=f"r{i}") for i in range(20)]
    calls = [c for i in range(20) for c in calls_of(f"r{i}")]
    findings = latency.build(load(write(tmp_path, runs, calls))).findings
    assert findings[1] == "Slowest call kind by median duration: llm, 2.0 s (n=20)."
