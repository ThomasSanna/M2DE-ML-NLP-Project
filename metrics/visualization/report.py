"""Write the AI metrics report: one self-contained HTML file.

Usage: uv run report.py [--dir DIR] [--out FILE] [--lang {fr,en}]
"""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import agent
import configs
import errors
import latency
import memory
import overview
import tokens
from i18n import LANGUAGES, language, t
from load import DataError, Metrics, load
from page import render
from render import Section, View

SECTIONS: tuple[Callable[[Metrics], Section], ...] = (
    overview.build,
    configs.build,
    latency.build,
    tokens.build,
    agent.build,
    memory.build,
    errors.build,
)

_HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chart and interpret the AI metrics CSV files.")
    parser.add_argument(
        "--dir",
        type=Path,
        default=_HERE.parent,
        help="directory holding runs.v1.csv and calls.v1.csv (default: metrics/)",
    )
    parser.add_argument(
        "--out", type=Path, default=_HERE / "report.html", help="HTML file to write"
    )
    parser.add_argument(
        "--lang", choices=LANGUAGES, default="fr", help="report language (default: fr)"
    )
    args = parser.parse_args(argv)
    # Loading is inside the block too: it writes the configuration labels.
    with language(args.lang):
        try:
            metrics = load(args.dir)
        except DataError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        page = render(
            views(metrics),
            source=args.dir.resolve(),
            runs=len(metrics.runs),
            calls=len(metrics.calls),
            generated_at=datetime.now(UTC),
        )
    args.out.write_text(page, encoding="utf-8")
    print(args.out.resolve())
    return 0


def views(metrics: Metrics) -> list[View]:
    """All configurations together, then one view per configuration when there are several."""
    ids = sorted(metrics.runs["config_id"].unique(), key=lambda config_id: int(config_id[1:]))
    together = View(
        name=t("All"),
        scope=t("All configurations together ({count}).", count=len(ids)),
        tiles=overview.tiles(metrics),
        sections=[build(metrics) for build in SECTIONS],
    )
    if len(ids) < 2:
        return [together]
    return [together, *(_configuration(metrics.only(config_id)) for config_id in ids)]


def _configuration(metrics: Metrics) -> View:
    first = metrics.runs.iloc[0]
    return View(
        name=str(first["config_id"]),
        scope=f"{first['config_id']} · {first['config']}",
        tiles=overview.tiles(metrics),
        # Comparing configurations means nothing inside one of them.
        sections=[build(metrics) for build in SECTIONS if build is not configs.build],
    )


if __name__ == "__main__":
    sys.exit(main())
