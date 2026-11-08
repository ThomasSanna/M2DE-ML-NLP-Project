"""Report language: texts are written in English in the code and translated through a catalog."""

import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

LANGUAGES = ("fr", "en")
CATALOG_FR = Path(__file__).resolve().parent / "locales" / "fr.toml"
_FR: dict[str, str] = tomllib.loads(CATALOG_FR.read_text(encoding="utf-8"))
# English outside a language block, so code and tests that do not choose a language read the
# texts as written; the command line picks French by default.
_language: ContextVar[str] = ContextVar("language", default="en")


@contextmanager
def language(code: str) -> Iterator[None]:
    """Write every text and number inside the block in this language."""
    if code not in LANGUAGES:
        raise ValueError(f"unknown report language {code!r}, expected one of {LANGUAGES}")
    token = _language.set(code)
    try:
        yield
    finally:
        _language.reset(token)


def current() -> str:
    return _language.get()


def t(text: str, **values: object) -> str:
    """Translate an English text, then fill its {placeholders} with already formatted values."""
    template = text if _language.get() == "en" else _FR[text]
    return template.format(**values)


def localize(text: str) -> str:
    """Rewrite a number formatted the English way with the separators of the language."""
    if _language.get() == "en":
        return text
    # French: narrow no-break space between thousands, decimal comma, no-break space before %.
    return text.replace(",", "\u202f").replace(".", ",").replace("%", "\u00a0%")


def number(value: float, spec: str = ".0f") -> str:
    return localize(format(value, spec))
