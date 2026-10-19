# Importer tous les modèles ici pour qu'ils soient enregistrés dans Base.metadata (Alembic).
from app.models.feedback import Feedback, FeedbackSource
from app.models.glpi_event import GlpiEvent, GlpiEventStatus
from app.models.knowledge import EMBEDDING_DIM, Knowledge, KnowledgeSource, KnowledgeStatus

__all__ = [
    "EMBEDDING_DIM",
    "Feedback",
    "FeedbackSource",
    "GlpiEvent",
    "GlpiEventStatus",
    "Knowledge",
    "KnowledgeSource",
    "KnowledgeStatus",
]
