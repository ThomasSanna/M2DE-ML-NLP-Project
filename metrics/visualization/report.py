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
from i18n import LANGUAGES, language
from load import DataError, Metrics, load
from render import Section, render

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
            [build(metrics) for build in SECTIONS],
            source=args.dir.resolve(),
            runs=len(metrics.runs),
            calls=len(metrics.calls),
            generated_at=datetime.now(UTC),
        )
    args.out.write_text(page, encoding="utf-8")
    print(args.out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
