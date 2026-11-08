"""The HTML page: a filter over the views, headline tiles, and one card per section.

Filters and tabs are radio buttons styled by CSS; only the explorer runs a script.
"""

import html
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

import explorer
from i18n import current, t
from render import (
    GRID,
    INK,
    INK_MUTED,
    SERIES,
    SURFACE,
    Chart,
    Explorer,
    Section,
    Table,
    Tabs,
    View,
)

# One number as stats.py writes it, optionally with a unit and a tag: "2 924", "67 %", "3,5 s
# (indicatif)". \s also matches the no-break spaces of French typography.
_NUMBER = re.compile(r"[+-]?\d[\d\s,.]*(\s?%|\sms|\ss)?(\s\(.+\))?")
_NUM = ' class="num"'


def render(
    views: list[View], *, source: Path, runs: int, calls: int, generated_at: datetime
) -> str:
    generated = t(
        "Generated {generated} from {source}: {runs} runs, {calls} calls.",
        generated=f"{generated_at:%Y-%m-%d %H:%M} UTC",
        source=source,
        runs=runs,
        calls=calls,
    )
    sample = t('Every number shows its sample size; "indicative" marks fewer than 20 runs.')
    tab_options = max(
        (
            len(item.options)
            for view in views
            for section in view.sections
            for item in section.charts
            if isinstance(item, Tabs)
        ),
        default=0,
    )
    explores = any(
        isinstance(item, Explorer)
        for view in views
        for section in view.sections
        for item in section.charts
    )
    return _PAGE.format(
        lang=current(),
        title=html.escape(t("AI metrics report")),
        style=_STYLE + (explorer.STYLE if explores else "") + _selection(len(views), tab_options),
        script=f"<script>{explorer.SCRIPT}</script>" if explores else "",
        filter=_filter(views),
        navs="".join(_nav(index, view) for index, view in enumerate(views)),
        meta=html.escape(f"{generated} {sample}"),
        views="".join(_view(index, view) for index, view in enumerate(views)),
    )


def _filter(views: list[View]) -> str:
    if len(views) < 2:
        return ""
    radios = "".join(
        f'<input type="radio" name="view" id="view-{index}"{" checked" if index == 0 else ""}>'
        f'<label for="view-{index}" title="{html.escape(view.scope)}">'
        f"{html.escape(view.name)}</label>"
        for index, view in enumerate(views)
    )
    label = html.escape(t("View"))
    return (
        f'<span class="filter-label">{label}</span>'
        f'<div class="filter" role="radiogroup" aria-label="{label}">{radios}</div>'
    )


def _nav(index: int, view: View) -> str:
    links = "".join(
        f'<a href="#{_anchor(index, section)}">{html.escape(section.title)}</a>'
        for section in view.sections
    )
    return f"<nav>{links}</nav>"


def _anchor(index: int, section: Section) -> str:
    return f"v{index}-" + re.sub(r"\W+", "-", section.title.lower()).strip("-")


def _view(index: int, view: View) -> str:
    tiles = "".join(
        f'<div class="tile"><div class="tile-label">{html.escape(tile.label)}</div>'
        f'<div class="tile-value">{html.escape(tile.value)}</div>'
        f'<div class="tile-note">{html.escape(tile.note)}</div></div>'
        for tile in view.tiles
    )
    sections = "".join(_section(index, section) for section in view.sections)
    return (
        f'<article class="view"><p class="scope">{html.escape(view.scope)}</p>'
        f'<div class="tiles">{tiles}</div>{sections}</article>'
    )


def _section(index: int, section: Section) -> str:
    anchor = _anchor(index, section)
    findings = "".join(f"<li>{html.escape(text)}</li>" for text in section.findings)
    charts = "".join(
        _tabs(f"{anchor}-{position}", item)
        if isinstance(item, Tabs)
        else explorer.markup(f"{anchor}-{position}", item)
        if isinstance(item, Explorer)
        else _figure(item)
        for position, item in enumerate(section.charts)
    )
    tables = "".join(_table(table) for table in section.tables)
    return (
        f'<section class="card"><h2 id="{anchor}">{html.escape(section.title)}</h2>'
        f'<div class="findings"><h3>{html.escape(t("Key findings"))}</h3><ul>{findings}</ul></div>'
        f'<div class="charts">{charts}</div>{tables}</section>'
    )


def _figure(chart: Chart) -> str:
    return (
        f"<figure><h3>{html.escape(chart.caption)}</h3>"
        f'<img src="data:image/png;base64,{chart.png_base64}" alt="{html.escape(chart.caption)}">'
        f"<figcaption>{html.escape(chart.reading)}</figcaption></figure>"
    )


def _tabs(group: str, tabs: Tabs) -> str:
    radios = "".join(
        f'<input type="radio" name="{group}" id="{group}-{position}"'
        f"{' checked' if position == 0 else ''}>"
        f'<label for="{group}-{position}">{html.escape(name)}</label>'
        for position, (name, _) in enumerate(tabs.options)
    )
    panels = "".join(f'<div class="panel">{_figure(chart)}</div>' for _, chart in tabs.options)
    return (
        f'<div class="tabs"><div class="tab-list" role="radiogroup">{radios}</div>'
        f'<div class="panels">{panels}</div></div>'
    )


def _table(table: Table) -> str:
    cells = table.frame.map(lambda value: "-" if pd.isna(value) else str(value))
    numeric = [
        all(value == "-" or _NUMBER.fullmatch(value) for value in cells[column])
        for column in cells.columns
    ]
    head = "".join(
        f"<th{_NUM if is_number else ''}>{html.escape(str(column))}</th>"
        for column, is_number in zip(cells.columns, numeric, strict=True)
    )
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td{_NUM if is_number else ''}>{html.escape(value)}</td>"
            for value, is_number in zip(row, numeric, strict=True)
        )
        + "</tr>"
        for row in cells.itertuples(index=False)
    )
    return (
        f'<div class="table"><h3>{html.escape(table.caption)}</h3><div class="scroll">'
        f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div></div>"
    )


def _selection(views: int, tab_options: int) -> str:
    """Show the view and the tab whose radio button is checked.

    Browsers without :has() skip the whole block and show every view and tab one below the
    other: nothing is hidden from them.
    """
    rules = [
        ".views > article, .navs > nav, .panels > .panel { display: none; }",
        ".views > article:only-of-type, .panels > .panel:only-child { display: block; }",
        ".navs > nav:only-of-type { display: flex; }",
    ]
    for n in range(1, views + 1):
        checked = f"body:has(.filter > input:nth-of-type({n}):checked)"
        rules.append(f"{checked} .views > article:nth-of-type({n}) {{ display: block; }}")
        rules.append(f"{checked} .navs > nav:nth-of-type({n}) {{ display: flex; }}")
    for n in range(1, tab_options + 1):
        rules.append(
            f".tabs:has(> .tab-list > input:nth-of-type({n}):checked)"
            f" > .panels > .panel:nth-child({n}) {{ display: block; }}"
        )
    return "@supports selector(:has(*)) {\n" + "\n".join(rules) + "\n}\n"


_ACCENT = SERIES[0]
_STYLE = f"""
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f3f2ee; color: {INK};
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
.bar {{ position: sticky; top: 0; z-index: 10; background: rgb(252 252 251 / 0.94);
  backdrop-filter: blur(8px); border-bottom: 1px solid {GRID}; }}
.bar-inner {{ max-width: 1280px; margin: 0 auto; padding: 10px 16px; display: flex;
  flex-wrap: wrap; align-items: center; gap: 8px 12px; }}
.filter-label {{ color: {INK_MUTED}; font-size: 0.8rem; text-transform: uppercase;
  letter-spacing: 0.04em; }}
.filter, .tab-list {{ display: inline-flex; flex-wrap: wrap; gap: 2px; padding: 3px;
  background: #e9e8e3; border-radius: 9px; }}
.filter {{ margin-right: 16px; }}
.filter input, .tab-list input {{ position: absolute; opacity: 0; pointer-events: none; }}
.filter label, .tab-list label {{ padding: 4px 12px; border-radius: 7px; cursor: pointer;
  font-size: 0.875rem; color: {INK_MUTED}; white-space: nowrap; }}
.filter label:hover, .tab-list label:hover {{ color: {INK}; }}
.filter input:checked + label, .tab-list input:checked + label {{ background: {SURFACE};
  color: {INK}; font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.14); }}
.filter input:focus-visible + label, .tab-list input:focus-visible + label {{
  outline: 2px solid {_ACCENT}; outline-offset: 1px; }}
.navs nav {{ display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 0.875rem; }}
.navs a {{ color: {INK_MUTED}; text-decoration: none; }}
.navs a:hover {{ color: {_ACCENT}; }}
main {{ max-width: 1280px; margin: 0 auto; padding: 28px 16px 64px; }}
h1 {{ font-size: 1.75rem; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.01em; }}
.meta {{ color: {INK_MUTED}; font-size: 0.85rem; margin: 0 0 24px; max-width: 90ch;
  overflow-wrap: anywhere; }}
.scope {{ font-weight: 600; margin: 0 0 12px; overflow-wrap: anywhere; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin: 0 0 24px; }}
.tile {{ background: {SURFACE}; border: 1px solid {GRID}; border-radius: 12px;
  padding: 14px 16px; }}
.tile-label {{ color: {INK_MUTED}; font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.04em; }}
.tile-value {{ font-size: 1.75rem; font-weight: 650; line-height: 1.2; margin: 4px 0 2px;
  font-variant-numeric: tabular-nums; }}
.tile-note {{ color: {INK_MUTED}; font-size: 0.8rem; }}
.card {{ background: {SURFACE}; border: 1px solid {GRID}; border-radius: 14px;
  padding: 20px 24px 24px; margin: 0 0 20px; box-shadow: 0 1px 2px rgb(0 0 0 / 0.04); }}
h2 {{ font-size: 1.3rem; margin: 0 0 14px; scroll-margin-top: 120px; }}
h3 {{ font-size: 0.95rem; margin: 0 0 8px; }}
.findings {{ background: #edf3fb; border-left: 3px solid {_ACCENT};
  border-radius: 0 8px 8px 0; padding: 10px 16px; margin: 0 0 20px; }}
.findings h3 {{ color: {_ACCENT}; font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.04em; margin: 0 0 4px; }}
.findings ul {{ margin: 0; padding-left: 18px; }}
.findings li {{ margin: 3px 0; }}
.charts {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; }}
@media (min-width: 1100px) {{ .charts {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
figure {{ margin: 0; }}
figure img {{ display: block; width: 100%; height: auto; }}
figcaption {{ color: {INK_MUTED}; font-size: 0.875rem; margin-top: 6px; }}
.tab-list {{ margin: 0 0 10px; }}
.table {{ margin-top: 28px; }}
.scroll {{ overflow: auto; max-height: 480px; border: 1px solid {GRID}; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem;
  font-variant-numeric: tabular-nums; }}
th, td {{ padding: 6px 12px; text-align: left; vertical-align: top;
  border-bottom: 1px solid {GRID}; }}
thead th {{ position: sticky; top: 0; background: #f1f0ec; color: {INK_MUTED};
  font-weight: 600; white-space: nowrap; }}
tbody tr:nth-child(even) {{ background: #f7f6f3; }}
.num {{ text-align: right; white-space: nowrap; }}
"""

_PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<header class="bar"><div class="bar-inner">{filter}<div class="navs">{navs}</div></div></header>
<main>
<h1>{title}</h1>
<p class="meta">{meta}</p>
<div class="views">{views}</div>
</main>
{script}
</body>
</html>
"""
