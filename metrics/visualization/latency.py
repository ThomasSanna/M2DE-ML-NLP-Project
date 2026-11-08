"""Latency: where the time of a run goes, call by call."""

import pandas as pd

from load import Metrics, answered
from render import (
    INK,
    KIND_COLORS,
    NEUTRAL,
    SURFACE,
    Chart,
    Section,
    Table,
    canvas,
    chart,
    histogram,
    legend,
)
from stats import cell_ms, fmt_ms, fmt_p95, fmt_pct, note

KINDS = ("embed", "vector_search", "rerank", "llm", "web_search")


def build(metrics: Metrics) -> Section:
    calls = metrics.calls
    done = answered(metrics.runs)
    charts = []
    if not done.empty:
        charts.append(_breakdown(done, calls))
        typical = _typical(done)
        if (calls["run_id"] == typical["run_id"]).any():
            charts.append(_timeline(typical, calls))
    gaps = calls["gap_ms"].dropna()
    if not gaps.empty:
        charts.append(
            histogram(
                gaps,
                xlabel="ms from the end of a call to the start of the next",
                caption="Gaps between consecutive calls of a run (prompt building, DB, parsing)",
            )
        )
    speed = calls["tokens_per_s"].dropna()
    if not speed.empty:
        charts.append(
            histogram(
                speed,
                xlabel="completion tokens per second, prefill included",
                caption="Generation speed of successful model calls",
                color=KIND_COLORS["llm"],
            )
        )
    return Section(
        "Latency",
        [_shares(done, calls), _slowest(calls), _gap(gaps), _speed(speed)],
        charts,
        [Table("Duration per call kind", _per_kind(calls))],
    )


def _typical(done: pd.DataFrame) -> pd.Series:
    """The answered run whose total is closest to the median total."""
    distance = (done["total_ms"] - done["total_ms"].median()).abs()
    return done.loc[distance.idxmin()]


def _shares(done: pd.DataFrame, calls: pd.DataFrame) -> str:
    if done.empty:
        return "No answered run: no latency breakdown."
    run = _typical(done)
    total = float(run["total_ms"])
    spent = calls[calls["run_id"] == run["run_id"]].groupby("kind")["duration_ms"].sum()
    shares = sorted(((str(k), float(v) / total) for k, v in spent.items()), key=lambda p: -p[1])
    parts = [f"{kind} {fmt_pct(share)}" for kind, share in shares]
    parts.append(f"other {fmt_pct(float(run['overhead_ms']) / total)}")
    median = float(done["total_ms"].median())
    return f"The run closest to the median total ({fmt_ms(median)}) spends: {', '.join(parts)}."


def _slowest(calls: pd.DataFrame) -> str:
    ok = calls[calls["status"] == "ok"]
    if ok.empty:
        return "No successful call in the data."
    medians = ok.groupby("kind")["duration_ms"].median()
    kind = str(medians.idxmax())
    n = int((ok["kind"] == kind).sum())
    return f"Slowest call kind by median duration: {kind}, {fmt_ms(medians[kind])} ({note(n)})."


def _gap(gaps: pd.Series) -> str:
    if gaps.empty:
        return "No consecutive calls in the data."
    return f"Median gap between consecutive calls: {fmt_ms(gaps.median())} ({note(len(gaps))})."


def _speed(speed: pd.Series) -> str:
    if speed.empty:
        return "No successful model call in the data."
    return (
        f"Median generation speed: {speed.median():.0f} tokens/s, prefill included"
        f" ({note(len(speed))})."
    )


def _per_kind(calls: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for kind in KINDS:
        of_kind = calls[calls["kind"] == kind]
        if of_kind.empty:
            continue
        ok = of_kind.loc[of_kind["status"] == "ok", "duration_ms"]
        rows.append(
            {
                "kind": kind,
                "calls": len(of_kind),
                "failed": int((of_kind["status"] != "ok").sum()),
                "median": cell_ms(ok.median()) if not ok.empty else "-",
                "p95": fmt_p95(ok.quantile(0.95) if not ok.empty else float("nan"), len(ok)),
                "max": cell_ms(ok.max()) if not ok.empty else "-",
            }
        )
    return pd.DataFrame(rows, columns=["kind", "calls", "failed", "median", "p95", "max"])


def _breakdown(done: pd.DataFrame, calls: pd.DataFrame) -> Chart:
    per_run = (
        calls[calls["run_id"].isin(done["run_id"])]
        .pivot_table(index="run_id", columns="kind", values="duration_ms", aggfunc="sum")
        .reindex(done["run_id"])
        .fillna(0.0)
    )
    per_run["other"] = done.set_index("run_id")["overhead_ms"]
    means = per_run.groupby(done.set_index("run_id")["config_id"]).mean()
    means = means.sort_index(key=lambda ids: ids.str[1:].astype(int))
    fig, ax = canvas(height=0.5 * len(means) + 1.4, grid="x")
    left = pd.Series(0.0, index=means.index)
    for kind in (*KINDS, "other"):
        if kind in means:
            ax.barh(
                means.index,
                means[kind],
                left=left,
                color=KIND_COLORS[kind],
                label=kind,
                height=0.6,
                edgecolor=SURFACE,
                linewidth=1.2,
            )
            left = left + means[kind]
    ax.invert_yaxis()
    ax.set_xlabel("ms per answered run (mean)")
    legend(ax)
    return chart(
        fig, "Mean time per answered run by call kind, per configuration (sums to the mean total)"
    )


def _timeline(run: pd.Series, calls: pd.DataFrame) -> Chart:
    steps = calls[calls["run_id"] == run["run_id"]].sort_values("seq")
    fig, ax = canvas(height=0.35 * len(steps) + 1.2, grid="x")
    labels = []
    for i, call in enumerate(steps.itertuples()):
        ax.barh(
            i,
            call.duration_ms,
            left=call.start_ms,
            color=KIND_COLORS.get(str(call.kind), NEUTRAL),
            height=0.6,
        )
        labels.append(str(call.kind) if pd.isna(call.step) else f"{call.kind} (step {call.step})")
    ax.axvline(float(run["total_ms"]), color=INK, linewidth=1, linestyle="--")
    ax.set_yticks(range(len(labels)), labels)
    ax.invert_yaxis()
    ax.set_xlabel("ms since the run started (dashed line: end of run)")
    return chart(
        fig, f"Timeline of the run closest to the median total ({fmt_ms(run['total_ms'])})"
    )
