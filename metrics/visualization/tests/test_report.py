import re
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import pytest

from i18n import LANGUAGES
from load import load
from page import render
from render import Chart, Section, Table, Tabs, View
from report import SECTIONS, main
from tests.conftest import EMPTY_RESULT, Row, call, calls_of, run, write

TITLES = {
    "en": (
        "Overview",
        "Configurations",
        "Latency",
        "Tokens and cache",
        "Agent",
        "Memory and thread",
        "Errors",
    ),
    "fr": (
        "Vue d'ensemble",
        "Configurations",
        "Latence",
        "Tokens et cache",
        "Agent",
        "Mémoire et fil",
        "Erreurs",
    ),
}


def test_main_writes_a_self_contained_report(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    assert main(["--dir", str(write(tmp_path, [run()], calls_of("r1"))), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert '<html lang="fr">' in html
    assert "<h1>Rapport des métriques IA</h1>" in html
    assert all(f">{escape(title)}<" in html for title in TITLES["fr"])
    assert 'src="data:image/png;base64,' in html
    # Nothing loaded from elsewhere: the explorer script is inline.
    assert re.findall(r"<script[^>]*>", html) == [
        '<script type="application/json" class="explorer-data">',
        "<script>",
    ]
    assert "<link" not in html


def test_main_reports_a_data_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "report.html"
    assert main(["--dir", str(tmp_path), "--out", str(out)]) == 1
    assert "runs.v1.csv" in capsys.readouterr().err
    assert not out.exists()


def test_label_is_escaped(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    data = write(tmp_path, [run(label="<b>x</b>")], calls_of("r1"))
    assert main(["--dir", str(data), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


DATASETS: dict[str, tuple[list[Row], list[Row]]] = {
    "single run": ([run()], calls_of("r1")),
    "errors only": (
        [run(outcome="embedding_error", **EMPTY_RESULT)],
        [call("r1", 1, "embed", None, 0.0, 5.0, status="EmbeddingError")],
    ),
    "run without calls": ([run()], []),
    "failed config next to a healthy one": (
        [run(), run(run_id="r2", label="b", outcome="llm_error", **EMPTY_RESULT)],
        calls_of("r1"),
    ),
}


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("name", DATASETS)
def test_every_section_renders(tmp_path: Path, name: str, lang: str) -> None:
    runs, calls = DATASETS[name]
    out = tmp_path / "report.html"
    data = str(write(tmp_path, runs, calls))
    assert main(["--dir", data, "--out", str(out), "--lang", lang]) == 0
    html = out.read_text(encoding="utf-8")
    assert f'<html lang="{lang}">' in html
    for title in TITLES[lang]:
        assert f">{escape(title)}<" in html


def test_sections_are_in_report_order(tmp_path: Path) -> None:
    metrics = load(write(tmp_path, [run()], calls_of("r1")))
    assert tuple(build(metrics).title for build in SECTIONS) == TITLES["en"]


def test_chart_count_does_not_grow_with_the_data(tmp_path: Path) -> None:
    def images(count: int) -> int:
        directory = tmp_path / str(count)
        directory.mkdir()
        runs = [run(run_id=f"r{i}", total_ms=3000.0 + i) for i in range(count)]
        calls = [c for i in range(count) for c in calls_of(f"r{i}")]
        out = directory / "report.html"
        assert main(["--dir", str(write(directory, runs, calls)), "--out", str(out)]) == 0
        return out.read_text(encoding="utf-8").count("<img")

    assert images(25) == images(500)


def _page(tmp_path: Path, *sections: Section) -> str:
    view = View(name="All", scope="Everything.", tiles=[], sections=list(sections))
    return render([view], source=tmp_path, runs=1, calls=0, generated_at=datetime(2026, 11, 8))


def test_chart_title_above_and_reading_below(tmp_path: Path) -> None:
    page = _page(tmp_path, Section("S", [], [Chart("Runs per day", "AAAA", "One bar per day.")]))
    title, image, reading = (
        page.index("<h3>Runs per day</h3>"),
        page.index("<img"),
        page.index("One bar per day."),
    )
    assert title < image < reading


def test_numeric_columns_are_right_aligned(tmp_path: Path) -> None:
    frame = pd.DataFrame({"kind": ["llm"], "calls": [12], "median": ["2.0 s"], "p95": ["-"]})
    page = _page(tmp_path, Section("S", [], tables=[Table("T", frame)]))
    assert "<td>llm</td>" in page
    assert '<td class="num">12</td><td class="num">2.0 s</td><td class="num">-</td>' in page


def test_tabs_show_their_first_option(tmp_path: Path) -> None:
    options = [("All kinds", Chart("a", "AAAA", "r")), ("llm", Chart("b", "BBBB", "r"))]
    page = _page(tmp_path, Section("S", [], [Tabs(options)]))
    radios = re.findall(r'<input type="radio" name="([^"]+)"[^>]*>', page)
    assert len(radios) == 2 and radios[0] == radios[1]
    assert re.search(r'<input type="radio"[^>]* checked>\s*<label[^>]*>All kinds</label>', page)


def test_one_view_per_configuration(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    runs = [run(), run(run_id="r2", label="b")]
    data = write(tmp_path, runs, [*calls_of("r1"), *calls_of("r2")])
    assert main(["--dir", str(data), "--out", str(out), "--lang", "en"]) == 0
    html = out.read_text(encoding="utf-8")
    assert html.count('<article class="view"') == 3
    filters = re.findall(r'<input type="radio" name="view"[^>]*>', html)
    assert len(filters) == 3 and "checked" in filters[0]
    # A single configuration has nothing to compare: only the all-configurations view has it.
    assert html.count(">Configurations</h2>") == 1


def test_a_single_configuration_needs_no_filter(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    assert main(["--dir", str(write(tmp_path, [run()], calls_of("r1"))), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert html.count('<article class="view"') == 1
    assert 'name="view"' not in html


@pytest.mark.parametrize("name", DATASETS)
def test_every_chart_explains_how_to_read_it(tmp_path: Path, name: str) -> None:
    metrics = load(write(tmp_path, *DATASETS[name]))
    charts = [c for build in SECTIONS for c in build(metrics).all_charts()]
    assert charts
    assert [c.caption for c in charts if not c.reading.strip()] == []
