"""Agent: steps, model outputs and web searches."""

import pandas as pd
from matplotlib.ticker import MaxNLocator

from i18n import number, t
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
                xlabel=t("model calls in the run"),
                ylabel=t("answered runs"),
                caption=t("Steps per answered run"),
                reading=t(
                    "Number of model calls an answered run needed. The agent stops at its step"
                    " limit (agent_max_steps): a tall last bar means runs used every step allowed."
                ),
            )
        )
    if not model_calls.empty:
        charts.append(_actions(model_calls))
    if any(searches.values()):
        labels = {
            "ok": t("ok"),
            "failed": t("failed"),
            "duplicate": t("duplicate"),
            "unavailable": t("unavailable"),
        }
        charts.append(
            bars(
                [labels[name] for name in searches],
                list(searches.values()),
                xlabel=t("web search"),
                ylabel=t("searches"),
                caption=t("Web searches asked by the model, by outcome"),
                reading=t(
                    "Searches the model asked for. ok: sent to Exa and answered; failed: sent but"
                    " Exa returned an error; duplicate: same query as earlier in the run, not sent"
                    " again; unavailable: web search disabled, nothing sent."
                ),
            )
        )
    table = Table(t("Model calls by step and action"), _action_table(model_calls))
    return Section(t("Agent"), findings, charts, [table])


def _steps(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return []
    first = int((done["steps"] == 1).sum())
    return [
        t(
            "{first} of {answered} answered runs answered at step 1 ({pct}, {note}).",
            first=first,
            answered=len(done),
            pct=fmt_pct(first / len(done)),
            note=note(len(done)),
        )
    ]


def _outputs(model_calls: pd.DataFrame) -> list[str]:
    if model_calls.empty:
        return [t("No model call in the data.")]
    n = len(model_calls)
    invalid = int((model_calls["action"] == "invalid").sum())
    cut = int((model_calls["finish_reason"] == "length").sum())
    return [
        t(
            "Invalid model outputs: {invalid} of {calls} model calls ({pct}).",
            invalid=invalid,
            calls=n,
            pct=fmt_pct(invalid / n),
        ),
        t("Responses cut by max_tokens: {cut} of {calls} model calls.", cut=cut, calls=n),
    ]


def _searches(searches: dict[str, int], runs: int, costs: pd.Series) -> str:
    if not any(searches.values()) and costs.empty:
        return t("No web search in the data.")
    sent = searches["ok"] + searches["failed"]
    return t(
        "Web searches: {ok} ok, {failed} failed, {duplicate} duplicate,"
        " {unavailable} unavailable, {per_run} per run ({note}).",
        **searches,
        per_run=number(sent / runs, ".2f"),
        note=note(runs),
    )


def _cost(costs: pd.Series) -> list[str]:
    if costs.empty:
        return []
    return [
        t(
            "Exa cost: ${total} in total, ${per_run} per run with a search.",
            total=number(costs.sum(), ".4f"),
            per_run=number(costs.mean(), ".4f"),
        )
    ]


def _dropped(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return []
    dropped = int((done["dropped_refs"].fillna(0) > 0).sum())
    return [
        t(
            "Runs with dropped refs (cited but never shown to the model): {dropped} of {answered}.",
            dropped=dropped,
            answered=len(done),
        )
    ]


def _actions(model_calls: pd.DataFrame) -> Chart:
    table = pd.crosstab(model_calls["step"].astype(int), model_calls["action"].astype(str))
    fig, ax = canvas(height=2.6)
    bottom = pd.Series(0, index=table.index)
    labels = [t("step {step}", step=step) for step in table.index]
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
    ax.set_ylabel(t("model calls"))
    legend(ax)
    return chart(
        fig,
        t("What the model did at each step"),
        t(
            "What each model call asked for. answer: the final reply; search: a web search,"
            " followed by another step; invalid: an output that does not follow the expected"
            " format, which costs a step."
        ),
    )


def _action_table(model_calls: pd.DataFrame) -> pd.DataFrame:
    rows = [
        (int(step), len(group), listing(counts(group["action"])))
        for step, group in model_calls.groupby("step")
    ]
    return pd.DataFrame(rows, columns=[t("step"), t("calls"), t("actions")])
