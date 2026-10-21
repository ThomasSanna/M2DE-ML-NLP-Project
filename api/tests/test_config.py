import pytest
from pydantic import ValidationError

from app.core.config import Settings


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
