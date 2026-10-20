"""Load data/knowledge_seed.json into the memory API.

Usage (bash):       API_KEY=... python scripts/load_knowledge.py [--url URL] [--file PATH]
Usage (PowerShell): $env:API_KEY = "..."; python scripts/load_knowledge.py [--url URL] [--file PATH]

The API has no idempotence: running this twice inserts every pair twice.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "data" / "knowledge_seed.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()

    api_key = os.environ.get("API_KEY")
    if not api_key:
        print("API_KEY is not set", file=sys.stderr)
        return 1

    entries = json.loads(args.file.read_text(encoding="utf-8"))
    failures = 0
    for index, entry in enumerate(entries, start=1):
        request = urllib.request.Request(
            f"{args.url}/api/v1/memory/knowledge",
            data=json.dumps(entry).encode("utf-8"),
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request).close()
        except urllib.error.HTTPError as error:
            failures += 1
            print(f"[{index}/{len(entries)}] HTTP {error.code}: {entry['question'][:60]}")
            continue
        print(f"[{index}/{len(entries)}] ok: {entry['category']}")

    print(f"{len(entries) - failures} inserted, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
