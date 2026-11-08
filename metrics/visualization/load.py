"""Read the metrics CSV files written by the API and derive the columns the report uses."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from i18n import number, t

RUNS_FILE = "runs.v1.csv"
CALLS_FILE = "calls.v1.csv"

_RUN_TYPES: dict[str, str] = {
    "run_id": "string",
    "started_at": "string",
    "source": "string",
    "ticket_id": "Int64",
    "label": "string",
    "query_hash": "string",
    "outcome": "string",
    "llm_model": "string",
    "agent_max_steps": "Int64",
    "llm_max_tokens": "Int64",
    "retrieval_candidates": "Int64",
    "retrieval_top_k": "Int64",
    "rerank_threshold": "Float64",
    "web_search_enabled": "boolean",
    "total_ms": "Float64",
    "retrieval_ms": "Float64",
    "agent_ms": "Float64",
    "llm_ms": "Float64",
    "web_ms": "Float64",
    "steps": "Int64",
    "invalid_outputs": "Int64",
    "searches": "Int64",
    "searches_failed": "Int64",
    "searches_duplicate": "Int64",
    "searches_unavailable": "Int64",
    "prompt_tokens": "Int64",
    "completion_tokens": "Int64",
    "cached_tokens": "Int64",
    "memory_candidates": "Int64",
    "memory_kept": "Int64",
    "memory_top_score": "Float64",
    "cited_knowledge": "Int64",
    "cited_web": "Int64",
    "dropped_refs": "Int64",
    "thread_messages": "Int64",
    "prior_replies": "Int64",
    "thread_chars": "Int64",
    "answer_chars": "Int64",
    "web_cost_usd": "Float64",
}

_CALL_TYPES: dict[str, str] = {
    "run_id": "string",
    "seq": "Int64",
    "kind": "string",
    "step": "Int64",
    "start_ms": "Float64",
    "duration_ms": "Float64",
    "status": "string",
    "items": "Int64",
    "prompt_tokens": "Int64",
    "completion_tokens": "Int64",
    "cached_tokens": "Int64",
    "finish_reason": "string",
    "action": "string",
    "cost_usd": "Float64",
}

RUN_COLUMNS = tuple(_RUN_TYPES)
CALL_COLUMNS = tuple(_CALL_TYPES)


class DataError(Exception):
    """The metrics files cannot be analyzed; the message is meant for the user."""


@dataclass(frozen=True)
class Metrics:
    runs: pd.DataFrame
    calls: pd.DataFrame

    def only(self, config_id: str) -> "Metrics":
        """The runs of one configuration and their calls; config ids keep their value."""
        runs = self.runs[self.runs["config_id"] == config_id]
        return Metrics(runs=runs, calls=self.calls[self.calls["run_id"].isin(runs["run_id"])])


def load(directory: Path) -> Metrics:
    runs = _read(directory / RUNS_FILE, _RUN_TYPES)
    if runs.empty:
        raise DataError(f"{directory / RUNS_FILE} has no run yet")
    calls = _read(directory / CALLS_FILE, _CALL_TYPES)
    runs["started_at"] = pd.to_datetime(runs["started_at"], utc=True, format="ISO8601")
    calls = _derive_calls(calls)
    return Metrics(runs=_derive_runs(runs, calls), calls=calls)


def _read(path: Path, types: dict[str, str]) -> pd.DataFrame:
    if not path.is_file():
        raise DataError(f"{path} not found: set METRICS_DIR on the API or pass --dir")
    try:
        header = pd.read_csv(path, nrows=0).columns
    except pd.errors.EmptyDataError:
        raise DataError(f"{path} is empty: it should at least hold its header") from None
    missing = sorted(set(types) - set(header))
    extra = sorted(set(header) - set(types))
    if missing or extra:
        raise DataError(
            f"{path} has unexpected columns (missing: {missing or 'none'}, extra: "
            f"{extra or 'none'}): a newer file version is probably in use"
        )
    # Only an empty cell is missing: a label such as "None" or "NA" stays text.
    return pd.read_csv(path, dtype=types, keep_default_na=False, na_values=[""])


def _derive_calls(calls: pd.DataFrame) -> pd.DataFrame:
    calls = calls.sort_values(["run_id", "seq"], kind="stable").reset_index(drop=True)
    end = calls["start_ms"] + calls["duration_ms"]
    calls["gap_ms"] = calls.groupby("run_id")["start_ms"].shift(-1) - end
    generated = (calls["kind"] == "llm") & (calls["status"] == "ok") & (calls["duration_ms"] > 0)
    calls["tokens_per_s"] = (calls["completion_tokens"] / calls["duration_ms"] * 1000).where(
        generated
    )
    return calls


def _derive_runs(runs: pd.DataFrame, calls: pd.DataFrame) -> pd.DataFrame:
    in_calls = runs["run_id"].map(calls.groupby("run_id")["duration_ms"].sum())
    runs["overhead_ms"] = runs["total_ms"] - in_calls.fillna(0.0)
    runs["cache_rate"] = (runs["cached_tokens"] / runs["prompt_tokens"]).where(
        runs["prompt_tokens"] > 0
    )
    runs["config"] = runs.apply(_config, axis=1).astype("string")
    runs["config_id"] = runs["config"].map(_config_ids(runs)).astype("string")
    knowledge = runs["cited_knowledge"].fillna(0) > 0
    web = runs["cited_web"].fillna(0) > 0
    grounding = pd.Series("none", index=runs.index, dtype="string")
    grounding[knowledge] = "memory"
    grounding[web] = "web"
    grounding[knowledge & web] = "memory+web"
    runs["grounding"] = grounding.where(runs["outcome"] == "answered")
    return runs


def answered(runs: pd.DataFrame) -> pd.DataFrame:
    return runs[runs["outcome"] == "answered"]


def _config_ids(runs: pd.DataFrame) -> dict[str, str]:
    """C1 is the configuration with the most answered runs: the baseline of comparisons."""
    order = (
        runs.assign(answered=runs["outcome"] == "answered")
        .groupby("config", as_index=False)
        .agg(answered=("answered", "sum"), first=("started_at", "min"))
        .sort_values(["answered", "first", "config"], ascending=[False, True, True])
    )
    return {config: f"C{i}" for i, config in enumerate(order["config"], start=1)}


def _config(run: pd.Series) -> str:
    return t(
        "{label} · {model} · steps {steps} · max_tokens {max_tokens} · candidates {candidates}"
        " · top_k {top_k} · threshold {threshold} · web {web}",
        label=run["label"] if pd.notna(run["label"]) else t("(no label)"),
        model=run["llm_model"],
        steps=run["agent_max_steps"],
        max_tokens=run["llm_max_tokens"],
        candidates=run["retrieval_candidates"],
        top_k=run["retrieval_top_k"],
        threshold=number(run["rerank_threshold"], "g"),
        web=t("on") if run["web_search_enabled"] else t("off"),
    )
