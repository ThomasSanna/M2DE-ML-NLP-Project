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
    [
        {"agent_max_steps": 0},
        {"rerank_threshold": 1.5},
        {"retrieval_top_k": 0},
        {"glpi_survey_poll_seconds": 0},
        {"feedback_weight": 1.5},
        {"feedback_prior": 0},
        {"feedback_half_life_days": 0},
        {"reuse_weight": -0.1},
        {"reuse_half": 0},
        {"feedback_weight": 0.6, "reuse_weight": 0.5},
    ],
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


def test_empty_metrics_settings_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRICS_DIR", "")
    monkeypatch.setenv("METRICS_LABEL", "")
    settings = _settings()
    assert (settings.metrics_dir, settings.metrics_label) == (None, None)


def test_empty_feedback_half_life_never_fades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEEDBACK_HALF_LIFE_DAYS", "")
    assert _settings().feedback_half_life_days is None


def test_default_rerank_threshold_keeps_every_entry_the_model_cited() -> None:
    # Calibrated on the 08/11 metrics: the lowest best score of a memory-citing run was 0.9895.
    assert _settings().rerank_threshold == 0.985
