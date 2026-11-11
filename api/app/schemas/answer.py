import uuid
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Bounds the request body (50 messages); the model reads only 2000 characters anyway.
THREAD_MESSAGE_MAX_CHARS = 20_000


class ThreadMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # `assistant` is the support side: technician or bot.
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=THREAD_MESSAGE_MAX_CHARS)


class AnswerRequest(BaseModel):
    messages: list[ThreadMessage] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _ends_with_user(self) -> Self:
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


class KnowledgeCitation(BaseModel):
    ref: str
    type: Literal["knowledge"] = "knowledge"
    knowledge_id: uuid.UUID
    question: str


class WebCitation(BaseModel):
    ref: str
    type: Literal["web"] = "web"
    url: str
    title: str


Citation = Annotated[KnowledgeCitation | WebCitation, Field(discriminator="type")]


class MemoryHit(BaseModel):
    knowledge_id: uuid.UUID
    score: float


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class AnswerResult(BaseModel):
    kind: Literal["answer"] = "answer"
    answer: str
    sources: list[Citation]
    # Every retrieved entry, cited or not.
    memory: list[MemoryHit]
    searches: list[str]
    steps: int
    usage: Usage


class EscalationResult(BaseModel):
    """The agent handed the request to a technician instead of answering."""

    kind: Literal["escalation"] = "escalation"
    # Shown to the requester.
    message: str
    # For the support only, never shown to the requester.
    reason: str
    memory: list[MemoryHit]
    searches: list[str]
    steps: int
    usage: Usage


AnswerOutcome = Annotated[AnswerResult | EscalationResult, Field(discriminator="kind")]
