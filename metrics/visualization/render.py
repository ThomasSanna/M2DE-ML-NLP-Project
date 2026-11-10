"""Report types and chart drawing; the HTML page itself is in page.py."""

import base64
import io
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, ScalarFormatter

from i18n import localize, t

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
    "escalated": SERIES[4],
    "embedding_error": SERIES[1],
    "reranker_error": SERIES[2],
    "llm_error": SERIES[3],
}
ACTION_COLORS = {
    "answer": SERIES[0],
    "search": SERIES[1],
    "escalate": SERIES[2],
    "invalid": SERIES[3],
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
    # How to read the chart: what one mark is, the codes it shows, and what to look for.
    reading: str


@dataclass(frozen=True)
class Tabs:
    """Charts shown one at a time, picked by name: the first one is shown first."""

    options: list[tuple[str, Chart]]


@dataclass(frozen=True)
class Criterion:
    """One way to narrow the runs: a key in the explorer data and its labeled choices."""

    key: str
    label: str
    options: list[tuple[str, str]]


@dataclass(frozen=True)
class Explorer:
    """Runs the reader filters in the browser by combining criteria; page.py draws it."""

    caption: str
    reading: str
    criteria: list[Criterion]
    # Read by explorer.js: numbers, colors and translated texts, all JSON-serializable.
    data: dict[str, object]


@dataclass(frozen=True)
class Table:
    caption: str
    frame: pd.DataFrame


@dataclass(frozen=True)
class Section:
    title: str
    findings: list[str]
    charts: list[Chart | Tabs | Explorer] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def all_charts(self) -> list[Chart]:
        """Every chart of the section, each option of a tab group included."""
        return [
            chart
            for item in self.charts
            for chart in (
                [c for _, c in item.options]
                if isinstance(item, Tabs)
                else [item]
                if isinstance(item, Chart)
                else []
            )
        ]


@dataclass(frozen=True)
class Tile:
    """A headline number at the top of a view."""

    label: str
    value: str
    note: str


@dataclass(frozen=True)
class View:
    """The whole report for one slice of the data, picked by name in the page filter."""

    name: str
    scope: str
    tiles: list[Tile]
    sections: list[Section]


def canvas(
    width: float = 6.0, height: float = 3.0, grid: Literal["x", "y"] = "y"
) -> tuple[Figure, Axes]:
    """A figure with recessive axes and grid, drawn without pyplot's global state."""
    fig = Figure(figsize=(width, height), facecolor=SURFACE, layout="constrained")
    ax = fig.add_subplot()
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=10)
    ax.xaxis.label.set_color(INK_MUTED)
    ax.yaxis.label.set_color(INK_MUTED)
    ax.grid(axis=grid, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def legend(ax: Axes) -> None:
    ax.legend(
        frameon=False, fontsize=10, labelcolor=INK_MUTED, loc="upper left", bbox_to_anchor=(1, 1)
    )


def histogram(
    values: pd.Series,
    *,
    xlabel: str,
    ylabel: str,
    caption: str,
    reading: str,
    color: str = SERIES[0],
    markers: list[tuple[float, str]] | None = None,
    bins: list[float] | None = None,
) -> Chart:
    """Distribution of one measure; markers draw labeled reference lines such as a threshold."""
    data = values.astype(float)
    fig, ax = canvas(height=2.6)
    ax.hist(
        data,
        bins=bins if bins is not None else bin_edges(data),
        color=color,
        edgecolor=SURFACE,
        linewidth=0.8,
    )
    for i, (value, label) in enumerate(markers or []):
        ax.axvline(value, color=INK, linewidth=1, linestyle="--")
        # Staggered heights keep the labels of close lines apart.
        ax.text(
            value,
            0.97 - 0.09 * i,
            f" {label}",
            transform=ax.get_xaxis_transform(),
            color=INK,
            fontsize=9,
            va="top",
            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1},
        )
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return chart(fig, caption, reading)


def bin_edges(values: pd.Series) -> list[float]:
    """Equal-width bin edges: one bin per value up to 30 bins, at least 5."""
    low, high = float(values.min()), float(values.max())
    # Float noise around one value leaves no room for distinct edges: widen the range.
    if high - low <= 1e-9 * max(1.0, abs(high)):
        low, high = low - 0.5, high + 0.5
    count = min(30, max(5, len(values)))
    return [low + (high - low) * i / count for i in range(count + 1)]


def bars(
    labels: list[str],
    values: list[float],
    *,
    xlabel: str,
    ylabel: str,
    caption: str,
    reading: str,
) -> Chart:
    """One series of vertical bars, each labeled with its value."""
    fig, ax = canvas(height=2.6)
    drawn = ax.bar(labels, values, color=SERIES[0], width=0.6)
    ax.bar_label(drawn, fmt="{:g}", color=INK_MUTED, fontsize=10, padding=2)
    # Keep bars narrow when there are only a few categories.
    ax.set_xlim(-0.5, max(len(labels), 4) - 0.5)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return chart(fig, caption, reading)


def boxes(
    groups: list[tuple[str, pd.Series]],
    *,
    xlabel: str,
    caption: str,
    reading: str,
    dots: str,
    ylabel: str = "",
) -> Chart:
    """Horizontal box plots, one per labeled group, on an axis that starts at zero."""
    fig, ax = canvas(height=0.5 * len(groups) + 1.2, grid="x")
    ax.boxplot(
        [values.astype(float).to_numpy() for _, values in groups],
        orientation="horizontal",
        tick_labels=[label for label, _ in groups],
        widths=0.5,
        patch_artist=True,
        boxprops={"facecolor": "#cde2fb", "edgecolor": SERIES[0]},
        whiskerprops={"color": SERIES[0]},
        capprops={"color": SERIES[0]},
        medianprops={"color": INK},
        # Outliers are drawn below with every other value; matplotlib's would draw them twice.
        showfliers=False,
    )
    # Every value as a dot: with a handful of runs the box alone hides the data.
    for row, (_, values) in enumerate(groups, start=1):
        ax.scatter(
            values.astype(float), [row] * len(values), s=14, color=SERIES[0], alpha=0.6, zorder=3
        )
    ax.invert_yaxis()
    # From zero, so a small spread does not look like a large difference between groups.
    ax.set_xlim(left=0)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    explained = t(
        "{reading} Box: middle half of the values; thick line: median; whiskers: the other"
        " values, up to 1.5 times the box length. {dots}",
        reading=reading,
        dots=dots,
    )
    return chart(fig, caption, explained)


def scatter(
    x: pd.Series, y: pd.Series, *, xlabel: str, ylabel: str, caption: str, reading: str
) -> Chart:
    fig, ax = canvas(height=3.0)
    ax.scatter(
        x.astype(float), y.astype(float), s=28, color=SERIES[0], edgecolors=SURFACE, linewidths=1
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return chart(fig, caption, reading)


class _LocalizedFormatter(ScalarFormatter):
    """Numeric tick labels with the decimal separator of the report language."""

    def __call__(self, x: float, pos: int | None = None) -> str:
        return localize(super().__call__(x, pos))


def chart(fig: Figure, caption: str, reading: str) -> Chart:
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            # Only numeric axes: category and fixed tick labels are words, not numbers.
            if type(axis.get_major_formatter()) is ScalarFormatter:
                axis.set_major_formatter(_LocalizedFormatter())
    buffer = io.BytesIO()
    # Twice the CSS pixel density, so charts stay sharp on high-density screens.
    fig.savefig(buffer, format="png", dpi=200, facecolor=SURFACE)
    png = base64.b64encode(buffer.getvalue()).decode()
    return Chart(caption=caption, png_base64=png, reading=reading)
