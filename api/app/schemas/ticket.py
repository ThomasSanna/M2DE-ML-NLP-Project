"""What the GLPI plugin reads back about the tickets the bot answers."""

from typing import Literal

from pydantic import BaseModel

ProgressStatus = Literal["idle", "thinking", "searching", "done", "failed"]


class SourceRead(BaseModel):
    """A web page cited by an answer, as the sources panel lists it."""

    title: str
    url: str
    domain: str


class StepRead(BaseModel):
    """One line of the progress bubble."""

    kind: Literal["thinking", "search"]
    query: str | None = None
    sources: list[SourceRead] = []


class ProgressRead(BaseModel):
    """`idle` when the bot never ran on this ticket in this process."""

    status: ProgressStatus
    steps: list[StepRead] = []
    followup_id: int | None = None


class AnswerSourcesRead(BaseModel):
    followup_id: int
    sources: list[SourceRead]
