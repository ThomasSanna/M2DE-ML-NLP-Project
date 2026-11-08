from html import escape
from pathlib import Path

import pytest

from i18n import LANGUAGES
from load import load
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
    assert "<script" not in html
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
