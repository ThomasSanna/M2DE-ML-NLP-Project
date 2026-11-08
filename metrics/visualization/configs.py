"""Configurations: each set of settings and METRICS_LABEL against the baseline (C1)."""

import pandas as pd

from load import Metrics, answered
from render import Chart, Section, Table, boxes
from stats import MIN_GROUP, cell_ms, cell_num, cell_pct, fmt_ms, fmt_p95, note

TITLE = "Configurations"
_MEMORY = ("memory", "memory+web")


def build(metrics: Metrics) -> Section:
    summary = _summary(metrics.runs)
    table = Table("Answered runs per configuration (C1 is the baseline)", _table(summary))
    done = answered(metrics.runs)
    if done.empty:
        findings = ["No answered run: configurations cannot be compared."]
        return Section(TITLE, findings, tables=[table])
    return Section(TITLE, _findings(summary), charts=[_totals(done)], tables=[table])


def _summary(runs: pd.DataFrame) -> pd.DataFrame:
    summary = runs.groupby("config_id").agg(
        config=("config", "first"), errors=("outcome", lambda o: int((o != "answered").sum()))
    )
    groups = answered(runs).groupby("config_id")
    summary["answered"] = groups.size().reindex(summary.index, fill_value=0)
    summary["median_ms"] = groups["total_ms"].median()
    summary["p95_ms"] = groups["total_ms"].quantile(0.95)
    summary["prompt"] = groups["prompt_tokens"].mean()
    summary["completion"] = groups["completion_tokens"].mean()
    summary["steps"] = groups["steps"].mean()
    summary["memory"] = groups["grounding"].agg(lambda g: g.isin(_MEMORY).mean())
    return summary.sort_index(key=lambda ids: ids.str[1:].astype(int))


def _findings(summary: pd.DataFrame) -> list[str]:
    base = summary.iloc[0]
    findings = [f"Baseline: {base['config']} ({note(int(base['answered']))})."]
    others = summary.iloc[1:]
    if others.empty:
        return [*findings, "Only one configuration in the data: nothing to compare."]
    compared = others[others["answered"] >= MIN_GROUP]
    if compared.empty:
        return [
            *findings,
            f"No configuration other than the baseline has {MIN_GROUP} answered runs:"
            " differences are not interpreted.",
        ]
    base_tokens = base["prompt"] + base["completion"]
    for _, row in compared.iterrows():
        tokens = row["prompt"] + row["completion"]
        findings.append(
            f"{row['config']}: median total {fmt_ms(row['median_ms'])}"
            f" ({row['median_ms'] / base['median_ms'] - 1:+.0%} vs baseline),"
            f" mean tokens {tokens:.0f} ({tokens / base_tokens - 1:+.0%})"
            f" ({note(int(row['answered']))})."
        )
    return findings


def _table(summary: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": summary.index,
            "configuration": summary["config"],
            "answered": summary["answered"],
            "errors": summary["errors"],
            "median total": summary["median_ms"].map(cell_ms),
            "p95 total": [
                fmt_p95(p95, n)
                for p95, n in zip(summary["p95_ms"], summary["answered"], strict=True)
            ],
            "prompt tokens": summary["prompt"].map(cell_num),
            "completion tokens": summary["completion"].map(cell_num),
            "steps": summary["steps"].map(lambda v: cell_num(v, 2)),
            "grounded in memory": summary["memory"].map(cell_pct),
        }
    )


def _totals(done: pd.DataFrame) -> Chart:
    groups = sorted(done.groupby("config_id"), key=lambda group: int(group[0][1:]))
    return boxes(
        [(str(config_id), group["total_ms"]) for config_id, group in groups],
        xlabel="total ms per answered run",
        caption="Total duration per configuration",
    )
