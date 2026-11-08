"""Report types, chart styling and the HTML page."""

import base64
import html
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

# Validated categorical palette for a light surface. Each entity keeps its color in every
# chart: colors follow the entity, never its rank.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e4e3df"
NEUTRAL = "#a3a29c"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
OUTCOME_COLORS = {
    "answered": SERIES[0],
    "embedding_error": SERIES[1],
    "reranker_error": SERIES[2],
    "llm_error": SERIES[3],
}
KIND_COLORS = {
    "embed": SERIES[0],
    "vector_search": SERIES[1],
    "rerank": SERIES[2],
    "llm": SERIES[3],
    "web_search": SERIES[4],
    "other": NEUTRAL,
}


@dataclass(frozen=True)
class Chart:
    caption: str
    png_base64: str


@dataclass(frozen=True)
class Table:
    caption: str
    frame: pd.DataFrame


@dataclass(frozen=True)
class Section:
    title: str
    findings: list[str]
    charts: list[Chart] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


def canvas(
    width: float = 7.0, height: float = 3.0, grid: Literal["x", "y"] = "y"
) -> tuple[Figure, Axes]:
    """A figure with recessive axes and grid, drawn without pyplot's global state."""
    fig = Figure(figsize=(width, height), facecolor=SURFACE, layout="constrained")
    ax = fig.add_subplot()
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.xaxis.label.set_color(INK_MUTED)
    ax.yaxis.label.set_color(INK_MUTED)
    ax.grid(axis=grid, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def legend(ax: Axes) -> None:
    ax.legend(
        frameon=False, fontsize=9, labelcolor=INK_MUTED, loc="upper left", bbox_to_anchor=(1, 1)
    )


def chart(fig: Figure, caption: str) -> Chart:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, facecolor=SURFACE)
    return Chart(caption=caption, png_base64=base64.b64encode(buffer.getvalue()).decode())


def render(
    sections: list[Section], *, source: Path, runs: int, calls: int, generated_at: datetime
) -> str:
    toc = "".join(
        f'<li><a href="#{_anchor(s.title)}">{html.escape(s.title)}</a></li>' for s in sections
    )
    return _PAGE.format(
        style=_STYLE,
        generated=f"{generated_at:%Y-%m-%d %H:%M} UTC",
        source=html.escape(str(source)),
        runs=runs,
        calls=calls,
        toc=toc,
        body="".join(_section(section) for section in sections),
    )


def _anchor(title: str) -> str:
    return title.lower().replace(" ", "-")


def _section(section: Section) -> str:
    findings = "".join(f"<li>{html.escape(text)}</li>" for text in section.findings)
    charts = "".join(
        f'<figure><img src="data:image/png;base64,{c.png_base64}" alt="{html.escape(c.caption)}">'
        f"<figcaption>{html.escape(c.caption)}</figcaption></figure>"
        for c in section.charts
    )
    tables = "".join(
        f'<div class="table"><h3>{html.escape(t.caption)}</h3>'
        f"{t.frame.to_html(index=False, escape=True, border=0, na_rep='-')}</div>"
        for t in section.tables
    )
    return (
        f'<section><h2 id="{_anchor(section.title)}">{html.escape(section.title)}</h2>'
        f'<ul class="findings">{findings}</ul>{charts}{tables}</section>'
    )


_STYLE = f"""
:root {{ color-scheme: light; }}
body {{ margin: 0; background: {SURFACE}; color: {INK};
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }}
h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
h2 {{ font-size: 1.25rem; margin: 40px 0 8px; padding-top: 8px; border-top: 1px solid {GRID}; }}
h3 {{ font-size: 0.95rem; margin: 16px 0 6px; color: {INK_MUTED}; }}
.meta, figcaption {{ color: {INK_MUTED}; font-size: 0.85rem; }}
nav ul {{ display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 0; list-style: none; }}
a {{ color: {SERIES[0]}; }}
.findings li {{ margin: 4px 0; }}
figure {{ margin: 16px 0; }}
img {{ max-width: 100%; height: auto; }}
.table {{ overflow-x: auto; }}
table {{ border-collapse: collapse; font-size: 0.85rem; font-variant-numeric: tabular-nums; }}
th, td {{ padding: 4px 10px; text-align: right; border-bottom: 1px solid {GRID}; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: {INK_MUTED}; font-weight: 600; }}
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI metrics report</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>AI metrics report</h1>
<p class="meta">Generated {generated} from {source}: {runs} runs, {calls} calls.
Every number shows its sample size; "indicative" marks fewer than 20 runs.</p>
<nav><ul>{toc}</ul></nav>
{body}
</main>
</body>
</html>
"""
