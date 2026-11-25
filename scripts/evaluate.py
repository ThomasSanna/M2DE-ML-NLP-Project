"""Evaluate the answer engine on the labelled questions of data/eval_set.json.

Usage (bash):       API_KEY=... python scripts/evaluate.py [--url URL] [--out results.json]
Usage (PowerShell): $env:API_KEY = "..."; python scripts/evaluate.py [--url URL]
Check the file only: python scripts/evaluate.py --check

Needs the API with the seed memory loaded (scripts/load_knowledge.py), so the VPN.
POST /api/v1/answer is read-only: no GLPI ticket, no reuse counted, nothing learnt.

Each question expects one route:
- memory:   the answer cites the expected seed entry (`knowledge`, its exact question);
- web:      the answer cites no memory entry (the question is outside the memory);
- handover: the bot hands the request over to a technician.
The search's cosine similarities are reported too: how close a reworded question stays
to its seed entry, and how close an unrelated one gets, to calibrate GROWTH_DEDUP_SIMILARITY.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "data" / "eval_set.json"
SEED_FILE = ROOT / "data" / "knowledge_seed.json"
ROUTES = ("memory", "web", "handover")


class Api:
    def __init__(self, url: str, api_key: str) -> None:
        self._url = url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._questions: dict[str, str] = {}

    def _call(self, method: str, path: str, body: object | None = None) -> Any:
        request = urllib.request.Request(
            f"{self._url}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers=self._headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))

    def answer(self, question: str) -> dict[str, Any]:
        body = {"messages": [{"role": "user", "content": question}]}
        return self._call("POST", "/api/v1/answer", body)

    def search(self, question: str) -> list[dict[str, Any]]:
        return self._call("POST", "/api/v1/memory/search", {"query": question, "top_k": 5})

    def question_of(self, knowledge_id: str) -> str:
        if knowledge_id not in self._questions:
            entry = self._call("GET", f"/api/v1/memory/knowledge/{knowledge_id}")
            self._questions[knowledge_id] = entry["question"]
        return self._questions[knowledge_id]


def check(items: list[dict[str, Any]]) -> list[str]:
    """Problems in the evaluation file, offline: routes, ids, expected seed entries."""
    seed = {entry["question"] for entry in json.loads(SEED_FILE.read_text(encoding="utf-8"))}
    problems = []
    ids = [item.get("id") for item in items]
    problems += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    for item in items:
        if item.get("expect") not in ROUTES or not item.get("question"):
            problems.append(f"{item.get('id')}: needs a question and an expect in {ROUTES}")
        if item.get("expect") == "memory" and item.get("knowledge") not in seed:
            problems.append(f"{item.get('id')}: `knowledge` is not a seed question")
        if item.get("expect") == "memory" and item.get("question") == item.get("knowledge"):
            problems.append(f"{item.get('id')}: reword the question, it is the seed one")
    return problems


def evaluate(api: Api, item: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = api.answer(item["question"])
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {**item, "error": str(exc)}
    seconds = time.perf_counter() - started
    cited = [s["question"] for s in result.get("sources", []) if s.get("type") == "knowledge"]
    retrieved = [api.question_of(hit["knowledge_id"]) for hit in result.get("memory", [])]
    hits = api.search(item["question"])
    expected = item.get("knowledge")
    row = {
        **item,
        "kind": result["kind"],
        "seconds": round(seconds, 2),
        "searches": len(result.get("searches", [])),
        "cited_memory": cited,
        "retrieved": expected in retrieved if expected else None,
        "cited": expected in cited if expected else None,
        "top_similarity": hits[0]["similarity"] if hits else None,
        "expected_similarity": next(
            (h["similarity"] for h in hits if h["knowledge"]["question"] == expected), None
        ),
    }
    row["ok"] = {
        "memory": row["kind"] == "answer" and bool(row["cited"]),
        "web": row["kind"] == "answer" and not cited,
        "handover": row["kind"] == "escalation",
    }[item["expect"]]
    return row


def _share(rows: list[dict[str, Any]], key: str) -> str:
    return f"{sum(bool(r[key]) for r in rows)}/{len(rows)}" if rows else "-"


def _spread(values: list[float]) -> str:
    if not values:
        return "-"
    values = sorted(values)
    p10, median = (_quantile(values, p) for p in (0.1, 0.5))
    return f"min {values[0]:.3f} · p10 {p10:.3f} · median {median:.3f} · max {values[-1]:.3f}"


def _quantile(values: list[float], p: float) -> float:
    """Nearest-rank quantile of sorted values."""
    return values[round(p * (len(values) - 1))]


def report(rows: list[dict[str, Any]]) -> None:
    done = [r for r in rows if "error" not in r]
    print(f"\n{len(rows)} questions, {len(rows) - len(done)} failed calls\n")
    for route in ROUTES:
        group = [r for r in done if r["expect"] == route]
        print(f"{route:9} correct route: {_share(group, 'ok')}")
    memory = [r for r in done if r["expect"] == "memory"]
    web = [r for r in done if r["expect"] == "web"]
    print(
        f"\nmemory    expected entry retrieved: {_share(memory, 'retrieved')}"
        f" · cited: {_share(memory, 'cited')}"
    )
    anyway = sum(bool(r["cited_memory"]) for r in web)
    print(f"web       cited a memory entry anyway: {anyway}/{len(web)}")
    if done:
        print(f"\noverall   correct route: {_share(done, 'ok')}")
        seconds = sorted(r["seconds"] for r in done)
        print(
            f"latency   median {statistics.median(seconds):.1f} s"
            f" · p95 {_quantile(seconds, 0.95):.1f} s"
        )
    print("\ncosine similarity (POST /memory/search), to calibrate GROWTH_DEDUP_SIMILARITY:")
    reworded = [r["expected_similarity"] for r in memory if r["expected_similarity"] is not None]
    outside = [r["top_similarity"] for r in web if r["top_similarity"] is not None]
    print(f"  reworded -> its seed entry: {_spread(reworded)}")
    print(f"  outside the memory -> best: {_spread(outside)}")
    wrong = [r for r in done if not r["ok"]]
    if wrong:
        print("\nwrong route:")
        for r in wrong:
            route = f"{r['expect']} -> {r['kind']}, memory cited: {len(r['cited_memory'])}"
            print(f"  {r['id']} ({route}): {r['question']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--out", type=Path, help="write each question's result as JSON")
    parser.add_argument("--check", action="store_true", help="check the file, call nothing")
    args = parser.parse_args()

    items = json.loads(args.file.read_text(encoding="utf-8"))
    problems = check(items)
    if problems or args.check:
        print("\n".join(problems) or f"{len(items)} questions, file OK")
        return 1 if problems else 0

    api_key = os.environ.get("API_KEY")
    if not api_key:
        print("API_KEY is not set", file=sys.stderr)
        return 1
    api = Api(args.url, api_key)
    rows = []
    for index, item in enumerate(items, start=1):
        rows.append(evaluate(api, item))
        print(f"\r{index}/{len(items)}", end="", file=sys.stderr, flush=True)
    report(rows)
    if args.out:
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
