"""Errors: which runs failed and which remote call failed them."""

import pandas as pd

from load import Metrics
from render import Section, Table
from stats import counts, fmt_pct, listing

# The table lists the latest failures only, so the report stays short on a long history.
LATEST = 50


def build(metrics: Metrics) -> Section:
    runs, calls = metrics.runs, metrics.calls
    failed_runs = runs[runs["outcome"] != "answered"]
    failed_calls = calls[calls["status"] != "ok"]
    findings = ["No failed run."]
    if not failed_runs.empty:
        n = len(runs)
        findings = [
            f"{len(failed_runs)} of {n} runs failed ({fmt_pct(len(failed_runs) / n)}):"
            f" {listing(counts(failed_runs['outcome']))}."
        ]
    if not failed_calls.empty:
        labels = failed_calls["kind"] + " " + failed_calls["status"]
        findings.append(f"Failed calls: {listing(counts(labels))}.")
    if failed_runs.empty:
        return Section("Errors", findings)
    caption = f"Failed runs (latest {LATEST})" if len(failed_runs) > LATEST else "Failed runs"
    return Section("Errors", findings, tables=[Table(caption, _table(failed_runs, failed_calls))])


def _table(failed_runs: pd.DataFrame, failed_calls: pd.DataFrame) -> pd.DataFrame:
    last_failure = failed_calls.groupby("run_id").last()
    failing = (last_failure["kind"] + " " + last_failure["status"]).astype(str)
    latest = failed_runs.sort_values("started_at", ascending=False).head(LATEST)
    return pd.DataFrame(
        {
            "started at": latest["started_at"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "source": latest["source"],
            "config": latest["config_id"],
            "outcome": latest["outcome"],
            "failing call": latest["run_id"].map(failing),
        }
    )
