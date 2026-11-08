"""Memory and thread: what retrieval kept, what answers rest on, and follow-up questions."""

import pandas as pd

from load import Metrics, answered
from render import Section, Table, bars, boxes, histogram
from stats import cell_ms, cell_num, fmt_ms, fmt_pct, note

# Best scores this close below the rerank threshold hint that the threshold drops useful entries.
NEAR_THRESHOLD = 0.05
_GROUNDING = ("memory", "web", "memory+web", "none")


def build(metrics: Metrics) -> Section:
    runs = metrics.runs
    retrieved = runs[runs["memory_kept"].notna()]
    done = answered(runs)
    findings = [_kept(retrieved), _near(runs), *_grounding(done), *_follow_ups(done)]
    charts = []
    if not retrieved.empty:
        most = int(max(retrieved["retrieval_top_k"].max(), retrieved["memory_kept"].max()))
        kept = retrieved["memory_kept"].value_counts().reindex(range(most + 1), fill_value=0)
        charts.append(
            bars(
                [str(k) for k in kept.index],
                [int(n) for n in kept],
                xlabel="memory entries kept after the rerank threshold",
                ylabel="runs",
                caption="Memory entries kept per run",
            )
        )
    scores = runs["memory_top_score"].dropna()
    if not scores.empty:
        thresholds = tuple(float(t) for t in runs["rerank_threshold"].dropna().unique())
        charts.append(
            histogram(
                scores,
                xlabel="best rerank score of the run (dashed: rerank threshold)",
                caption="Best rerank score per run against the threshold",
                markers=thresholds,
                # Fixed 0.025-wide bins over [0, 1]: scores near 1 stay visible.
                bins=[i / 40 for i in range(41)],
            )
        )
    if not done.empty:
        shares = done["grounding"].value_counts().reindex(_GROUNDING, fill_value=0)
        charts.append(
            bars(
                list(_GROUNDING),
                [int(n) for n in shares],
                xlabel="sources cited by the answer",
                ylabel="answered runs",
                caption="What answers are grounded in",
            )
        )
        by_replies = _by_replies(done)
        charts.append(
            boxes(
                [(label, group["total_ms"]) for label, group in by_replies],
                xlabel="total ms per answered run",
                caption="Total duration by replies already in the thread",
                ylabel="prior replies",
            )
        )
        charts.append(
            boxes(
                [(label, group["prompt_tokens"]) for label, group in by_replies],
                xlabel="prompt tokens per answered run",
                caption="Prompt tokens by replies already in the thread",
                ylabel="prior replies",
            )
        )
    return Section(
        "Memory and thread",
        findings,
        charts,
        [Table("Answered runs by replies already in the thread", _replies_table(done))],
    )


def _kept(retrieved: pd.DataFrame) -> str:
    if retrieved.empty:
        return "No memory retrieval in the data."
    none = int((retrieved["memory_kept"] == 0).sum())
    n = len(retrieved)
    return f"{none} of {n} runs kept no memory entry ({fmt_pct(none / n)}, {note(n)})."


def _near(runs: pd.DataFrame) -> str:
    score, threshold = runs["memory_top_score"], runs["rerank_threshold"]
    near = int(((score >= threshold - NEAR_THRESHOLD) & (score < threshold)).fillna(False).sum())
    if near == 0:
        return f"No run scored within {NEAR_THRESHOLD} below the threshold."
    if near == 1:
        return (
            f"1 run scored within {NEAR_THRESHOLD} below the threshold: a lower threshold would"
            " admit its best entry."
        )
    return (
        f"{near} runs scored within {NEAR_THRESHOLD} below the threshold: a lower threshold"
        " would admit their best entries."
    )


def _grounding(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return ["No answered run: no grounding figures."]
    share = done["grounding"].value_counts(normalize=True).reindex(_GROUNDING, fill_value=0.0)
    return [
        f"Answers grounded in memory {fmt_pct(share['memory'])}, web {fmt_pct(share['web'])},"
        f" both {fmt_pct(share['memory+web'])}, none {fmt_pct(share['none'])}"
        f" ({note(len(done))})."
    ]


def _follow_ups(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return []
    first = done[done["prior_replies"] == 0]
    follow = done[done["prior_replies"] > 0]
    if follow.empty:
        return ["No follow-up question in the data."]
    parts = [
        f"{fmt_ms(group['total_ms'].median())} for {name} ({note(len(group))})"
        for name, group in (("first questions", first), ("follow-ups", follow))
        if not group.empty
    ]
    return [f"Median total: {', '.join(parts)}."]


def _by_replies(done: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Answered runs grouped by prior replies: 0, 1, 2 and 3+."""
    bucket = done["prior_replies"].clip(upper=3)
    return [
        ("3+" if replies == 3 else str(replies), group) for replies, group in done.groupby(bucket)
    ]


def _replies_table(done: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "prior replies": label,
            "runs": len(group),
            "median total": cell_ms(group["total_ms"].median()),
            "mean prompt tokens": cell_num(group["prompt_tokens"].mean()),
            "median thread chars": cell_num(group["thread_chars"].median()),
            "grounded in memory": fmt_pct(group["grounding"].isin(["memory", "memory+web"]).mean()),
        }
        for label, group in _by_replies(done)
    ]
    columns = [
        "prior replies",
        "runs",
        "median total",
        "mean prompt tokens",
        "median thread chars",
        "grounded in memory",
    ]
    return pd.DataFrame(rows, columns=columns)
