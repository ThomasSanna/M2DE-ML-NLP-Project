from pathlib import Path

import pytest

from load import load
from report import SECTIONS, main
from tests.conftest import calls_of, run, write


def test_main_writes_a_self_contained_report(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    assert main(["--dir", str(write(tmp_path, [run()], calls_of("r1"))), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert all(f">{build(load(tmp_path)).title}<" in html for build in SECTIONS)
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
