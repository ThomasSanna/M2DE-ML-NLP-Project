import uuid
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ThreadMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # `assistant` is the support side: technician or bot.
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


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
    # A one-member discriminated union is rejected by pydantic: escalation will turn the
    # route's return type into `AnswerResult | EscalationResult` on `kind`.
    kind: Literal["answer"] = "answer"
    answer: str
    sources: list[Citation]
    # Every retrieved entry, cited or not.
    memory: list[MemoryHit]
    searches: list[str]
    steps: int
    usage: Usage
