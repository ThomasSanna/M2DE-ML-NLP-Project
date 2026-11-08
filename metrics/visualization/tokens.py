"""Tokens and cache: how much each run sends to and receives from the model."""

import pandas as pd

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
            f"Mean prompt tokens grow from {by_step.iloc[0]:.0f} at step"
            f" {int(by_step.index[0])} to {by_step.iloc[-1]:.0f} at step {last}."
        )
    charts = []
    if not done.empty:
        charts.append(
            scatter(
                done["prompt_tokens"],
                done["completion_tokens"],
                xlabel="prompt tokens (all steps)",
                ylabel="completion tokens",
                caption="Prompt and completion tokens per answered run",
            )
        )
        charts.append(
            scatter(
                done["thread_chars"],
                done["prompt_tokens"],
                xlabel="thread length (characters)",
                ylabel="prompt tokens (all steps)",
                caption="Thread length against prompt tokens per answered run",
            )
        )
    if not by_step.empty:
        charts.append(
            bars(
                [f"step {int(step)}" for step in by_step.index],
                [round(float(v)) for v in by_step],
                xlabel="agent step",
                ylabel="mean prompt tokens",
                caption="Mean prompt tokens of a model call, by agent step",
            )
        )
    if not cached.empty:
        charts.append(
            histogram(
                cached["cache_rate"].dropna(),
                xlabel="cached share of prompt tokens",
                caption="Prefix cache rate per run",
                color=SERIES[2],
            )
        )
    return Section(
        "Tokens and cache", findings, charts, [Table("Tokens by step", _table(model_calls))]
    )


def _means(done: pd.DataFrame) -> str:
    if done.empty:
        return "No answered run: no token figures."
    return (
        f"Mean per answered run: {done['prompt_tokens'].mean():.0f} prompt and"
        f" {done['completion_tokens'].mean():.0f} completion tokens ({note(len(done))})."
    )


def _cache(cached: pd.DataFrame) -> str:
    if cached.empty:
        return (
            "Cached tokens are not reported: start vLLM with --enable-prompt-tokens-details"
            " to measure the prefix cache."
        )
    # Weighted by tokens: a long prompt served from the cache matters more than a short one.
    rate = cached["cached_tokens"].sum() / cached["prompt_tokens"].sum()
    return f"Cache rate: {fmt_pct(rate)} of prompt tokens ({note(len(cached))})."


def _table(model_calls: pd.DataFrame) -> pd.DataFrame:
    groups = model_calls.groupby("step")
    table = pd.DataFrame(
        {
            "calls": groups.size(),
            "mean prompt": groups["prompt_tokens"].mean().map(cell_num),
            "max prompt": groups["prompt_tokens"].max().map(cell_num),
            "mean completion": groups["completion_tokens"].mean().map(cell_num),
            "max completion": groups["completion_tokens"].max().map(cell_num),
        }
    )
    return table.rename_axis("step").reset_index()
