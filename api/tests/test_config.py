import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from app.core.config import Settings, get_database_settings
from tests.conftest import API_ROOT


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        postgres_password="test",
        api_key="test",
        embedding_base_url="http://embeddings.invalid/v1",
        llm_base_url="http://llm.invalid/v1",
        reranker_base_url="http://reranker.invalid",
        **overrides,
    )


def test_exa_include_domains_is_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_INCLUDE_DOMAINS", "univ-corse.fr, example.org")
    assert _settings().exa_include_domains == ["univ-corse.fr", "example.org"]


def test_empty_exa_include_domains_is_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_INCLUDE_DOMAINS", "")
    assert _settings().exa_include_domains == []


def test_empty_exa_api_key_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "")
    assert _settings().exa_api_key is None


@pytest.mark.parametrize(
    "overrides",
    [{"agent_max_steps": 0}, {"rerank_threshold": 1.5}, {"retrieval_top_k": 0}],
)
def test_invalid_agent_settings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


def test_migrations_need_only_database_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Outside api/, the relative env files (the real `.env`) are not found.
    monkeypatch.chdir(tmp_path)
    for name in ("API_KEY", "EMBEDDING_BASE_URL", "LLM_BASE_URL", "RERANKER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    get_database_settings.cache_clear()
    output = io.StringIO()
    config = Config(API_ROOT / "alembic.ini", output_buffer=output)
    config.attributes["configure_logger"] = False

    try:
        command.upgrade(config, "head", sql=True)
    finally:
        get_database_settings.cache_clear()

    assert "CREATE TABLE knowledge" in output.getvalue()
