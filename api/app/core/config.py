from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Configuration read from the environment.

    Locally (outside Docker), the repository root `.env` is also read: run
    commands from `api/`.
    """

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "memoire"
    postgres_password: SecretStr
    postgres_db: str = "memoire"

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

    @field_validator(
        "glpi_webhook_secret", "embedding_api_key", "embedding_instruction", mode="before"
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if value == "" else value

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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields provided by the environment
