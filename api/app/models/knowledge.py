import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.feedback import Feedback

# Dimension des vecteurs de BAAI/bge-multilingual-gemma2 (serveur de l'université).
# Au-delà de 2000 dimensions, l'index HNSW de pgvector exige `halfvec` (float16, max 4000).
EMBEDDING_DIM = 3584


def str_enum(enum_cls: type[StrEnum]) -> Enum:
    """Enum stocké en VARCHAR + CHECK (plus simple à faire évoluer qu'un type ENUM Postgres)."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class KnowledgeSource(StrEnum):
    GLPI_HISTORY = "glpi_history"  # paire Q/R issue de l'historique GLPI (ou du jeu synthétique)
    LLM = "llm"  # réponse générée par la pipeline
    TECHNICIAN = "technician"  # réponse écrite ou corrigée par un technicien


class KnowledgeStatus(StrEnum):
    ACTIVE = "active"
    TO_REVIEW = "to_review"  # mal notée : à réviser par un technicien
    ARCHIVED = "archived"


class Knowledge(TimestampMixin, Base):
    """Une « connaissance » de la mémoire : une paire question/réponse indexée."""

    __tablename__ = "knowledge"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[KnowledgeSource] = mapped_column(str_enum(KnowledgeSource))
    status: Mapped[KnowledgeStatus] = mapped_column(
        str_enum(KnowledgeStatus), default=KnowledgeStatus.ACTIVE, index=True
    )
    glpi_ticket_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Nombre de fois où la connaissance a servi à construire une réponse.
    reuse_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Embedding de la question : c'est elle qu'on rapproche des nouvelles demandes.
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIM))

    feedbacks: Mapped[list["Feedback"]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "ix_knowledge_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )
