import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import EmbedderDep, SessionDep, require_api_key
from app.schemas.knowledge import KnowledgeCreate, KnowledgeRead, SearchHit, SearchRequest
from app.services import memory

router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(require_api_key)])


@router.post("/knowledge", status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    data: KnowledgeCreate, session: SessionDep, embedder: EmbedderDep
) -> KnowledgeRead:
    """Ajoute une paire question/réponse à la mémoire (la question est embeddée)."""
    knowledge = await memory.add_knowledge(session, embedder, data)
    return KnowledgeRead.model_validate(knowledge)


@router.get("/knowledge/{knowledge_id}")
async def read_knowledge(knowledge_id: uuid.UUID, session: SessionDep) -> KnowledgeRead:
    knowledge = await memory.get_knowledge(session, knowledge_id)
    if knowledge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connaissance introuvable")
    return KnowledgeRead.model_validate(knowledge)


@router.post("/search")
async def search_knowledge(
    request: SearchRequest, session: SessionDep, embedder: EmbedderDep
) -> list[SearchHit]:
    """Top-k des connaissances actives les plus proches de la question."""
    results = await memory.search(session, embedder, request.query, request.top_k)
    return [
        SearchHit(knowledge=KnowledgeRead.model_validate(knowledge), similarity=similarity)
        for knowledge, similarity in results
    ]
