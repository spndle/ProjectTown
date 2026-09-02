from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class QuestStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TraceLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QuestCreate(BaseModel):
    goal: str | None = Field(default=None, min_length=3, max_length=2_000)
    template_id: str | None = Field(default=None, max_length=80)
    workspace: str | None = Field(default=None, max_length=240)

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if len(value) < 3:
            raise ValueError("goal must contain at least 3 non-whitespace characters")
        return value

    @field_validator("template_id", "workspace")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class Milestone(BaseModel):
    id: str
    position: int
    title: str
    description: str
    status: MilestoneStatus
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    started_at: str | None = None
    finished_at: str | None = None


class Quest(BaseModel):
    id: str
    goal: str
    template_id: str | None = None
    workspace: str
    status: QuestStatus
    current_step: int | None = None
    current_milestone_id: str | None = None
    progress: float = Field(ge=0.0, le=1.0)
    progress_percent: int = Field(ge=0, le=100)
    milestones: list[Milestone] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class QuestList(BaseModel):
    items: list[Quest]
    total: int


class RunAccepted(BaseModel):
    quest_id: str
    status: QuestStatus
    message: str


class Trace(BaseModel):
    id: int
    quest_id: str
    sequence: int
    trace_type: str
    level: TraceLevel
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    created_at: str


class TraceList(BaseModel):
    items: list[Trace]
    total: int


class QuestTemplate(BaseModel):
    id: str
    name: str
    description: str
    goal_example: str
    estimated_steps: int
    expected_artifacts: list[str]


class TemplateList(BaseModel):
    items: list[QuestTemplate]
    total: int


class HealthResponse(BaseModel):
    status: str
    version: str
    agent: str
    database: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
