from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Configuration lue depuis l'environnement.

    En local (hors Docker), le `.env` racine du dépôt est aussi lu : lancer les
    commandes depuis `api/`.
    """

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "memoire"
    postgres_password: SecretStr
    postgres_db: str = "memoire"

    # Clé exigée (en-tête X-API-Key) sur les routes de la mémoire.
    api_key: SecretStr
    # Secret partagé avec le webhook GLPI ; vide = signature non vérifiée.
    glpi_webhook_secret: SecretStr | None = None

    # Serveur d'embeddings compatible OpenAI (vLLM de l'université), URL incluant `/v1`.
    embedding_base_url: AnyHttpUrl
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "BAAI/bge-multilingual-gemma2"
    # Instruction préfixée à tous les textes (mémoire et requêtes : la tâche est symétrique,
    # question contre questions). La changer impose de ré-embedder toute la mémoire.
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
    return Settings()  # type: ignore[call-arg]  # champs requis fournis par l'environnement
