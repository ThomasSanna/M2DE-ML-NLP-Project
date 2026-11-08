"""Agent: steps, model outputs and web searches."""

import pandas as pd
from matplotlib.ticker import MaxNLocator

from load import Metrics, answered
from render import ACTION_COLORS, SURFACE, Chart, Section, Table, bars, canvas, chart, legend
from stats import counts, fmt_pct, listing, note

_SEARCH_COLUMNS = {
    "ok": "searches",
    "failed": "searches_failed",
    "duplicate": "searches_duplicate",
    "unavailable": "searches_unavailable",
}


def build(metrics: Metrics) -> Section:
    runs, calls = metrics.runs, metrics.calls
    done = answered(runs)
    model_calls = calls[(calls["kind"] == "llm") & (calls["status"] == "ok")]
    searches = {name: int(runs[column].sum()) for name, column in _SEARCH_COLUMNS.items()}
    costs = runs["web_cost_usd"].dropna()
    findings = [
        *_steps(done),
        *_outputs(model_calls),
        _searches(searches, len(runs), costs),
        *_cost(costs),
        *_dropped(done),
    ]
    charts = []
    if not done.empty:
        # Every possible step count shows, so an unused step reads as zero, not as missing.
        most = int(max(done["agent_max_steps"].max(), done["steps"].max()))
        steps = done["steps"].value_counts().reindex(range(1, most + 1), fill_value=0)
        charts.append(
            bars(
                [str(step) for step in steps.index],
                [int(n) for n in steps],
                xlabel="model calls in the run",
                ylabel="answered runs",
                caption="Steps per answered run",
            )
        )
    if not model_calls.empty:
        charts.append(_actions(model_calls))
    if any(searches.values()):
        charts.append(
            bars(
                list(searches),
                list(searches.values()),
                xlabel="web search",
                ylabel="searches",
                caption="Web searches asked by the model, by outcome",
            )
        )
    table = Table("Model calls by step and action", _action_table(model_calls))
    return Section("Agent", findings, charts, [table])


def _steps(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return []
    first = int((done["steps"] == 1).sum())
    return [
        f"{first} of {len(done)} answered runs answered at step 1"
        f" ({fmt_pct(first / len(done))}, {note(len(done))})."
    ]


def _outputs(model_calls: pd.DataFrame) -> list[str]:
    if model_calls.empty:
        return ["No model call in the data."]
    n = len(model_calls)
    invalid = int((model_calls["action"] == "invalid").sum())
    cut = int((model_calls["finish_reason"] == "length").sum())
    return [
        f"Invalid model outputs: {invalid} of {n} model calls ({fmt_pct(invalid / n)}).",
        f"Responses cut by max_tokens: {cut} of {n} model calls.",
    ]


def _searches(searches: dict[str, int], runs: int, costs: pd.Series) -> str:
    if not any(searches.values()) and costs.empty:
        return "No web search in the data."
    sent = searches["ok"] + searches["failed"]
    return (
        f"Web searches: {searches['ok']} ok, {searches['failed']} failed,"
        f" {searches['duplicate']} duplicate, {searches['unavailable']} unavailable,"
        f" {sent / runs:.2f} per run ({note(runs)})."
    )


def _cost(costs: pd.Series) -> list[str]:
    if costs.empty:
        return []
    return [f"Exa cost: ${costs.sum():.4f} in total, ${costs.mean():.4f} per run with a search."]


def _dropped(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return []
    dropped = int((done["dropped_refs"].fillna(0) > 0).sum())
    return [
        f"Runs with dropped refs (cited but never shown to the model): {dropped} of {len(done)}."
    ]


def _actions(model_calls: pd.DataFrame) -> Chart:
    table = pd.crosstab(model_calls["step"].astype(int), model_calls["action"].astype(str))
    fig, ax = canvas(height=2.6)
    bottom = pd.Series(0, index=table.index)
    labels = [f"step {step}" for step in table.index]
    for action, color in ACTION_COLORS.items():
        if action in table:
            ax.bar(
                labels,
                table[action],
                bottom=bottom,
                color=color,
                label=action,
                width=0.6,
                edgecolor=SURFACE,
                linewidth=1.2,
            )
            bottom = bottom + table[action]
    ax.set_xlim(-0.5, max(len(labels), 4) - 0.5)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("model calls")
    legend(ax)
    return chart(fig, "What the model did at each step")


def _action_table(model_calls: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"step": int(step), "calls": len(group), "actions": listing(counts(group["action"]))}
        for step, group in model_calls.groupby("step")
    ]
    return pd.DataFrame(rows, columns=["step", "calls", "actions"])
