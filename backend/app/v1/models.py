from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainModel(BaseModel):
    """Strict base model used by the v1 runtime and API."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class QuestStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    RUNNING = "running"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    WAITING_USER = "waiting_user"
    PAUSED = "paused"
    RECOVERING = "recovering"
    DISCARDING = "discarding"
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ReceiptStatus(str, Enum):
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN_EFFECT = "unknown_effect"


class DecisionKind(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class Budget(DomainModel):
    max_steps: int = Field(default=50, ge=1, le=10_000)
    max_tool_calls: int = Field(default=50, ge=1, le=10_000)
    max_messages: int = Field(default=30, ge=1, le=10_000)
    max_tokens: int = Field(default=20_000, ge=0, le=10_000_000)
    max_seconds: float = Field(default=300.0, gt=0, le=86_400)
    max_replans: int = Field(default=2, ge=0, le=20)


class BudgetUsage(DomainModel):
    steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    messages: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)


class AcceptanceCriterion(DomainModel):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal[
        "file_exists_nonempty",
        "markdown",
        "python_syntax",
        "json_schema",
        "diff_scope",
    ]
    description: str = Field(min_length=1, max_length=2_000)
    path: str | None = Field(default=None, max_length=500)
    required_keys: list[str] = Field(default_factory=list, max_length=200)
    allowed_paths: list[str] = Field(default_factory=list, max_length=200)
    changed_paths: list[str] = Field(default_factory=list, max_length=2_000)
    required: bool = True


class GoalContract(DomainModel):
    id: str = Field(min_length=1, max_length=120)
    quest_id: str = Field(min_length=1, max_length=120)
    version: int = Field(default=1, ge=1)
    goal: str = Field(min_length=3, max_length=2_000)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    non_goals: list[str] = Field(default_factory=list, max_length=200)
    budget: Budget = Field(default_factory=Budget)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=200)
    confirmed: bool = False
    created_at: str | None = None
    confirmed_at: str | None = None


class QuestCreate(DomainModel):
    goal: str = Field(min_length=3, max_length=2_000)
    workspace: str | None = Field(default=None, max_length=240)
    template_id: str | None = Field(default=None, max_length=80)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    non_goals: list[str] = Field(default_factory=list, max_length=200)
    budget: Budget = Field(default_factory=Budget)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    force_multi_agent: bool | None = None
    artifact_review_required: bool = False

    @field_validator("goal")
    @classmethod
    def strip_goal(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if len(normalized) < 3:
            raise ValueError("goal must contain at least 3 non-whitespace characters")
        return normalized

    @field_validator("workspace", "template_id")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class QuestConfirm(DomainModel):
    expected_state_version: int = Field(ge=1)
    expected_contract_version: int = Field(default=1, ge=1)
    approved: bool = True
    goal: str | None = Field(default=None, min_length=3, max_length=2_000)
    constraints: list[str] | None = Field(default=None, max_length=200)
    non_goals: list[str] | None = Field(default=None, max_length=200)
    budget: Budget | None = None
    acceptance_criteria: list[AcceptanceCriterion] | None = None
    note: str | None = Field(default=None, max_length=2_000)


class QuestControl(DomainModel):
    expected_state_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)


class ArtifactReview(DomainModel):
    expected_state_version: int = Field(ge=1)
    review_id: str = Field(min_length=1, max_length=120)
    manifest_hash: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    decision: Literal["retain", "discard"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2_000)


class DecisionCreate(DomainModel):
    kind: DecisionKind
    expected_state_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2_000)
    contract_patch: dict[str, Any] = Field(default_factory=dict)


class Milestone(DomainModel):
    id: str
    plan_version: int = Field(default=1, ge=1)
    position: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2_000)
    dependencies: list[str] = Field(default_factory=list)
    status: MilestoneStatus = MilestoneStatus.PENDING
    tool_name: str = Field(min_length=1, max_length=120)
    tool_args: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    attempt: int = Field(default=0, ge=0)


class Quest(DomainModel):
    id: str
    goal: str
    workspace: str
    status: QuestStatus = QuestStatus.DRAFT
    state_version: int = Field(ge=1)
    plan_id: str | None = None
    plan_version: int = Field(default=1, ge=0)
    plan_metadata: dict[str, Any] = Field(default_factory=dict)
    template_id: str | None = None
    contract: GoalContract | None
    milestones: list[Milestone] = Field(default_factory=list)
    current_milestone_id: str | None = None
    progress: float = Field(default=0, ge=0, le=1)
    route: list[str] = Field(default_factory=list, max_length=3)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    pause_requested: bool = False
    recovery_required: bool = False
    pending_approval: dict[str, Any] | None = None
    legacy_unverified: bool = False
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class FailureCategory(str, Enum):
    CONTRACT_VALIDATION = "contract_validation"
    BUDGET_RATE_LIMIT = "budget_rate_limit"
    TOOL_POLICY = "tool_policy"
    TOOL_EXECUTION = "tool_execution"
    UNKNOWN_EFFECT = "unknown_effect"
    VERIFIER_EVIDENCE = "verifier_evidence"
    ARTIFACT_REVIEW = "artifact_review"
    RECOVERY_CHECKPOINT = "recovery_checkpoint"
    WATCHDOG_DAG = "watchdog_dag"
    MODEL_PROVIDER = "model_provider"
    INTERNAL_RUNTIME = "internal_runtime"


class FailureSummary(DomainModel):
    category: FailureCategory
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Z0-9_]+$")
    message: str = Field(min_length=1, max_length=240)
    recoverable: bool


class FailureEventReference(DomainModel):
    id: int = Field(ge=1)
    sequence: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_]+$")


class FailureReceiptReference(DomainModel):
    action_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$",
    )
    status: ReceiptStatus


class FailureCheckpointReference(DomainModel):
    present: bool
    valid: bool
    state_version: int | None = Field(default=None, ge=0)


class FailureArtifactReviewReference(DomainModel):
    pending: bool
    review_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$",
    )


class FailureNavigation(DomainModel):
    milestone_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$",
    )
    event: FailureEventReference | None = None
    decision_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$",
    )
    evidence_ids: list[
        Annotated[
            str,
            Field(
                min_length=1,
                max_length=120,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$",
            ),
        ]
    ] = Field(default_factory=list, max_length=20)
    receipt: FailureReceiptReference | None = None
    checkpoint: FailureCheckpointReference
    artifact_review: FailureArtifactReviewReference


class FailureNavigationResponse(DomainModel):
    quest_id: str = Field(min_length=1, max_length=120)
    status: QuestStatus
    state_version: int = Field(ge=0)
    updated_at: str = Field(min_length=1, max_length=80)
    summary: FailureSummary
    navigation: FailureNavigation


class Event(DomainModel):
    id: int
    quest_id: str
    sequence: int = Field(ge=1)
    event_type: str
    event_schema_version: int = Field(default=1, ge=1)
    state_version_before: int = Field(ge=0)
    state_version_after: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str


class Evidence(DomainModel):
    id: str
    quest_id: str | None = None
    milestone_id: str | None = None
    criterion_id: str
    criterion_version: int = Field(default=1, ge=1)
    verifier: str
    verifier_version: str = "1.0"
    artifact_path: str | None = None
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    action_attempt: str | None = None
    source_event_sequence: int | None = Field(default=None, ge=1)
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class VerificationResult(DomainModel):
    id: str
    quest_id: str | None = None
    milestone_id: str | None = None
    criterion_id: str
    criterion_version: int = Field(default=1, ge=1)
    verifier: str = "projecttown.deterministic"
    verifier_version: str = "1.0"
    passed: bool
    evidence: Evidence
    reason: str | None = None
    created_at: str | None = None


class Decision(DomainModel):
    id: str
    quest_id: str
    kind: DecisionKind
    note: str
    state_version: int = Field(ge=1)
    created_at: str


class ActionProposal(DomainModel):
    id: str
    quest_id: str
    milestone_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    expected_state_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)


class ToolReceipt(DomainModel):
    action_id: str
    idempotency_key: str
    status: ReceiptStatus
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    side_effect_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


QuestCreateRequest = QuestCreate
QuestConfirmRequest = QuestConfirm
QuestControlRequest = QuestControl
Receipt = ToolReceipt
ToolAction = ActionProposal
