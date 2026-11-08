"""Errors: which runs failed and which remote call failed them."""

import pandas as pd

from i18n import t
from load import Metrics
from render import Section, Table
from stats import counts, fmt_pct, listing

# The table lists the latest failures only, so the report stays short on a long history.
LATEST = 50


def build(metrics: Metrics) -> Section:
    runs, calls = metrics.runs, metrics.calls
    failed_runs = runs[runs["outcome"] != "answered"]
    failed_calls = calls[calls["status"] != "ok"]
    title = t("Errors")
    findings = [t("No failed run.")]
    if not failed_runs.empty:
        findings = [
            t(
                "{failed} of {runs} runs failed ({pct}): {outcomes}.",
                failed=len(failed_runs),
                runs=len(runs),
                pct=fmt_pct(len(failed_runs) / len(runs)),
                outcomes=listing(counts(failed_runs["outcome"])),
            )
        ]
    if not failed_calls.empty:
        labels = failed_calls["kind"] + " " + failed_calls["status"]
        findings.append(t("Failed calls: {calls}.", calls=listing(counts(labels))))
    if failed_runs.empty:
        return Section(title, findings)
    caption = (
        t("Failed runs (latest {latest})", latest=LATEST)
        if len(failed_runs) > LATEST
        else t("Failed runs")
    )
    return Section(title, findings, tables=[Table(caption, _table(failed_runs, failed_calls))])


def _table(failed_runs: pd.DataFrame, failed_calls: pd.DataFrame) -> pd.DataFrame:
    last_failure = failed_calls.groupby("run_id").last()
    failing = (last_failure["kind"] + " " + last_failure["status"]).astype(str)
    latest = failed_runs.sort_values("started_at", ascending=False).head(LATEST)
    return pd.DataFrame(
        {
            t("started at"): latest["started_at"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            t("source"): latest["source"],
            t("config"): latest["config_id"],
            t("outcome"): latest["outcome"],
            t("failing call"): latest["run_id"].map(failing),
        }
    )
