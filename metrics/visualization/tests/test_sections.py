from pathlib import Path

import overview
from load import Metrics, load
from stats import fmt_ms, fmt_pct, note
from tests.conftest import run, write


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
