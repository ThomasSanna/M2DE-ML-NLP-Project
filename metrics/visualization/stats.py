"""Small-sample rule and number formats shared by the report sections."""

import pandas as pd

# Below this many runs, p95 values and comparisons are only indicative.
INDICATIVE_BELOW = 20
# A configuration needs this many answered runs before its difference is interpreted.
MIN_GROUP = 10


def note(n: int) -> str:
    return f"n={n}, indicative" if n < INDICATIVE_BELOW else f"n={n}"


def fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.1f} s" if round(ms) >= 1000 else f"{ms:.0f} ms"


def fmt_pct(ratio: float) -> str:
    return f"{ratio:.0%}"


def counts(values: pd.Series) -> list[tuple[str, int]]:
    """Value counts sorted by count, then name, so sentences are stable."""
    pairs = ((str(value), int(n)) for value, n in values.value_counts().items())
    return sorted(pairs, key=lambda pair: (-pair[1], pair[0]))


def listing(pairs: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name} {n}" for name, n in pairs)
