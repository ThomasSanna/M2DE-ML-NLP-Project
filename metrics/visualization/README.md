# Metrics report

Charts and interprets the AI metrics the API writes to `metrics/runs.v1.csv` and
`metrics/calls.v1.csv` (columns in [`doc/api_doc.md` § Metrics](../../doc/api_doc.md#metrics)).
The output is one self-contained HTML file: charts are embedded, nothing is loaded from the
network, and every sentence is computed from the current files.

## Run

```bash
cd metrics/visualization
uv run report.py                       # reads ../*.v1.csv, writes report.html here
uv run report.py --dir /path/to/csv --out /tmp/report.html
```

Collect data first: set `METRICS_DIR` (and optionally `METRICS_LABEL`) in `.env`, then
`docker compose up -d --build api`. `report.html` holds your data and is gitignored.

A missing file, a file with no run, or columns that do not match `v1` stop the script with a
message and exit code 1; no report is written.

## Reading the report

Every number shows its sample size `n`. Below 20 runs it is tagged `indicative`, and p95
values carry the same tag. A configuration needs 10 answered runs before its difference
from the baseline is put into words. Sentences describe the data; they never claim a cause.

| Section | What it tells you |
|---|---|
| Overview | Period covered, sources (`api`, `ticket_stream`, `worker`), share of answered runs, runs per configuration. |
| Configurations | One row per configuration: `METRICS_LABEL` plus the settings recorded with each run. C1 is the configuration with the most answered runs, the baseline: other configurations are compared with it on median total and mean tokens. |
| Latency | Mean time per run split by call kind, per configuration. `other` is the time outside remote calls: prompt building, DB commit, parsing. Also the timeline of a typical run, gaps between calls, and generation speed (completion tokens per second, prefill included). |
| Tokens and cache | Prompt and completion tokens per run and per agent step, thread length against prompt size, and the prefix cache rate. The cache rate needs vLLM started with `--enable-prompt-tokens-details`; until then the report says it is not reported. |
| Agent | Steps per run, what the model did at each step (`answer`, `search`, `invalid`), responses cut by `LLM_MAX_TOKENS`, web searches by outcome, Exa cost, and refs the model cited without having seen them (dropped). |
| Memory and thread | Memory entries kept after the rerank threshold, best rerank score against the threshold (runs just below it hint at a threshold that is too strict), what answers cite, and first questions against follow-ups (`prior_replies`). |
| Errors | Failed runs by outcome, the call that failed them, and the latest failures. |

## Comparing configurations

Give each campaign its own `METRICS_LABEL` (for example `baseline`, `steps-5`,
`threshold-0.9`), restart the API, and send the same questions. The label is part of the
configuration key, so each campaign gets its own row, box and bar.

## Development

```bash
uv run ruff format . && uv run ruff check .
uv run pytest -q
```

Tests write synthetic CSV files with the `v1` header (`tests/conftest.py`) and check the
exact sentences of each section, plus a whole report on edge cases: a single run, error-only
runs, a run without call rows, a failed configuration next to a healthy one.
