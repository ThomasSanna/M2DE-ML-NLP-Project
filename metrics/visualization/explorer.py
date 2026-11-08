"""Total duration of answered runs, filtered in the browser by criteria the reader combines.

Python writes one row per answered run; explorer.js filters the rows and draws the charts.
"""

import html
import json
from pathlib import Path

import pandas as pd

from i18n import current, t
from load import Metrics, answered, spent_by_kind
from render import (
    GRID,
    INK,
    INK_MUTED,
    KIND_COLORS,
    SERIES,
    SURFACE,
    Criterion,
    Explorer,
    bin_edges,
)

SCRIPT = (Path(__file__).resolve().parent / "explorer.js").read_text(encoding="utf-8")


def build(metrics: Metrics) -> Explorer | None:
    done = answered(metrics.runs)
    if done.empty:
        return None
    spent = spent_by_kind(done, metrics.calls)
    whichever = t("Any")
    return Explorer(
        caption=t("Total duration of an answered run, from the question to the answer"),
        reading=t(
            "Each answered run counts once with all its calls: memory search, every model step,"
            " web searches, and the time the API spends between them. Combine the criteria to"
            " keep some runs only. Histogram: gray bars are every answered run of this view,"
            " colored bars the selection, dashed lines its median and p95. Split: the mean time"
            " of a selected run cut by call kind, next to the mean of every run."
        ),
        criteria=[
            Criterion(
                "web",
                t("Web search"),
                [("all", whichever), ("yes", t("Used")), ("no", t("Not used"))],
            ),
            Criterion(
                "thread",
                t("Thread"),
                [("all", whichever), ("yes", t("Follow-up")), ("no", t("First question"))],
            ),
            Criterion(
                "memory",
                t("Memory"),
                [("all", whichever), ("yes", t("Entries shown")), ("no", t("No entry"))],
            ),
        ],
        data={
            "lang": current(),
            "kinds": [
                {
                    "name": kind,
                    "label": t("other") if kind == "other" else kind,
                    "color": KIND_COLORS[kind],
                }
                for kind in spent.columns
            ],
            "edges": bin_edges(done["total_ms"] / 1000),
            "runs": {
                "total_ms": [float(total) for total in done["total_ms"]],
                "spent_ms": [[float(ms) for ms in row] for row in spent.itertuples(index=False)],
                "web": _flags(done["web_ms"] > 0),
                "thread": _flags(done["prior_replies"] > 0),
                "memory": _flags(done["memory_kept"] > 0),
            },
            "colors": {"selected": SERIES[0], "all": GRID, "ink": INK, "muted": INK_MUTED},
            # Templates keep their {placeholders} for the script to fill.
            "text": {
                "count": t("{n} of {total} answered runs", n="{n}", total="{total}"),
                "median": t("median {value}", value="{value}"),
                "p95": t("p95 {value}", value="{value}"),
                "indicative": t("{value} (indicative)", value="{value}"),
                "none": t("No answered run matches these criteria."),
                "axis": t("total duration per answered run (s)"),
                "runs": t("runs"),
                "bin": t("{range}: {n} selected of {all}", range="{range}", n="{n}", all="{all}"),
                "split": t("mean seconds per answered run, by call kind"),
                "selected": t("Selection"),
                "every": t("All"),
            },
        },
    )


def markup(group: str, explorer: Explorer) -> str:
    """The criteria as radio groups, the charts the script fills, and the data it reads."""
    criteria = "".join(
        f'<fieldset data-key="{criterion.key}"><legend>{html.escape(criterion.label)}</legend>'
        '<div class="tab-list">'
        + "".join(
            f'<input type="radio" name="{group}-{criterion.key}"'
            f' id="{group}-{criterion.key}-{value}" value="{value}"'
            f"{' checked' if position == 0 else ''}>"
            f'<label for="{group}-{criterion.key}-{value}">{html.escape(label)}</label>'
            for position, (value, label) in enumerate(criterion.options)
        )
        + "</div></fieldset>"
        for criterion in explorer.criteria
    )
    caption = html.escape(explorer.caption)
    split = html.escape(t("mean seconds per answered run, by call kind"))
    # "</" would end the script element early: JSON allows escaping the slash.
    data = json.dumps(explorer.data, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return (
        f'<figure class="explorer"><h3>{caption}</h3><form class="criteria">{criteria}</form>'
        f'<p class="explorer-summary" aria-live="polite">'
        f"{html.escape(t('This chart needs JavaScript.'))}</p>"
        f'<div class="explorer-charts"><svg class="explorer-histogram" role="img"'
        f' aria-label="{caption}"></svg><svg class="explorer-split" role="img"'
        f' aria-label="{split}"></svg></div>'
        f"<figcaption>{html.escape(explorer.reading)}</figcaption>"
        f'<script type="application/json" class="explorer-data">{data}</script></figure>'
    )


STYLE = f"""
.explorer {{ grid-column: 1 / -1; }}
.criteria {{ display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 0 0 10px; }}
.criteria fieldset {{ border: 0; margin: 0; padding: 0; min-width: 0; }}
.criteria legend {{ float: left; padding: 6px 8px 0 0; color: {INK_MUTED}; font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.04em; }}
.explorer-summary {{ font-weight: 600; margin: 0 0 8px; font-variant-numeric: tabular-nums; }}
.explorer-charts {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }}
@media (min-width: 1100px) {{
  .explorer-charts {{ grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }} }}
.explorer svg {{ display: block; width: 100%; height: auto; }}
.explorer svg text {{ fill: {INK_MUTED}; font-size: 13px; font-variant-numeric: tabular-nums; }}
.explorer svg text.mark {{ fill: {INK}; paint-order: stroke; stroke: {SURFACE};
  stroke-width: 4px; }}
"""


def _flags(condition: pd.Series) -> list[bool]:
    # Missing values (a column the API left empty) count as "no".
    return [bool(flag) for flag in condition.fillna(False)]
