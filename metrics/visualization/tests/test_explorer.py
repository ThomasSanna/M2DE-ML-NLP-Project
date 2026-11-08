import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import explorer
from load import load
from page import render
from render import Section, View
from tests.conftest import EMPTY_RESULT, call, calls_of, run, write

_SCRIPT = Path(__file__).resolve().parents[1] / "explorer.js"
_NODE = shutil.which("node")


def test_each_answered_run_counts_once_with_all_its_calls(tmp_path: Path) -> None:
    runs = [
        run(),
        run(run_id="r2", total_ms=5000.0, web_ms=900.0, prior_replies=2, memory_kept=0),
        run(run_id="r3", outcome="llm_error", **EMPTY_RESULT),
    ]
    calls = [
        *calls_of("r1"),
        *calls_of("r2"),
        call("r2", 5, "web_search", 1, 3000.0, 900.0),
        *calls_of("r3")[:3],
    ]
    built = explorer.build(load(write(tmp_path, runs, calls)))
    assert built is not None
    kinds = ["embed", "vector_search", "rerank", "llm", "web_search", "other"]
    assert [kind["name"] for kind in built.data["kinds"]] == kinds
    assert built.data["runs"] == {
        "total_ms": [3000.0, 5000.0],
        # Time outside remote calls closes the sum: each row adds up to total_ms.
        "spent_ms": [
            [200.0, 50.0, 750.0, 1990.0, 0.0, 10.0],
            [200.0, 50.0, 750.0, 1990.0, 900.0, 1110.0],
        ],
        "web": [False, True],
        "thread": [False, True],
        "memory": [True, False],
    }


def test_criteria_offer_any_with_and_without(tmp_path: Path) -> None:
    built = explorer.build(load(write(tmp_path, [run()], calls_of("r1"))))
    assert built is not None
    assert [(c.key, [value for value, _ in c.options]) for c in built.criteria] == [
        ("web", ["all", "yes", "no"]),
        ("thread", ["all", "yes", "no"]),
        ("memory", ["all", "yes", "no"]),
    ]


def test_no_explorer_without_answered_run(tmp_path: Path) -> None:
    runs = [run(outcome="llm_error", **EMPTY_RESULT)]
    assert explorer.build(load(write(tmp_path, runs, []))) is None


def test_page_embeds_the_runs_and_the_script_once(tmp_path: Path) -> None:
    built = explorer.build(load(write(tmp_path, [run()], calls_of("r1"))))
    assert built is not None
    views = [View("All", "s", [], [Section("S", [], [built])]) for _ in range(2)]
    page = render(views, source=tmp_path, runs=1, calls=4, generated_at=datetime(2026, 11, 8))
    payloads = re.findall(
        r'<script type="application/json" class="explorer-data">(.*?)</script>', page
    )
    assert [json.loads(payload) for payload in payloads] == [built.data, built.data]
    assert page.count("<script>") == 1
    # One radio group per criterion and explorer, "any" checked first.
    checked = re.findall(r'<input type="radio" name="([^"]+)" [^>]*value="all" checked>', page)
    assert len(checked) == len(set(checked)) == 6


@pytest.mark.skipif(_NODE is None, reason="needs Node.js to run the page script")
def test_browser_statistics_match_pandas(tmp_path: Path) -> None:
    totals = [1000.0 + (i * 7919) % 9000 for i in range(60)]
    runs = [
        run(run_id=f"r{i}", total_ms=total, web_ms=100.0 if i % 3 == 0 else 0.0)
        for i, total in enumerate(totals)
    ]
    built = explorer.build(load(write(tmp_path, runs, [])))
    assert built is not None and _NODE is not None
    choice = {"web": "yes", "thread": "all", "memory": "all"}
    code = (
        f"const {{ summarize }} = require({json.dumps(str(_SCRIPT))});"
        " const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        " process.stdout.write(JSON.stringify(summarize(input.data, input.choice)));"
    )
    result = subprocess.run(
        [_NODE, "-e", code],
        input=json.dumps({"data": built.data, "choice": choice}),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    summary = json.loads(result.stdout)
    picked = pd.Series([total for i, total in enumerate(totals) if i % 3 == 0])
    assert (summary["n"], summary["total"]) == (20, 60)
    assert summary["median"] == pytest.approx(picked.median())
    assert summary["p95"] == pytest.approx(picked.quantile(0.95))
    edges = built.data["edges"]
    assert summary["counts"] == np.histogram(picked / 1000, bins=edges)[0].tolist()
    assert summary["allCounts"] == np.histogram(np.array(totals) / 1000, bins=edges)[0].tolist()
