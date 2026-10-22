from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class DatabaseSettings(BaseSettings):
    """Database connection read from the environment.

    Locally (outside Docker), the repository root `.env` is also read: run
    commands from `api/`. Alembic reads only this part, so migrations do not depend on
    the model server settings the API requires.
    """

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "memoire"
    postgres_password: SecretStr
    postgres_db: str = "memoire"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class Settings(DatabaseSettings):
    """Configuration of the API."""

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Key required (X-API-Key header) on the memory routes.
    api_key: SecretStr
    # Secret shared with the GLPI webhook; empty = signature not verified.
    glpi_webhook_secret: SecretStr | None = None

    # OpenAI-compatible embeddings server (university vLLM), URL including `/v1`.
    embedding_base_url: AnyHttpUrl
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "BAAI/bge-multilingual-gemma2"
    # Instruction prefixed to every text (memory and queries: the task is symmetric,
    # question against questions). Changing it requires re-embedding the whole memory.
    embedding_instruction: str | None = (
        "Étant donné une demande d'assistance informatique, "
        "retrouver les demandes similaires déjà posées"
    )
    embedding_timeout_seconds: float = 30.0

    # University LLM (Hugging Face TGI, OpenAI-compatible), URL including `/v1`.
    llm_base_url: AnyHttpUrl
    # TGI serves a single model and ignores this field; kept for logs and portability.
    llm_model: str = "google/gemma3-12b-it"
    llm_max_tokens: int = Field(default=512, ge=1)
    llm_timeout_seconds: float = 60.0

    # University reranker (`POST /rerank`), URL without `/v1`.
    reranker_base_url: AnyHttpUrl
    reranker_timeout_seconds: float = 30.0

    # Vector candidates sent to the reranker, then entries kept above the threshold
    # (sigmoid of the reranker logit). 0.95 is logit 2.9: on the seed memory, expected
    # entries score 4.3 or more, off-topic queries under 3.2.
    retrieval_candidates: int = Field(default=20, ge=1)
    retrieval_top_k: int = Field(default=3, ge=1)
    rerank_threshold: float = Field(default=0.95, ge=0.0, le=1.0)

    # Exa web search; empty key = web search disabled.
    exa_api_key: SecretStr | None = None
    # NoDecode: the env value is a comma-separated list, not JSON.
    exa_include_domains: Annotated[list[str], NoDecode] = []
    exa_num_results: int = Field(default=3, ge=1, le=100)
    exa_timeout_seconds: float = 15.0

    # Model calls per answer; the last one is forced to answer.
    agent_max_steps: int = Field(default=3, ge=1)

    @field_validator(
        "glpi_webhook_secret",
        "embedding_api_key",
        "embedding_instruction",
        "exa_api_key",
        mode="before",
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("exa_include_domains", mode="before")
    @classmethod
    def _split_domains(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields provided by the environment


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()  # type: ignore[call-arg]  # password provided by the environment
