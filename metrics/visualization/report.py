"""Write the AI metrics report: one self-contained HTML file.

Usage: uv run report.py [--dir DIR] [--out FILE]
"""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import configs
import latency
import overview
from load import DataError, Metrics, load
from render import Section, render

SECTIONS: tuple[Callable[[Metrics], Section], ...] = (
    overview.build,
    configs.build,
    latency.build,
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
    args = parser.parse_args(argv)
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
