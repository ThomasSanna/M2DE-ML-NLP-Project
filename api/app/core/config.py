from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
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

    # LLM de génération (vLLM de l'université, compatible OpenAI), URL incluant `/v1`.
    llm_base_url: AnyHttpUrl = AnyHttpUrl("http://193.48.31.6:32297/v1")
    llm_api_key: SecretStr | None = None
    llm_model: str = "google/gemma3-12b-it"
    llm_timeout_seconds: float = 120.0
    # Nombre de tokens regroupés par événement SSE.
    sse_chunk_tokens: int = Field(default=6, ge=1)
    # Connaissances de la mémoire injectées dans le prompt.
    answer_top_k: int = Field(default=3, ge=1, le=20)

    # API REST de GLPI (nom du service Docker). Sans User-Token, les routes qui écrivent
    # dans GLPI répondent 503.
    glpi_api_url: AnyHttpUrl = AnyHttpUrl("http://glpi/apirest.php/")
    glpi_user_token: SecretStr | None = None
    glpi_app_token: SecretStr | None = None
    # Suivi privé = proposition à valider par un technicien ; public = envoi direct (mail).
    glpi_followup_private: bool = False

    @field_validator(
        "glpi_webhook_secret",
        "embedding_api_key",
        "embedding_instruction",
        "llm_api_key",
        "glpi_user_token",
        "glpi_app_token",
        mode="before",
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
