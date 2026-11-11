"""What the GLPI plugin reads back about the tickets the bot answers."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

ProgressStatus = Literal["idle", "thinking", "searching", "done", "failed"]


class SourceRead(BaseModel):
    """A web page cited by an answer, as the sources panel lists it."""

    title: str
    url: str
    domain: str


class ThinkingStep(BaseModel):
    """The agent is reading the ticket."""

    kind: Literal["thinking"] = "thinking"


class SearchStep(BaseModel):
    """The agent is looking for sources, with what it found."""

    kind: Literal["search"] = "search"
    query: str
    sources: list[SourceRead] = Field(default_factory=list)


StepRead = Annotated[ThinkingStep | SearchStep, Field(discriminator="kind")]


class ProgressRead(BaseModel):
    """`idle` when the bot never ran on this ticket in this process."""

    status: ProgressStatus
    steps: list[StepRead] = []
    followup_id: int | None = None


class AnswerSourcesRead(BaseModel):
    followup_id: int
    sources: list[SourceRead]
