import ast
import string
import tomllib
from pathlib import Path

import pytest

import overview
from i18n import CATALOG_FR, language
from load import load
from render import Explorer, Tabs, canvas, chart
from report import SECTIONS
from stats import cell_num, fmt_ms, fmt_p95, fmt_pct, note
from tests.conftest import write
from tests.test_report import DATASETS

_CODE = Path(__file__).resolve().parents[1]


def _translated_texts() -> list[ast.expr]:
    """The first argument of every t() call in the report code."""
    texts = []
    for path in sorted(_CODE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        texts += [
            node.args[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "t"
        ]
    return texts


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_catalog_translates_every_text_and_nothing_else() -> None:
    texts = _translated_texts()
    assert [ast.unparse(text) for text in texts if not isinstance(text, ast.Constant)] == []
    english = {text.value for text in texts if isinstance(text, ast.Constant)}
    catalog = tomllib.loads(CATALOG_FR.read_text(encoding="utf-8"))
    assert sorted(english - set(catalog)) == []
    assert sorted(set(catalog) - english) == []
    assert [en for en, fr in catalog.items() if _fields(en) != _fields(fr)] == []


def test_french_number_formats() -> None:
    with language("fr"):
        assert (fmt_ms(1990.0), fmt_ms(250.0), fmt_pct(0.6667)) == ("2,0 s", "250 ms", "67\u00a0%")
        assert (cell_num(2924.0), cell_num(1.0, 2)) == ("2\u202f924", "1,00")
        assert (note(19), fmt_p95(3500.0, 2)) == ("n=19, indicatif", "3,5 s (indicatif)")
    assert fmt_ms(1990.0) == "2.0 s"


def test_unknown_language_is_rejected() -> None:
    with pytest.raises(ValueError, match="de"), language("de"):
        pass


def test_french_ticks_use_a_decimal_comma() -> None:
    fig, ax = canvas()
    ax.plot([0.0, 1.0], [0.0, 1.0])
    with language("fr"):
        chart(fig, "c", "r")
        assert ax.xaxis.get_major_formatter().format_ticks([0.25, 0.5]) == ["0,25", "0,50"]


@pytest.mark.parametrize("name", DATASETS)
def test_every_sentence_and_caption_is_translated(tmp_path: Path, name: str) -> None:
    directory = write(tmp_path, *DATASETS[name])

    def texts(code: str) -> list[str]:
        with language(code):
            metrics = load(directory)
            sections = [build(metrics) for build in SECTIONS]
            tiles = [tile.label for tile in overview.tiles(metrics)]
        # The first option of a tab group is a translated text; the others are call kinds.
        first_options = [
            item.options[0][0]
            for section in sections
            for item in section.charts
            if isinstance(item, Tabs)
        ]
        explorers = [
            text
            for section in sections
            for item in section.charts
            if isinstance(item, Explorer)
            for text in (
                item.caption,
                item.reading,
                *(criterion.label for criterion in item.criteria),
                *(label for criterion in item.criteria for _, label in criterion.options),
            )
        ]
        return (
            tiles
            + first_options
            + explorers
            + [
                text
                for section in sections
                for text in (
                    *section.findings,
                    *(c.caption for c in section.all_charts()),
                    *(c.reading for c in section.all_charts()),
                    *(table.caption for table in section.tables),
                )
            ]
        )

    english, french = texts("en"), texts("fr")
    assert [en for en, fr in zip(english, french, strict=True) if en == fr] == []
