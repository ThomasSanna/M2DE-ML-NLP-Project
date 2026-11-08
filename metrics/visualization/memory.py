"""Memory and thread: what retrieval kept, what answers rest on, and follow-up questions."""

import pandas as pd

from i18n import number, t
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
                xlabel=t("memory entries kept after the rerank threshold"),
                ylabel=t("runs"),
                caption=t("Memory entries kept per run"),
                reading=t(
                    "How many stored answers passed the rerank threshold and were shown to the"
                    " model. 0: the model answered without the memory."
                ),
            )
        )
    scores = runs["memory_top_score"].dropna()
    if not scores.empty:
        charts.append(
            histogram(
                scores,
                xlabel=t("best rerank score of the run"),
                ylabel=t("runs"),
                caption=t("Best rerank score per run against the threshold"),
                reading=t(
                    "Best score the reranker gave to the run's memory candidates, from 0"
                    " (unrelated) to 1 (same question). Entries right of their configuration's"
                    " dashed line are kept; many runs just left of it suggest a threshold that is"
                    " too strict."
                ),
                markers=thresholds(runs),
                # Fixed 0.025-wide bins over [0, 1]: scores near 1 stay visible.
                bins=[i / 40 for i in range(41)],
            )
        )
    if not done.empty:
        shares = done["grounding"].value_counts().reindex(_GROUNDING, fill_value=0)
        names = {
            "memory": t("memory"),
            "web": t("web"),
            "memory+web": t("memory+web"),
            "none": t("none"),
        }
        charts.append(
            bars(
                [names[grounding] for grounding in _GROUNDING],
                [int(n) for n in shares],
                xlabel=t("sources cited by the answer"),
                ylabel=t("answered runs"),
                caption=t("What answers are grounded in"),
                reading=t(
                    "What the final answer cites: memory entries, web results, both, or no"
                    " source at all."
                ),
            )
        )
        by_replies = _by_replies(done)
        charts.append(
            boxes(
                [(label, group["total_ms"] / 1000) for label, group in by_replies],
                xlabel=t("total duration per answered run (s)"),
                caption=t("Total duration by replies already in the thread"),
                reading=t(
                    "Runs grouped by the number of support replies already in the ticket"
                    " (0: first question, 3+: three or more). Shows whether a longer conversation"
                    " slows the answer."
                ),
                ylabel=t("prior replies"),
            )
        )
        charts.append(
            boxes(
                [(label, group["prompt_tokens"]) for label, group in by_replies],
                xlabel=t("prompt tokens per answered run"),
                caption=t("Prompt tokens by replies already in the thread"),
                reading=t(
                    "Same grouping. Shows whether a longer conversation makes the prompt grow."
                ),
                ylabel=t("prior replies"),
            )
        )
    return Section(
        t("Memory and thread"),
        findings,
        charts,
        [Table(t("Answered runs by replies already in the thread"), _replies_table(done))],
    )


def thresholds(runs: pd.DataFrame) -> list[tuple[float, str]]:
    """One line per rerank threshold, named after the configurations that use it."""
    used = runs.dropna(subset=["rerank_threshold"]).groupby("rerank_threshold")["config_id"]
    return [
        (
            float(threshold),
            t(
                "{configs}: {threshold}",
                configs=", ".join(sorted(ids.unique(), key=lambda config: int(config[1:]))),
                threshold=number(threshold, "g"),
            ),
        )
        for threshold, ids in used
    ]


def _kept(retrieved: pd.DataFrame) -> str:
    if retrieved.empty:
        return t("No memory retrieval in the data.")
    none = int((retrieved["memory_kept"] == 0).sum())
    n = len(retrieved)
    return t(
        "{none} of {runs} runs kept no memory entry ({pct}, {note}).",
        none=none,
        runs=n,
        pct=fmt_pct(none / n),
        note=note(n),
    )


def _near(runs: pd.DataFrame) -> str:
    score, threshold = runs["memory_top_score"], runs["rerank_threshold"]
    near = int(((score >= threshold - NEAR_THRESHOLD) & (score < threshold)).fillna(False).sum())
    margin = number(NEAR_THRESHOLD, "g")
    if near == 0:
        return t("No run scored within {margin} below the threshold.", margin=margin)
    if near == 1:
        return t(
            "1 run scored within {margin} below the threshold: a lower threshold would"
            " admit its best entry.",
            margin=margin,
        )
    return t(
        "{runs} runs scored within {margin} below the threshold: a lower threshold"
        " would admit their best entries.",
        runs=near,
        margin=margin,
    )


def _grounding(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return [t("No answered run: no grounding figures.")]
    share = done["grounding"].value_counts(normalize=True).reindex(_GROUNDING, fill_value=0.0)
    return [
        t(
            "Answers grounded in memory {memory}, web {web}, both {both}, none {none} ({note}).",
            memory=fmt_pct(share["memory"]),
            web=fmt_pct(share["web"]),
            both=fmt_pct(share["memory+web"]),
            none=fmt_pct(share["none"]),
            note=note(len(done)),
        )
    ]


def _follow_ups(done: pd.DataFrame) -> list[str]:
    if done.empty:
        return []
    first = done[done["prior_replies"] == 0]
    follow = done[done["prior_replies"] > 0]
    if follow.empty:
        return [t("No follow-up question in the data.")]
    parts = []
    if not first.empty:
        parts.append(
            t(
                "{total} for first questions ({note})",
                total=fmt_ms(first["total_ms"].median()),
                note=note(len(first)),
            )
        )
    parts.append(
        t(
            "{total} for follow-ups ({note})",
            total=fmt_ms(follow["total_ms"].median()),
            note=note(len(follow)),
        )
    )
    return [t("Median total: {parts}.", parts=", ".join(parts))]


def _by_replies(done: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Answered runs grouped by prior replies: 0, 1, 2 and 3+."""
    bucket = done["prior_replies"].clip(upper=3)
    return [
        ("3+" if replies == 3 else str(replies), group) for replies, group in done.groupby(bucket)
    ]


def _replies_table(done: pd.DataFrame) -> pd.DataFrame:
    rows = [
        (
            label,
            len(group),
            cell_ms(group["total_ms"].median()),
            cell_num(group["prompt_tokens"].mean()),
            cell_num(group["thread_chars"].median()),
            fmt_pct(group["grounding"].isin(["memory", "memory+web"]).mean()),
        )
        for label, group in _by_replies(done)
    ]
    columns = [
        t("prior replies"),
        t("runs"),
        t("median total"),
        t("mean prompt tokens"),
        t("median thread chars"),
        t("grounded in memory"),
    ]
    return pd.DataFrame(rows, columns=columns)
