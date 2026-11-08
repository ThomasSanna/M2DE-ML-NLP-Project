"""Tokens and cache: how much each run sends to and receives from the model."""

import pandas as pd

from i18n import number, t
from load import Metrics, answered
from render import SERIES, Section, Table, bars, histogram, scatter
from stats import cell_num, fmt_pct, note


def build(metrics: Metrics) -> Section:
    done = answered(metrics.runs)
    model_calls = metrics.calls[
        (metrics.calls["kind"] == "llm")
        & (metrics.calls["status"] == "ok")
        & metrics.calls["prompt_tokens"].notna()
    ]
    by_step = model_calls.groupby("step")["prompt_tokens"].mean().sort_index()
    cached = metrics.runs[metrics.runs["cached_tokens"].notna()]
    findings = [_means(done), _cache(cached)]
    if len(by_step) > 1:
        last = int(by_step.index[-1])
        findings.append(
            t(
                "Mean prompt tokens grow from {first} at step {first_step} to {last}"
                " at step {last_step}.",
                first=number(by_step.iloc[0]),
                first_step=int(by_step.index[0]),
                last=number(by_step.iloc[-1]),
                last_step=last,
            )
        )
    charts = []
    if not done.empty:
        charts.append(
            scatter(
                done["prompt_tokens"],
                done["completion_tokens"],
                xlabel=t("prompt tokens (all steps)"),
                ylabel=t("completion tokens"),
                caption=t("Prompt and completion tokens per answered run"),
            )
        )
        charts.append(
            scatter(
                done["thread_chars"],
                done["prompt_tokens"],
                xlabel=t("thread length (characters)"),
                ylabel=t("prompt tokens (all steps)"),
                caption=t("Thread length against prompt tokens per answered run"),
            )
        )
    if not by_step.empty:
        charts.append(
            bars(
                [t("step {step}", step=int(step)) for step in by_step.index],
                [round(float(v)) for v in by_step],
                xlabel=t("agent step"),
                ylabel=t("mean prompt tokens"),
                caption=t("Mean prompt tokens of a model call, by agent step"),
            )
        )
    if not cached.empty:
        charts.append(
            histogram(
                cached["cache_rate"].dropna(),
                xlabel=t("cached share of prompt tokens"),
                caption=t("Prefix cache rate per run"),
                color=SERIES[2],
            )
        )
    return Section(
        t("Tokens and cache"), findings, charts, [Table(t("Tokens by step"), _table(model_calls))]
    )


def _means(done: pd.DataFrame) -> str:
    if done.empty:
        return t("No answered run: no token figures.")
    return t(
        "Mean per answered run: {prompt} prompt and {completion} completion tokens ({note}).",
        prompt=number(done["prompt_tokens"].mean()),
        completion=number(done["completion_tokens"].mean()),
        note=note(len(done)),
    )


def _cache(cached: pd.DataFrame) -> str:
    if cached.empty:
        return t(
            "Cached tokens are not reported: start vLLM with --enable-prompt-tokens-details"
            " to measure the prefix cache."
        )
    # Weighted by tokens: a long prompt served from the cache matters more than a short one.
    rate = cached["cached_tokens"].sum() / cached["prompt_tokens"].sum()
    return t(
        "Cache rate: {rate} of prompt tokens ({note}).",
        rate=fmt_pct(rate),
        note=note(len(cached)),
    )


def _table(model_calls: pd.DataFrame) -> pd.DataFrame:
    groups = model_calls.groupby("step")
    table = pd.DataFrame(
        {
            t("calls"): groups.size(),
            t("mean prompt"): groups["prompt_tokens"].mean().map(cell_num),
            t("max prompt"): groups["prompt_tokens"].max().map(cell_num),
            t("mean completion"): groups["completion_tokens"].mean().map(cell_num),
            t("max completion"): groups["completion_tokens"].max().map(cell_num),
        }
    )
    return table.rename_axis(t("step")).reset_index()
