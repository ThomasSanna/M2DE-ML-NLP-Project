"""Overview: what the data holds and how runs ended."""

import pandas as pd
from matplotlib.ticker import MaxNLocator

from load import Metrics
from render import OUTCOME_COLORS, SURFACE, Chart, Section, Table, canvas, chart, legend
from stats import counts, fmt_pct, listing


def build(metrics: Metrics) -> Section:
    runs = metrics.runs
    return Section(
        title="Overview",
        findings=[_period(runs), _answered(runs), f"Sources: {listing(counts(runs['source']))}."],
        charts=[_per_day(runs)],
        tables=[
            Table("Runs per configuration", _per_config(runs)),
            Table("Runs per source and outcome", _crosstab(runs, "source")),
        ],
    )


def _period(runs: pd.DataFrame) -> str:
    first, last = runs["started_at"].min(), runs["started_at"].max()
    if len(runs) == 1:
        return f"1 run at {first:%Y-%m-%d %H:%M} UTC."
    return f"{len(runs)} runs between {first:%Y-%m-%d %H:%M} and {last:%Y-%m-%d %H:%M} UTC."


def _answered(runs: pd.DataFrame) -> str:
    answered = int((runs["outcome"] == "answered").sum())
    return f"{answered} of {len(runs)} runs answered ({fmt_pct(answered / len(runs))})."


def _crosstab(runs: pd.DataFrame, by: str) -> pd.DataFrame:
    table = pd.crosstab(runs[by].astype(str), runs["outcome"].astype(str))
    table["total"] = table.sum(axis=1)
    return table.reset_index().rename_axis(columns=None)


def _per_config(runs: pd.DataFrame) -> pd.DataFrame:
    table = _crosstab(runs, "config")
    span = runs.groupby(runs["config"].astype(str))["started_at"].agg(["min", "max"])
    table["first run"] = table["config"].map(span["min"].dt.strftime("%Y-%m-%d %H:%M"))
    table["last run"] = table["config"].map(span["max"].dt.strftime("%Y-%m-%d %H:%M"))
    return table.sort_values("total", ascending=False, kind="stable")


def _per_day(runs: pd.DataFrame) -> Chart:
    days = runs.assign(day=runs["started_at"].dt.strftime("%Y-%m-%d"))
    table = pd.crosstab(days["day"], days["outcome"].astype(str)).sort_index()
    fig, ax = canvas(height=2.6)
    bottom = pd.Series(0, index=table.index)
    for outcome, color in OUTCOME_COLORS.items():
        if outcome in table:
            ax.bar(
                table.index,
                table[outcome],
                bottom=bottom,
                color=color,
                label=outcome,
                width=0.6,
                edgecolor=SURFACE,
                linewidth=1.2,
            )
            bottom = bottom + table[outcome]
    # Keep bars narrow when only a few days have runs.
    ax.set_xlim(-0.5, max(len(table), 4) - 0.5)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("runs")
    legend(ax)
    return chart(fig, "Runs per day, by outcome")
