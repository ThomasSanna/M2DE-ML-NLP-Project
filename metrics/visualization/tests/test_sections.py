from pathlib import Path

import pandas as pd

import agent
import configs
import errors
import latency
import memory
import overview
import tokens
from i18n import language
from load import Metrics, load
from render import histogram
from stats import fmt_ms, fmt_pct, note
from tests.conftest import call, calls_of, run, write


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


def test_overview_findings_in_french(tmp_path: Path) -> None:
    data = _overview_data(tmp_path)
    with language("fr"):
        findings = overview.build(data).findings
    assert findings == [
        "3 exécutions du 2026-11-08 10:00 au 2026-11-08 12:00 UTC.",
        "Exécutions ayant répondu : 2 sur 3 (67\u00a0%).",
        "Sources : api 2, worker 1.",
    ]


def test_configuration_label_in_french(tmp_path: Path) -> None:
    with language("fr"):
        findings = configs.build(load(write(tmp_path, [run()], []))).findings
    assert findings[0] == (
        "Référence : (sans label) · google/gemma-4-12B-it · étapes 3 · max_tokens 512"
        " · candidats 20 · top_k 3 · seuil 0,95 · web activé (n=1, indicatif)."
    )


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


def test_tokens_findings_without_cache(tmp_path: Path) -> None:
    assert tokens.build(load(write(tmp_path, [run()], calls_of("r1")))).findings == [
        "Mean per answered run: 900 prompt and 100 completion tokens (n=1, indicative).",
        "Cached tokens are not reported: start vLLM with --enable-prompt-tokens-details"
        " to measure the prefix cache.",
    ]


def test_tokens_cache_rate_and_prompt_growth(tmp_path: Path) -> None:
    calls = [
        *calls_of("r1")[:3],
        call(
            "r1",
            4,
            "llm",
            1,
            1010.0,
            900.0,
            prompt_tokens=900,
            completion_tokens=10,
            action="search",
        ),
        call(
            "r1",
            5,
            "llm",
            2,
            1910.0,
            1090.0,
            prompt_tokens=1300,
            completion_tokens=90,
            action="answer",
        ),
    ]
    data = load(write(tmp_path, [run(steps=2, cached_tokens=1100, prompt_tokens=2200)], calls))
    findings = tokens.build(data).findings
    assert "Cache rate: 50% of prompt tokens (n=1, indicative)." in findings
    assert "Mean prompt tokens grow from 900 at step 1 to 1300 at step 2." in findings


def test_agent_findings(tmp_path: Path) -> None:
    runs = [run(), run(run_id="r2", steps=2, searches=1, web_cost_usd=0.005)]
    findings = agent.build(load(write(tmp_path, runs, []))).findings
    assert "1 of 2 answered runs answered at step 1 (50%, n=2, indicative)." in findings
    assert (
        "Web searches: 1 ok, 0 failed, 0 duplicate, 0 unavailable, 0.50 per run (n=2, indicative)."
    ) in findings
    assert "Exa cost: $0.0050 in total, $0.0050 per run with a search." in findings
    assert "No model call in the data." in findings


def test_agent_model_calls(tmp_path: Path) -> None:
    calls = [
        *calls_of("r1")[:3],
        call("r1", 4, "llm", 1, 1010.0, 900.0, action="invalid", finish_reason="length"),
        call("r1", 5, "llm", 2, 1910.0, 1090.0, action="answer", finish_reason="stop"),
    ]
    findings = agent.build(load(write(tmp_path, [run(steps=2, invalid_outputs=1)], calls))).findings
    assert "Invalid model outputs: 1 of 2 model calls (50%)." in findings
    assert "Responses cut by max_tokens: 1 of 2 model calls." in findings


def test_agent_without_web_search(tmp_path: Path) -> None:
    findings = agent.build(load(write(tmp_path, [run()], calls_of("r1")))).findings
    assert "No web search in the data." in findings


def test_memory_findings(tmp_path: Path) -> None:
    runs = [
        run(),
        run(
            run_id="r2",
            memory_kept=0,
            memory_top_score=0.93,
            cited_knowledge=0,
            prior_replies=1,
            total_ms=4000.0,
        ),
        run(run_id="r3", memory_kept=0, memory_top_score=0.5, cited_knowledge=0),
    ]
    assert memory.build(load(write(tmp_path, runs, []))).findings == [
        "2 of 3 runs kept no memory entry (67%, n=3, indicative).",
        "1 run scored within 0.05 below the threshold: a lower threshold would admit its"
        " best entry.",
        "Answers grounded in memory 33%, web 0%, both 0%, none 67% (n=3, indicative).",
        "Median total: 3.0 s for first questions (n=2, indicative),"
        " 4.0 s for follow-ups (n=1, indicative).",
    ]


def test_memory_without_follow_ups_or_near_misses(tmp_path: Path) -> None:
    findings = memory.build(load(write(tmp_path, [run()], []))).findings
    assert "No run scored within 0.05 below the threshold." in findings
    assert "No follow-up question in the data." in findings


def test_errors_findings(tmp_path: Path) -> None:
    runs = [
        run(),
        run(run_id="r2", outcome="llm_error"),
        run(run_id="r3", outcome="embedding_error"),
    ]
    calls = [
        *calls_of("r2")[:3],
        call("r2", 4, "llm", 1, 1010.0, 5.0, status="LLMError"),
        call("r3", 1, "embed", None, 0.0, 5.0, status="EmbeddingError"),
    ]
    assert errors.build(load(write(tmp_path, runs, calls))).findings == [
        "2 of 3 runs failed (67%): embedding_error 1, llm_error 1.",
        "Failed calls: embed EmbeddingError 1, llm LLMError 1.",
    ]


def test_errors_without_failures(tmp_path: Path) -> None:
    findings = errors.build(load(write(tmp_path, [run()], calls_of("r1")))).findings
    assert findings == ["No failed run."]


def test_histogram_of_nearly_equal_values() -> None:
    # Float noise around one value (60 tokens/s from every call) must not break the bins.
    values = pd.Series([59.99999999999999, 60.0, 60.000000000000014], dtype="Float64")
    assert histogram(values, xlabel="x", ylabel="y", caption="c", reading="r").png_base64


def test_threshold_lines_name_their_configurations(tmp_path: Path) -> None:
    runs = [
        *(run(run_id=f"a{i}") for i in range(3)),
        *(run(run_id=f"b{i}", label="b", rerank_threshold=0.9) for i in range(2)),
        run(run_id="c0", label="c"),
    ]
    data = load(write(tmp_path, runs, []))
    assert memory.thresholds(data.runs) == [(0.9, "C2: 0.9"), (0.95, "C1, C3: 0.95")]
    with language("fr"):
        assert memory.thresholds(data.runs) == [(0.9, "C2 : 0,9"), (0.95, "C1, C3 : 0,95")]


def test_timeline_names_the_run_it_shows(tmp_path: Path) -> None:
    captions = [
        c.caption for c in latency.build(load(write(tmp_path, [run()], calls_of("r1")))).charts
    ]
    assert "Timeline of the run closest to the median total (3.0 s): C1, 2026-11-08 10:00 UTC" in (
        captions
    )
