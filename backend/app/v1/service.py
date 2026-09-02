from __future__ import annotations

import copy
import hashlib
import re
import stat
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from ..agent import RuleBasedAgent
from ..errors import AppError
from ..runtime import ProgressWatchdog, SparseMessageBus, stable_hash
from ..tools import Sandbox, ToolRegistry
from ..utils import utc_now
from .gateway import ToolGateway
from .models import (
    AcceptanceCriterion,
    ArtifactReview,
    DecisionCreate,
    GoalContract,
    QuestConfirm,
    QuestCreate,
)
from .orchestration import build_handoff, compile_draft, replan_plan
from .provenance import classify_artifact_provenance, scan_sandbox_workspace
from .storage import V1Storage
from .verifier import Verifier

ACTIVE_STATUSES = {"running", "verifying", "replanning", "recovering"}
TERMINAL_STATUSES = {"completed", "budget_exhausted", "failed"}
ARTIFACT_PREVIEW_BYTES = 256_000

_FAILURE_CATEGORY_BY_CODE = {
    "GOAL_CONTRACT_REJECTED": (
        "contract_validation",
        "GOAL_CONTRACT_REJECTED",
        "The goal contract needs revision.",
        True,
    ),
    "BUDGET_EXHAUSTED": (
        "budget_rate_limit",
        "BUDGET_EXHAUSTED",
        "The configured execution budget was exhausted.",
        True,
    ),
    "REPLAN_BUDGET_EXHAUSTED": (
        "budget_rate_limit",
        "REPLAN_BUDGET_EXHAUSTED",
        "The replanning budget was exhausted.",
        True,
    ),
    "TOOL_POLICY_DENIED": (
        "tool_policy",
        "TOOL_POLICY_DENIED",
        "A requested tool operation was not permitted.",
        True,
    ),
    "TOOL_FAILED": (
        "tool_execution",
        "TOOL_FAILED",
        "A tool operation did not complete.",
        True,
    ),
    "UNKNOWN_EFFECT": (
        "unknown_effect",
        "UNKNOWN_EFFECT",
        "A tool effect requires reconciliation.",
        True,
    ),
    "FINAL_VERIFICATION_FAILED": (
        "verifier_evidence",
        "FINAL_VERIFICATION_FAILED",
        "Independent verification did not pass.",
        True,
    ),
    "CHECKPOINT_INVALID": (
        "recovery_checkpoint",
        "CHECKPOINT_INVALID",
        "Recovery checkpoint validation did not pass.",
        True,
    ),
    "PROCESS_INTERRUPTED": (
        "recovery_checkpoint",
        "PROCESS_INTERRUPTED",
        "Execution was interrupted and requires recovery.",
        True,
    ),
    "LOOP_DETECTED": (
        "watchdog_dag",
        "LOOP_DETECTED",
        "Execution progress requires constrained replanning.",
        True,
    ),
    "MODEL_PROVIDER_FAILURE": (
        "model_provider",
        "MODEL_PROVIDER_FAILURE",
        "The model provider did not complete the evaluation.",
        True,
    ),
    "RUNTIME_ERROR": (
        "internal_runtime",
        "RUNTIME_ERROR",
        "The runtime could not complete the Quest.",
        False,
    ),
}
_FAILURE_CONTEXT_UNAVAILABLE = (
    "internal_runtime",
    "FAILURE_CONTEXT_UNAVAILABLE",
    "No failure context is available for this Quest.",
    False,
)
_ARTIFACT_REVIEW_PENDING = (
    "artifact_review",
    "ARTIFACT_REVIEW_PENDING",
    "Artifacts are awaiting an explicit user review decision.",
    True,
)
_SAFE_NAVIGATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


def _safe_navigation_id(value: Any) -> str | None:
    candidate = str(value) if value is not None else ""
    return candidate if _SAFE_NAVIGATION_ID.fullmatch(candidate) else None


class V1QuestService:
    """Event-sourced, deterministic v1 Quest orchestrator."""

    def __init__(
        self,
        *,
        storage: V1Storage,
        agent: RuleBasedAgent,
        sandbox: Sandbox,
        tools: ToolRegistry,
        gateway: ToolGateway,
        max_workers: int = 2,
        lease_seconds: float = 30.0,
        watchdog_threshold: int = 3,
    ) -> None:
        self.storage = storage
        self.agent = agent
        self.sandbox = sandbox
        self.tools = tools
        self.gateway = gateway
        self.verifier = Verifier(sandbox, tools)
        self.lease_seconds = lease_seconds
        self.watchdog_threshold = watchdog_threshold
        self._owner = f"runtime-{uuid.uuid4().hex[:12]}"
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="projecttown-v1",
        )
        self._futures: dict[str, Future[None]] = {}
        self._future_lock = Lock()
        self._artifact_review_lock = Lock()
        self._closed = False
        self._messages = SparseMessageBus()
        self.recovery_summary = self._recover_interrupted()

    def close(self, wait: bool = True) -> None:
        self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def create_quest(self, payload: QuestCreate) -> dict[str, Any]:
        quest_id = f"qv1_{uuid.uuid4().hex[:12]}"
        if payload.artifact_review_required and payload.workspace is not None:
            raise AppError(
                "ARTIFACT_REVIEW_WORKSPACE_FORBIDDEN",
                "Artifact review quests use a service-owned workspace",
                status_code=422,
            )
        workspace = payload.workspace or f"quests/{quest_id}"
        try:
            if (
                payload.artifact_review_required
                and self.sandbox.workspace_path(workspace).exists()
            ):
                raise AppError(
                    "ARTIFACT_REVIEW_WORKSPACE_EXISTS",
                    "Service-owned artifact review workspace already exists",
                    status_code=409,
                )
            self.sandbox.workspace_path(workspace, create=True)
        except AppError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "INVALID_WORKSPACE")
            message = getattr(exc, "message", str(exc))
            details = getattr(exc, "details", {})
            raise AppError(code, message, status_code=422, details=details) from exc

        _, template_id, contract, plan, route = compile_draft(
            self.agent, payload, quest_id
        )
        plan.setdefault("metadata", {})["template_id"] = template_id
        created = self.storage.create_draft(
            quest_id,
            contract,
            plan,
            workspace=workspace,
            route=route,
        )
        if payload.artifact_review_required:
            self.storage.append_event(
                quest_id,
                "ArtifactReviewConfigured",
                {
                    "artifact_review_required": True,
                    "artifact_disposition": "pending",
                    "pending_artifact_review": None,
                },
                created["state_version"],
            )
            return self.get_quest(quest_id)
        return created

    def list_quests(self) -> list[dict[str, Any]]:
        return self.storage.list_quests()

    def search_quests(
        self,
        *,
        q: str | None,
        statuses: list[str],
        offset: int,
        limit: int | None,
    ) -> tuple[list[dict[str, Any]], int]:
        return self.storage.search_quests(
            q=q,
            statuses=statuses,
            offset=offset,
            limit=limit,
        )

    def list_templates(self) -> list[dict[str, Any]]:
        return self.agent.list_templates()

    def get_quest(self, quest_id: str) -> dict[str, Any]:
        state = self.storage.get_quest(quest_id)
        if state is None:
            raise AppError(
                "QUEST_NOT_FOUND",
                f"Quest '{quest_id}' was not found",
                status_code=404,
                details={"quest_id": quest_id},
            )
        return state

    def get_failure_navigation(self, quest_id: str) -> dict[str, Any]:
        """Return a fixed-message, read-only failure navigation projection."""
        raw = self.storage.failure_navigation_inputs(quest_id)
        if raw is None:
            raise AppError(
                "QUEST_NOT_FOUND",
                f"Quest '{quest_id}' was not found",
                status_code=404,
                details={"quest_id": quest_id},
            )
        code = raw["error_code"]
        if raw["artifact_review_pending"]:
            summary = _ARTIFACT_REVIEW_PENDING
        elif code in _FAILURE_CATEGORY_BY_CODE:
            summary = _FAILURE_CATEGORY_BY_CODE[code]
        elif raw["action"] and raw["action"]["status"] == "unknown_effect":
            summary = _FAILURE_CATEGORY_BY_CODE["UNKNOWN_EFFECT"]
        elif raw["action"] and raw["action"]["status"] == "failed":
            summary = _FAILURE_CATEGORY_BY_CODE["TOOL_FAILED"]
        elif raw["status"] in {"failed", "budget_exhausted", "recovering"} or code:
            summary = _FAILURE_CATEGORY_BY_CODE["RUNTIME_ERROR"]
        else:
            summary = _FAILURE_CONTEXT_UNAVAILABLE
        event = raw["event"]
        safe_event = None
        if event is not None and str(event["event_type"]).replace("_", "").isalnum():
            safe_event = {
                "id": int(event["event_id"]),
                "sequence": int(event["sequence"]),
                "type": str(event["event_type"]),
            }
        action = raw["action"]
        safe_receipt = None
        action_id = (
            _safe_navigation_id(action["action_id"]) if action is not None else None
        )
        if action is not None and action_id is not None:
            safe_receipt = {"action_id": action_id, "status": action["status"]}
        safe_milestone = _safe_navigation_id(raw["milestone_id"])
        return {
            "quest_id": raw["quest_id"],
            "status": raw["status"],
            "state_version": raw["state_version"],
            "updated_at": raw["updated_at"],
            "summary": {
                "category": summary[0],
                "code": summary[1],
                "message": summary[2],
                "recoverable": summary[3],
            },
            "navigation": {
                "milestone_id": safe_milestone,
                "event": safe_event,
                "decision_id": _safe_navigation_id(raw["decision_id"]),
                "evidence_ids": [
                    evidence_id
                    for value in raw["evidence_ids"]
                    if (evidence_id := _safe_navigation_id(value)) is not None
                ],
                "receipt": safe_receipt,
                "checkpoint": raw["checkpoint"],
                "artifact_review": {
                    "pending": raw["artifact_review_pending"],
                    "review_id": _safe_navigation_id(raw["review_id"]),
                },
            },
        }

    def confirm_quest(self, quest_id: str, payload: QuestConfirm) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        if state["status"] != "draft":
            raise AppError(
                "QUEST_NOT_CONFIRMABLE",
                f"Quest in '{state['status']}' state cannot be confirmed",
                status_code=409,
            )
        if state["state_version"] != payload.expected_state_version:
            raise AppError(
                "STATE_VERSION_CONFLICT",
                "Quest changed after the confirmation form was loaded",
                status_code=409,
            )
        if not payload.approved:
            event = self.storage.append_event(
                quest_id,
                "GoalContractRejected",
                {
                    "status": "draft",
                    "error": {
                        "code": "GOAL_CONTRACT_REJECTED",
                        "message": "Goal Contract requires revision",
                    },
                },
                state["state_version"],
            )
            return self.get_quest(event["quest_id"])

        contract = copy.deepcopy(state["contract"])
        if int(contract["version"]) != payload.expected_contract_version:
            raise AppError(
                "CONTRACT_VERSION_CONFLICT",
                "Goal Contract version does not match",
                status_code=409,
            )
        contract["version"] = int(contract["version"]) + 1
        for field in ("goal", "constraints", "non_goals", "acceptance_criteria"):
            value = getattr(payload, field)
            if value is not None:
                contract[field] = (
                    [
                        item.model_dump(mode="json")
                        if hasattr(item, "model_dump")
                        else item
                        for item in value
                    ]
                    if isinstance(value, list)
                    else value
                )
        if payload.budget is not None:
            contract["budget"] = payload.budget.model_dump(mode="json")
        contract["confirmed"] = True
        contract["confirmed_at"] = utc_now()
        validated = GoalContract.model_validate(contract).model_dump(mode="json")
        try:
            self.storage.store_contract_version(
                quest_id,
                validated,
                expected_state_version=state["state_version"],
            )
        except ValueError as exc:
            raise AppError("STATE_VERSION_CONFLICT", str(exc), status_code=409) from exc
        return self.get_quest(quest_id)

    def start_quest(
        self, quest_id: str, expected_state_version: int | None = None
    ) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        try:
            self._check_expected_state_version(state, expected_state_version)
        except AppError as exc:
            latest = self.storage.get_quest(quest_id)
            if latest is not None and latest.get("status") in ACTIVE_STATUSES:
                raise AppError(
                    "QUEST_LEASE_CONFLICT",
                    "Quest already has an active executor",
                    status_code=409,
                ) from exc
            raise
        if state["status"] != "planned":
            raise AppError(
                "QUEST_NOT_RUNNABLE",
                f"Quest in '{state['status']}' state cannot be started",
                status_code=409,
            )
        return self._start_worker(state, transition=True)

    def pause_quest(
        self, quest_id: str, expected_state_version: int | None = None
    ) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        self._check_expected_state_version(state, expected_state_version)
        if state["status"] == "paused":
            return state
        if state["status"] not in ACTIVE_STATUSES:
            raise AppError(
                "QUEST_NOT_PAUSABLE",
                f"Quest in '{state['status']}' state cannot be paused",
                status_code=409,
            )
        self.storage.append_event(
            quest_id,
            "PauseRequested",
            {"pause_requested": True},
            state["state_version"],
        )
        return self.get_quest(quest_id)

    def resume_quest(
        self, quest_id: str, expected_state_version: int | None = None
    ) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        self._check_expected_state_version(state, expected_state_version)
        if state["status"] not in {"paused", "recovering"}:
            raise AppError(
                "QUEST_NOT_RESUMABLE",
                f"Quest in '{state['status']}' state cannot be resumed",
                status_code=409,
            )
        try:
            replayed = self.storage.replay(quest_id)
        except ValueError as exc:
            raise AppError("EVENT_REPLAY_FAILED", str(exc), status_code=409) from exc
        if stable_hash(replayed) != stable_hash(state):
            raise AppError(
                "STATE_CHECKSUM_MISMATCH",
                "Event replay does not match the current projection",
                status_code=409,
            )
        if self.storage.has_checkpoint(
            quest_id
        ) and not self.storage.validate_checkpoint(quest_id):
            raise AppError(
                "CHECKPOINT_INVALID",
                "Checkpoint checksum or replay validation failed",
                status_code=409,
            )

        unresolved = self.storage.list_unresolved_actions(quest_id)
        for action in unresolved:
            receipt = self.gateway.execute(
                action_id=action["action_id"],
                quest_id=quest_id,
                milestone_id=action["milestone_id"],
                idempotency_key=action["idempotency_key"],
                expected_state_version=action["expected_state_version"],
                workspace=state["workspace"],
                tool_name=action["tool_name"],
                arguments=action["arguments"],
                approved=self._action_is_approved(quest_id, action["action_id"]),
            )
            if receipt["status"] == "unknown_effect":
                self.storage.append_event(
                    quest_id,
                    "RecoveryNeedsUser",
                    {
                        "status": "waiting_user",
                        "recovery_required": True,
                        "error": receipt["error"],
                    },
                    state["state_version"],
                )
                return self.get_quest(quest_id)

        # Reconciliation may have atomically appended ToolCommitted, so the
        # pre-recovery projection version is no longer valid here.
        state = self.get_quest(quest_id)
        self.storage.append_event(
            quest_id,
            "RecoveryStarted",
            {
                "status": "recovering",
                "pause_requested": False,
                "milestones": state["milestones"],
            },
            state["state_version"],
        )
        state = self.get_quest(quest_id)
        self.storage.append_event(
            quest_id,
            "RecoveryCompleted",
            {"status": "running", "recovery_required": False, "error": None},
            state["state_version"],
        )
        return self._start_worker(self.get_quest(quest_id), transition=False)

    def submit_decision(self, quest_id: str, payload: DecisionCreate) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        if state["status"] != "waiting_user":
            raise AppError(
                "DECISION_NOT_REQUESTED",
                f"Quest in '{state['status']}' state is not waiting for a decision",
                status_code=409,
            )
        if state.get("pending_artifact_review"):
            raise AppError(
                "ARTIFACT_REVIEW_REQUIRED",
                "Use the artifact review endpoint to retain or discard the frozen results",
                status_code=409,
            )
        if state["state_version"] != payload.expected_state_version:
            raise AppError(
                "STATE_VERSION_CONFLICT", "Quest state changed", status_code=409
            )
        decision_id = f"decision_{uuid.uuid4().hex[:12]}"
        data = payload.model_dump(mode="json")
        data.update(
            {
                "id": decision_id,
                "quest_id": quest_id,
                "state_version": state["state_version"],
            }
        )
        kind = str(payload.kind)
        pending_approval = state.get("pending_approval")
        if kind == "approve" and pending_approval:
            expected_action_id = str(pending_approval["action_id"])
            if payload.contract_patch.get("action_id") != expected_action_id:
                raise AppError(
                    "APPROVAL_TARGET_MISMATCH",
                    "Approval must name the exact pending action",
                    status_code=422,
                    details={"pending_action_id": expected_action_id},
                )
        validated_contract: dict[str, Any] | None = None
        modified_plan: dict[str, Any] | None = None
        modified_route: list[str] | None = None
        if kind == "modify":
            unknown = set(payload.contract_patch) - {
                "goal",
                "constraints",
                "non_goals",
                "budget",
                "acceptance_criteria",
            }
            if not payload.contract_patch or unknown:
                raise AppError(
                    "INVALID_CONTRACT_PATCH",
                    "A modification requires only supported Goal Contract fields",
                    status_code=422,
                    details={"unknown_fields": sorted(unknown)},
                )
            contract = copy.deepcopy(state["contract"])
            contract.update(copy.deepcopy(payload.contract_patch))
            contract["version"] = int(contract["version"]) + 1
            contract["confirmed"] = True
            contract["confirmed_at"] = utc_now()
            try:
                validated_contract = GoalContract.model_validate(contract).model_dump(
                    mode="json"
                )
            except ValueError as exc:
                raise AppError(
                    "INVALID_CONTRACT_PATCH",
                    "Goal Contract modification is invalid",
                    status_code=422,
                    details={"validation_error": str(exc)},
                ) from exc
            _, _, _, modified_plan, modified_route = compile_draft(
                self.agent,
                QuestCreate(
                    goal=validated_contract["goal"],
                    workspace=state["workspace"],
                    template_id=state.get("template_id"),
                    constraints=validated_contract["constraints"],
                    non_goals=validated_contract["non_goals"],
                    budget=validated_contract["budget"],
                    acceptance_criteria=validated_contract["acceptance_criteria"],
                    force_multi_agent=len(state.get("route", [])) > 1,
                ),
                quest_id,
            )
            modified_plan["id"] = state.get("plan_id", modified_plan["id"])
            modified_plan["version"] = int(state["plan_version"]) + 1
            for milestone in modified_plan["milestones"]:
                milestone["plan_version"] = modified_plan["version"]
        events: list[tuple[str, dict[str, Any]]]
        if kind == "reject":
            events = [
                (
                    "UserRejected",
                    {
                        "status": "failed",
                        "finished_at": utc_now(),
                        "error": {"code": "USER_REJECTED", "message": payload.note},
                    },
                )
            ]
        elif kind == "modify":
            assert validated_contract is not None
            assert modified_plan is not None
            assert modified_route is not None
            events = [
                (
                    "GoalContractModified",
                    {
                        "contract": validated_contract,
                        "goal": str(validated_contract.get("goal", "")),
                        "status": "replanning",
                        "error": None,
                    },
                ),
                (
                    "PlanReplanned",
                    {
                        "plan_id": str(
                            modified_plan.get("id", state.get("plan_id", ""))
                        ),
                        "plan_version": int(modified_plan.get("version", 1)),
                        "plan_metadata": copy.deepcopy(
                            dict(modified_plan.get("metadata", {}))
                        ),
                        "milestones": modified_plan["milestones"],
                        "status": "running",
                    },
                ),
                (
                    "UserModificationApplied",
                    {
                        "status": "paused",
                        "route": modified_route,
                        "pending_approval": None,
                        "current_milestone_id": None,
                        "error": None,
                    },
                ),
            ]
        else:
            milestones = copy.deepcopy(state["milestones"])
            if pending_approval:
                for milestone in milestones:
                    if milestone["id"] == pending_approval["milestone_id"]:
                        milestone["status"] = "pending"
                        milestone["attempt"] = max(
                            0, int(milestone.get("attempt", 0)) - 1
                        )
            events = [
                (
                    "UserApproved",
                    {
                        "status": "paused",
                        "error": None,
                        "pending_approval": None,
                        "milestones": milestones,
                    },
                )
            ]
        try:
            return self.storage.apply_decision(
                quest_id,
                decision_id,
                data,
                expected_state_version=payload.expected_state_version,
                kind=kind,
                events=events,
                contract=validated_contract,
                plan=modified_plan,
            )
        except ValueError as exc:
            message = str(exc)
            if "approval target" in message:
                code, status_code = "APPROVAL_TARGET_MISMATCH", 422
            elif "completed milestone" in message:
                code, status_code = "COMPLETED_MILESTONE_IMMUTABLE", 409
            elif "decision not requested" in message:
                code, status_code = "DECISION_NOT_REQUESTED", 409
            else:
                code, status_code = "STATE_VERSION_CONFLICT", 409
            raise AppError(code, message, status_code=status_code) from exc

    def get_events(
        self, quest_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        self.get_quest(quest_id)
        return self.storage.list_events(quest_id, after_sequence)

    def get_evidence(self, quest_id: str) -> list[dict[str, Any]]:
        self.get_quest(quest_id)
        return self.storage.list_evidence(quest_id)

    def get_artifacts(self, quest_id: str) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        if not state.get("artifact_review_required"):
            raise AppError(
                "ARTIFACT_REVIEW_NOT_ENABLED",
                "Artifact review was not requested",
                status_code=404,
            )
        pending = state.get("pending_artifact_review") or {}
        return {
            "review": {
                "status": state["status"],
                "disposition": state.get("artifact_disposition"),
                "review_id": pending.get("review_id"),
                "manifest_hash": pending.get("manifest_hash"),
                "pending": pending,
            },
            "artifact_disposition": state.get("artifact_disposition"),
            "items": state.get("artifact_manifest", []),
        }

    def preview_artifact(self, quest_id: str, artifact_id: str) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        item = next(
            (
                candidate
                for candidate in state.get("artifact_manifest", [])
                if candidate["artifact_id"] == artifact_id
            ),
            None,
        )
        if item is None:
            raise AppError(
                "ARTIFACT_NOT_FOUND", "Artifact was not found", status_code=404
            )
        path = self._verified_artifact_path(state, item)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(
                "ARTIFACT_NOT_TEXT", "Artifact is not UTF-8 text", status_code=409
            ) from exc
        return {
            "artifact_id": artifact_id,
            "path": item["path"],
            "content": content,
            "size": item["size"],
            "hash": item["hash"],
        }

    def review_artifacts(
        self, quest_id: str, payload: ArtifactReview
    ) -> dict[str, Any]:
        # Filesystem effects cannot share SQLite's transaction. The release is
        # single-process, so serialize review decisions while the durable
        # intent/receipt protocol coordinates retry and startup recovery.
        with self._artifact_review_lock:
            return self._review_artifacts_locked(quest_id, payload)

    def _review_artifacts_locked(
        self, quest_id: str, payload: ArtifactReview
    ) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        if not state.get("artifact_review_required"):
            raise AppError(
                "ARTIFACT_REVIEW_NOT_ENABLED",
                "Artifact review was not requested",
                status_code=409,
            )
        receipt = self.storage.get_artifact_review_receipt(quest_id)
        if receipt is not None:
            self._validate_artifact_review_receipt(receipt, payload)
            if (
                payload.decision == "discard"
                and self.get_quest(quest_id)["status"] == "discarding"
            ):
                self._discard_artifacts(self.get_quest(quest_id))
            return self.get_quest(quest_id)
        if state["status"] != "waiting_user" or not state.get(
            "pending_artifact_review"
        ):
            raise AppError(
                "ARTIFACT_REVIEW_NOT_PENDING",
                "Quest is not awaiting artifact review",
                status_code=409,
            )
        pending = state["pending_artifact_review"]
        if (
            pending.get("review_id") != payload.review_id
            or pending.get("manifest_hash") != payload.manifest_hash
        ):
            raise AppError(
                "ARTIFACT_REVIEW_CONFLICT",
                "Artifact review does not match the frozen manifest",
                status_code=409,
            )
        self._check_expected_state_version(state, payload.expected_state_version)
        if payload.decision == "retain":
            self._verify_artifact_manifest(state)
        try:
            receipt, event = self.storage.begin_artifact_review(
                quest_id,
                review_id=payload.review_id,
                manifest_hash=payload.manifest_hash,
                idempotency_key=payload.idempotency_key,
                decision=payload.decision,
                note=payload.note,
                expected_state_version=state["state_version"],
                event_type=(
                    "ArtifactAccepted"
                    if payload.decision == "retain"
                    else "ArtifactDiscardRequested"
                ),
                patch=(
                    {
                        "artifact_disposition": "retained",
                        "pending_artifact_review": None,
                    }
                    if payload.decision == "retain"
                    else {
                        "status": "discarding",
                        "artifact_disposition": "discarding",
                        "pending_artifact_review": None,
                    }
                ),
                final_event=(
                    (
                        "QuestCompleted",
                        {
                            "status": "completed",
                            "progress": 1.0,
                            "current_milestone_id": None,
                            "finished_at": utc_now(),
                            "error": None,
                        },
                    )
                    if payload.decision == "retain"
                    else None
                ),
            )
        except ValueError as exc:
            raise AppError(
                "STATE_VERSION_CONFLICT", "Quest state changed", status_code=409
            ) from exc
        if event is None:
            self._validate_artifact_review_receipt(receipt, payload)
            return self.get_quest(quest_id)
        if payload.decision == "retain":
            self.storage.save_checkpoint(quest_id)
            return self.get_quest(quest_id)
        self._discard_artifacts(self.get_quest(quest_id))
        return self.get_quest(quest_id)

    @staticmethod
    def _validate_artifact_review_receipt(
        receipt: dict[str, Any], payload: ArtifactReview
    ) -> None:
        expected = {
            "review_id": payload.review_id,
            "manifest_hash": payload.manifest_hash,
            "idempotency_key": payload.idempotency_key,
            "decision": payload.decision,
            "note": payload.note,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "Review request conflicts with the recorded decision",
                status_code=409,
            )

    def _verify_artifact_manifest(self, state: dict[str, Any]) -> None:
        manifest = state.get("artifact_manifest", [])
        if not manifest:
            raise AppError(
                "ARTIFACT_MANIFEST_EMPTY",
                "No previewable verified artifacts were produced",
                status_code=409,
            )
        for item in manifest:
            self._verified_artifact_path(state, item)
            self._verify_artifact_provenance_reference(state, item)

    def _verify_artifact_provenance_reference(
        self, state: dict[str, Any], item: dict[str, Any]
    ) -> None:
        """Validate an optional shadow-provenance binding before disposition.

        Historical reviews do not contain a provenance id and intentionally keep
        their original retain/discard behaviour.  A new review which advertises
        provenance, however, must still be bound to its immutable side ledger.
        """

        provenance_id = item.get("provenance_id")
        if provenance_id is None:
            return
        if not isinstance(provenance_id, str) or not provenance_id:
            raise AppError(
                "ARTIFACT_PROVENANCE_INVALID",
                "Artifact provenance reference is invalid",
                status_code=409,
            )
        row = self.storage.get_artifact_provenance(provenance_id)
        expected_status = item.get("provenance_status")
        expected_mode = item.get("provenance_mode")
        expected_storage_status = (
            "shadow"
            if isinstance(expected_status, str)
            and expected_status.startswith("shadow_")
            else "legacy_unobserved"
            if expected_status == "legacy_unobserved"
            else "unrecoverable"
            if isinstance(expected_status, str)
            and expected_status.startswith("unrecoverable_")
            else None
        )
        if (
            row is None
            or expected_mode != "compatibility_shadow"
            or expected_storage_status is None
            or row.get("quest_id") != state["id"]
            or row.get("artifact_id") != item.get("artifact_id")
            or row.get("artifact_hash") != item.get("hash")
            or row.get("evidence_id") != item.get("evidence_id")
            or row.get("baseline_snapshot_id") != item.get("baseline_snapshot_id")
            or row.get("final_snapshot_id") != item.get("final_snapshot_id")
            or row.get("status") != expected_storage_status
        ):
            raise AppError(
                "ARTIFACT_PROVENANCE_INVALID",
                "Artifact provenance does not match the frozen manifest",
                status_code=409,
            )

    def _verified_artifact_path(
        self, state: dict[str, Any], item: dict[str, Any]
    ) -> Path:
        if item.get("created_by_quest") is not True:
            raise AppError(
                "ARTIFACT_OWNERSHIP_INVALID",
                "Artifact is not marked as created by this Quest",
                status_code=409,
            )
        path = self._artifact_lexical_path(state, item)
        if not path.exists():
            raise AppError(
                "ARTIFACT_PATH_INVALID",
                "Artifact path is unavailable",
                status_code=409,
            )
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item["size"]
        ):
            raise AppError(
                "ARTIFACT_CHANGED",
                "Artifact is no longer the frozen regular file",
                status_code=409,
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["hash"]:
            raise AppError(
                "ARTIFACT_HASH_MISMATCH",
                "Artifact no longer matches the frozen hash",
                status_code=409,
            )
        return path

    def _artifact_lexical_path(
        self, state: dict[str, Any], item: dict[str, Any]
    ) -> Path:
        relative = Path(str(item.get("path", "")))
        if (
            not relative.parts
            or relative.is_absolute()
            or relative.drive
            or any(part in {".", ".."} for part in relative.parts)
        ):
            raise AppError(
                "ARTIFACT_PATH_INVALID",
                "Artifact path is not a safe relative path",
                status_code=409,
            )
        current = self.sandbox.workspace_path(state["workspace"])
        for part in relative.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                file_attributes = getattr(current.lstat(), "st_file_attributes", 0)
                if current.is_symlink() or file_attributes & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ):
                    raise AppError(
                        "ARTIFACT_PATH_INVALID",
                        "Artifact path contains a symbolic link or reparse point",
                        status_code=409,
                    )
        return current

    def _discard_artifacts(self, state: dict[str, Any]) -> None:
        for item in state.get("artifact_manifest", []):
            path = self._artifact_lexical_path(state, item)
            if path.exists() or path.is_symlink():
                self._verified_artifact_path(state, item).unlink()
        latest = self.get_quest(state["id"])
        if latest["status"] == "discarding":
            self.storage.finalize_artifact_discard(
                latest["id"], expected_state_version=latest["state_version"]
            )

    def get_decisions(self, quest_id: str) -> list[dict[str, Any]]:
        self.get_quest(quest_id)
        return self.storage.list_decisions(quest_id)

    @staticmethod
    def _check_expected_state_version(
        state: dict[str, Any], expected_state_version: int | None
    ) -> None:
        if (
            expected_state_version is not None
            and state["state_version"] != expected_state_version
        ):
            raise AppError(
                "STATE_VERSION_CONFLICT",
                "Quest changed after the control was prepared",
                status_code=409,
                details={
                    "expected_state_version": expected_state_version,
                    "actual_state_version": state["state_version"],
                },
            )

    def _start_worker(
        self, state: dict[str, Any], *, transition: bool
    ) -> dict[str, Any]:
        if self._closed:
            raise AppError(
                "SERVICE_UNAVAILABLE", "Runtime is shutting down", status_code=503
            )
        quest_id = state["id"]
        if transition:
            try:
                event = self.storage.append_event(
                    quest_id,
                    "QuestStarted",
                    {"status": "running", "error": None},
                    state["state_version"],
                )
            except ValueError as exc:
                latest = self.storage.get_quest(quest_id)
                if latest is not None and latest.get("status") in ACTIVE_STATUSES:
                    raise AppError(
                        "QUEST_LEASE_CONFLICT",
                        "Quest already has an active executor",
                        status_code=409,
                    ) from exc
                raise AppError(
                    "STATE_VERSION_CONFLICT", "Quest state changed", status_code=409
                ) from exc
            state = self.get_quest(event["quest_id"])
        try:
            future = self._executor.submit(self._execute_quest, quest_id)
        except Exception as exc:
            latest = self.storage.get_quest(quest_id)
            if latest is not None and latest["status"] not in TERMINAL_STATUSES:
                self.storage.append_event(
                    quest_id,
                    "QuestSchedulingFailed",
                    {
                        "status": "failed",
                        "finished_at": utc_now(),
                        "error": {
                            "code": "SCHEDULING_FAILED",
                            "message": "Quest could not be scheduled",
                        },
                    },
                    latest["state_version"],
                )
            raise AppError(
                "SCHEDULING_FAILED",
                "Quest could not be scheduled",
                status_code=503,
            ) from exc
        with self._future_lock:
            self._futures[quest_id] = future
        future.add_done_callback(lambda _: self._forget_future(quest_id))
        return state

    def _forget_future(self, quest_id: str) -> None:
        with self._future_lock:
            self._futures.pop(quest_id, None)

    def _lease_owner(self, quest_id: str) -> str:
        return f"{self._owner}:{quest_id}:{uuid.uuid4().hex}"

    def _execute_quest(self, quest_id: str) -> None:
        owner = self._lease_owner(quest_id)
        stop_heartbeat = Event()
        lease_lost = Event()
        heartbeat: Thread | None = None
        try:
            state = self.get_quest(quest_id)
            if self._closed:
                if state.get("status") in ACTIVE_STATUSES:
                    try:
                        self.storage.append_event(
                            quest_id,
                            "QuestExecutionCancelled",
                            {
                                "status": "paused",
                                "pause_requested": False,
                                "recovery_required": True,
                                "error": {
                                    "code": "SERVICE_SHUTDOWN",
                                    "message": "Runtime is shutting down",
                                },
                            },
                            state["state_version"],
                        )
                    except ValueError:
                        pass
                return
            if state.get("pause_requested"):
                self.storage.append_event(
                    quest_id,
                    "QuestPaused",
                    {
                        "status": "paused",
                        "pause_requested": False,
                        "recovery_required": False,
                    },
                    state["state_version"],
                )
                self.storage.save_checkpoint(quest_id)
                return
            admitted = self.storage.admit_execution(
                quest_id, owner, self.lease_seconds, state["state_version"], utc_now()
            )
            if admitted is None:
                return
            heartbeat = self._start_lease_heartbeat(
                quest_id, owner, stop_heartbeat, lease_lost
            )
            if not self._record_execution_baseline(admitted, owner, lease_lost):
                return
            while True:
                if lease_lost.is_set():
                    return
                state = self.get_quest(quest_id)
                if state["status"] not in ACTIVE_STATUSES:
                    return
                if state.get("pause_requested"):
                    self.storage.append_event(
                        quest_id,
                        "QuestPaused",
                        {
                            "status": "paused",
                            "pause_requested": False,
                            "recovery_required": False,
                        },
                        state["state_version"],
                    )
                    self.storage.save_checkpoint(quest_id)
                    return

                ready = self._ready_milestones(state)
                if not ready:
                    if lease_lost.is_set():
                        return
                    if all(
                        item.get("status") == "completed"
                        for item in state["milestones"]
                    ):
                        self._finalize_quest(state, lease_lost, owner)
                    else:
                        self._fail_quest(
                            state,
                            "DAG_STALLED",
                            "No milestone is ready and the Quest is incomplete",
                        )
                    return
                milestone = ready[0]
                budget_reason = self._budget_reason(state, milestone)
                if budget_reason:
                    if lease_lost.is_set():
                        return
                    self.storage.append_event(
                        quest_id,
                        "BudgetExhausted",
                        {
                            "status": "budget_exhausted",
                            "finished_at": utc_now(),
                            "error": {
                                "code": "BUDGET_EXHAUSTED",
                                "message": budget_reason,
                            },
                        },
                        state["state_version"],
                    )
                    return
                try:
                    if not self._execute_milestone(state, milestone, owner, lease_lost):
                        return
                except ValueError as exc:
                    if "state version conflict" in str(exc):
                        continue
                    raise
        except Exception as exc:  # noqa: BLE001 - persistent worker boundary
            state = self.storage.get_quest(quest_id)
            if (
                not lease_lost.is_set()
                and state
                and state.get("status") not in TERMINAL_STATUSES
            ):
                self._fail_quest(
                    state,
                    "RUNTIME_ERROR",
                    "Unexpected runtime failure",
                    details={"exception_type": type(exc).__name__},
                )
        finally:
            stop_heartbeat.set()
            if heartbeat is not None:
                heartbeat.join(timeout=max(1.0, self.lease_seconds))
                if heartbeat.is_alive():
                    lease_lost.set()
                    raise RuntimeError("lease heartbeat did not stop")
            self.storage.release_lease(quest_id, owner)

    def _record_execution_baseline(
        self, admitted: dict[str, Any], owner: str, lease_lost: Event
    ) -> bool:
        """Persist one pre-tool compatibility-shadow baseline, without events."""

        if lease_lost.is_set():
            return False
        quest_id = admitted["quest_id"]
        state_version = admitted["state_version_after"]
        event_sequence = admitted["sequence"]
        if self.storage.get_baseline_snapshot(quest_id) is not None:
            return not lease_lost.is_set()
        actions = self.storage.list_tool_actions(quest_id)
        identity = {
            "quest_id": quest_id,
            "state_version": state_version,
            "event_sequence": event_sequence,
            "workspace": self.get_quest(quest_id)["workspace"],
        }
        try:
            if actions:
                self.storage.record_legacy_unobserved_baseline(
                    f"baseline_{stable_hash({**identity, 'mode': 'legacy_unobserved'})[:24]}",
                    quest_id,
                    owner,
                    state_version,
                    event_sequence=event_sequence,
                )
            else:
                state = self.get_quest(quest_id)
                snapshot = scan_sandbox_workspace(self.sandbox, state["workspace"])
                self.storage.save_baseline_snapshot(
                    f"baseline_{stable_hash({**identity, 'policy_version': snapshot.policy_version, 'status': snapshot.status, 'root_hash': snapshot.root_hash})[:24]}",
                    quest_id,
                    owner,
                    state_version,
                    snapshot.to_dict(),
                    event_sequence=event_sequence,
                )
        except ValueError as exc:
            message = str(exc)
            if message in {
                "state version conflict",
                "live execution lease is not held by owner",
            }:
                # A lost lease or CAS conflict must stop this worker before
                # the first milestone/tool boundary.
                return False
            if message == "baseline snapshot already exists for quest":
                return (
                    self.storage.get_baseline_snapshot(quest_id) is not None
                    and not lease_lost.is_set()
                )
            raise
        return not lease_lost.is_set()

    def _start_lease_heartbeat(
        self, quest_id: str, owner: str, stop: Event, lost: Event
    ) -> Thread:
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        interval = self.lease_seconds / 3

        def renew() -> None:
            while not stop.wait(interval):
                try:
                    if not self.storage.renew_lease(
                        quest_id, owner, self.lease_seconds
                    ):
                        lost.set()
                        return
                except Exception:  # noqa: BLE001 - lease loss must stop execution.
                    lost.set()
                    return

        heartbeat = Thread(
            target=renew, name=f"projecttown-lease-heartbeat-{quest_id}", daemon=True
        )
        heartbeat.start()
        return heartbeat

    @staticmethod
    def _ready_milestones(state: dict[str, Any]) -> list[dict[str, Any]]:
        completed = {
            item["id"]
            for item in state["milestones"]
            if item.get("status") == "completed"
        }
        ready = [
            item
            for item in state["milestones"]
            if item.get("status") in {"pending", "running", "verifying"}
            and set(item.get("dependencies", [])) <= completed
        ]
        return sorted(ready, key=lambda item: (item.get("position", 0), item["id"]))

    def _budget_reason(
        self, state: dict[str, Any], milestone: dict[str, Any]
    ) -> str | None:
        budget = state["contract"]["budget"]
        usage = state["budget_usage"]
        fresh_attempt = milestone.get("status") == "pending"
        increment = 1 if fresh_attempt else 0
        if int(usage.get("steps", 0)) + increment > int(budget["max_steps"]):
            return "step budget exceeded"
        if int(usage.get("tool_calls", 0)) + increment > int(budget["max_tool_calls"]):
            return "tool-call budget exceeded"
        message_cost = 2 if fresh_attempt and len(state.get("route", [])) > 1 else 0
        if int(usage.get("messages", 0)) + message_cost > int(budget["max_messages"]):
            return "message budget exceeded"
        started_at = state.get("started_at")
        if started_at:
            try:
                elapsed = datetime.now().astimezone() - datetime.fromisoformat(
                    started_at
                )
                if elapsed.total_seconds() > float(budget["max_seconds"]):
                    return "time budget exceeded"
            except ValueError:
                return "invalid runtime start timestamp"
        if milestone["tool_name"] not in self.gateway.allowlist:
            return "plan references a tool outside the configured allowlist"
        return None

    def _execute_milestone(
        self,
        state: dict[str, Any],
        milestone: dict[str, Any],
        owner: str,
        lease_lost: Event,
    ) -> bool:
        if lease_lost.is_set():
            return False
        quest_id = state["id"]
        route = state.get("route", ["agent"])
        message_cost = 2 if len(route) > 1 else 0
        usage = copy.deepcopy(state["budget_usage"])
        fresh_attempt = milestone.get("status") == "pending"
        if fresh_attempt:
            usage["steps"] = int(usage.get("steps", 0)) + 1
            usage["tool_calls"] = int(usage.get("tool_calls", 0)) + 1
            usage["messages"] = int(usage.get("messages", 0)) + message_cost
        milestones = self._updated_milestones(
            state,
            milestone["id"],
            status="running",
            attempt=int(milestone.get("attempt", 0)) + (1 if fresh_attempt else 0),
        )
        patch: dict[str, Any] = {
            "status": "running",
            "current_milestone_id": milestone["id"],
            "milestones": milestones,
            "budget_usage": usage,
        }
        if message_cost and fresh_attempt:
            handoff = build_handoff(
                quest_id,
                state["state_version"],
                milestone.get("evidence_ids", []),
                "tool_receipt",
                payload={"milestone_id": milestone["id"]},
            )
            published = self._messages.publish(**handoff)
            patch["last_handoff"] = handoff
            patch["handoff_accepted"] = published.accepted
        event = self.storage.append_event(
            quest_id,
            "MilestoneStarted",
            patch,
            state["state_version"],
        )
        if lease_lost.is_set():
            return False
        state = self.get_quest(quest_id)
        milestone = self._milestone(state, milestone["id"])
        action_id = f"action_{stable_hash({'quest': quest_id, 'milestone': milestone['id'], 'attempt': milestone['attempt']})[:16]}"
        idempotency_key = f"{quest_id}:{milestone['id']}:{milestone['attempt']}"

        approved = self._action_is_approved(quest_id, action_id)
        if milestone["tool_name"] in self.gateway.high_risk_tools and not approved:
            if lease_lost.is_set():
                return False
            self.storage.append_event(
                quest_id,
                "ApprovalRequired",
                {
                    "status": "waiting_user",
                    "pending_approval": {
                        "action_id": action_id,
                        "milestone_id": milestone["id"],
                        "tool_name": milestone["tool_name"],
                        "arguments_hash": stable_hash(milestone["tool_args"]),
                    },
                    "error": {
                        "code": "APPROVAL_REQUIRED",
                        "message": (
                            f"Tool '{milestone['tool_name']}' requires explicit approval"
                        ),
                    },
                },
                state["state_version"],
            )
            self.storage.save_checkpoint(quest_id)
            return False

        resource_key: str | None = None
        if lease_lost.is_set():
            return False
        if milestone["tool_name"] == "write_file":
            resource_key = (
                f"{state['workspace']}:{milestone['tool_args'].get('path', '')}"
            )
            if not self.storage.acquire_resource_lease(
                resource_key,
                quest_id,
                owner,
                self.lease_seconds,
            ):
                if lease_lost.is_set():
                    return False
                self.storage.append_event(
                    quest_id,
                    "ResourceBusy",
                    {
                        "status": "waiting_user",
                        "error": {
                            "code": "RESOURCE_BUSY",
                            "message": "Workspace resource has another writer",
                        },
                    },
                    event["state_version_after"],
                )
                return False
        try:
            if lease_lost.is_set():
                return False
            receipt = self.gateway.execute(
                action_id=action_id,
                quest_id=quest_id,
                milestone_id=milestone["id"],
                idempotency_key=idempotency_key,
                expected_state_version=event["state_version_after"],
                workspace=state["workspace"],
                tool_name=milestone["tool_name"],
                arguments=milestone["tool_args"],
                approved=approved,
            )
        finally:
            if resource_key is not None:
                self.storage.release_resource_lease(resource_key, quest_id, owner)

        if lease_lost.is_set():
            return False
        state = self.get_quest(quest_id)
        if receipt["status"] == "failed":
            if lease_lost.is_set():
                return False
            failed = self._updated_milestones(
                state,
                milestone["id"],
                status="failed",
                error=receipt["error"],
            )
            self.storage.append_event(
                quest_id,
                "ToolFailed",
                {
                    "status": "failed",
                    "milestones": failed,
                    "finished_at": utc_now(),
                    "error": receipt["error"],
                    "last_receipt": receipt,
                },
                state["state_version"],
            )
            return False
        if receipt["status"] == "unknown_effect":
            if lease_lost.is_set():
                return False
            self.storage.append_event(
                quest_id,
                "ToolEffectUnknown",
                {
                    "status": "paused",
                    "recovery_required": True,
                    "error": receipt["error"],
                    "last_receipt": receipt,
                },
                state["state_version"],
            )
            self.storage.save_checkpoint(quest_id)
            return False

        receipt_event = next(
            event
            for event in reversed(self.storage.list_events(quest_id))
            if event["event_type"] == "ToolCommitted"
            and event["payload"]
            .get("patch", {})
            .get("last_receipt", {})
            .get("action_id")
            == action_id
        )
        if lease_lost.is_set():
            return False
        state = self.get_quest(quest_id)
        if milestone.get("status") != "verifying":
            if lease_lost.is_set():
                return False
            verifying = self._updated_milestones(
                state, milestone["id"], status="verifying"
            )
            self.storage.append_event(
                quest_id,
                "MilestoneVerificationStarted",
                {"status": "verifying", "milestones": verifying},
                state["state_version"],
            )
        state = self.get_quest(quest_id)
        milestone = self._milestone(state, milestone["id"])
        if lease_lost.is_set():
            return False
        passed, evidence_ids, reasons = self._verify_milestone(
            state,
            milestone,
            action_id,
            receipt_event["sequence"],
            lease_lost,
        )
        if lease_lost.is_set():
            return False
        if not passed:
            if lease_lost.is_set():
                return False
            if self._record_progress_and_check_loop(
                state, milestone, state["state_version"]
            ):
                return False
            return self._handle_replan(state, milestone, reasons, lease_lost)

        if lease_lost.is_set():
            return False
        completed = self._updated_milestones(
            state,
            milestone["id"],
            status="completed",
            evidence_ids=evidence_ids,
        )
        done_count = sum(item.get("status") == "completed" for item in completed)
        progress = done_count / max(len(completed), 1)
        if lease_lost.is_set():
            return False
        complete_event = self.storage.append_event(
            quest_id,
            "MilestoneVerified",
            {
                "status": "running",
                "milestones": completed,
                "progress": progress,
                "current_milestone_id": None,
                "last_verified_milestone_id": milestone["id"],
            },
            state["state_version"],
        )
        self.storage.save_checkpoint(quest_id)
        return not self._record_progress_and_check_loop(
            self.get_quest(quest_id), milestone, complete_event["sequence"]
        )

    def _verify_milestone(
        self,
        state: dict[str, Any],
        milestone: dict[str, Any],
        action_id: str,
        source_event_sequence: int,
        lease_lost: Event,
    ) -> tuple[bool, list[str], list[str]]:
        if lease_lost.is_set():
            return False, [], []
        criteria_by_id = {
            item["id"]: AcceptanceCriterion.model_validate(item)
            for item in state["contract"]["acceptance_criteria"]
        }
        criterion_ids = list(milestone.get("acceptance_criteria", []))
        results = []
        if criterion_ids:
            for criterion_id in criterion_ids:
                if lease_lost.is_set():
                    return False, [], []
                criterion = criteria_by_id[criterion_id]
                results.append(
                    self.verifier.verify(
                        criterion,
                        state["workspace"],
                        quest_id=state["id"],
                        milestone_id=milestone["id"],
                        criterion_version=int(state["contract"]["version"]),
                        action_attempt=action_id,
                        event_sequence=source_event_sequence,
                    )
                )
        else:
            if lease_lost.is_set():
                return False, [], []
            results.append(
                self.verifier.verify_read_only_tool(
                    criterion_id=f"observation:{milestone['id']}",
                    tool_name=milestone["tool_name"],
                    workspace=state["workspace"],
                    arguments=milestone["tool_args"],
                    quest_id=state["id"],
                    milestone_id=milestone["id"],
                    action_attempt=action_id,
                    event_sequence=source_event_sequence,
                )
            )
        for result in results:
            if lease_lost.is_set():
                return False, [], []
            self.storage.append_evidence(
                state["id"],
                result.evidence.id,
                result.evidence.model_dump(mode="json"),
            )
            self.storage.append_verification_result(
                state["id"], result.id, result.model_dump(mode="json")
            )
        return (
            all(
                result.passed
                for result in results
                if result.criterion_id
                in {
                    criterion_id
                    for criterion_id in criterion_ids
                    if criteria_by_id[criterion_id].required
                }
            ),
            [result.evidence.id for result in results if result.passed],
            [
                result.reason or "verification failed"
                for result in results
                if not result.passed
            ],
        )

    def _handle_replan(
        self,
        state: dict[str, Any],
        milestone: dict[str, Any],
        reasons: list[str],
        lease_lost: Event,
    ) -> bool:
        hypothesis = "; ".join(reasons)[:2_000]
        result = replan_plan(state, milestone["id"], hypothesis)
        if lease_lost.is_set():
            return False
        if result["requires_user"]:
            if lease_lost.is_set():
                return False
            self.storage.append_event(
                state["id"],
                "ReplanNeedsUser",
                {
                    "status": "waiting_user",
                    "error": {
                        "code": "REPLAN_BUDGET_EXHAUSTED",
                        "message": hypothesis,
                    },
                },
                state["state_version"],
            )
            return False
        usage = copy.deepcopy(state["budget_usage"])
        usage["replans"] = int(usage.get("replans", 0)) + 1
        if lease_lost.is_set():
            return False
        self.storage.append_event(
            state["id"],
            "ReplanningStarted",
            {
                "status": "replanning",
                "budget_usage": usage,
                "last_failure_hypothesis": hypothesis,
            },
            state["state_version"],
        )
        state = self.get_quest(state["id"])
        if lease_lost.is_set():
            return False
        self.storage.store_plan_version(
            state["id"],
            result["plan"],
            expected_state_version=state["state_version"],
        )
        if lease_lost.is_set():
            return False
        self.storage.save_checkpoint(state["id"])
        return True

    @staticmethod
    def _time_budget_exceeded(state: dict[str, Any]) -> bool:
        started_at = state.get("started_at")
        if not started_at:
            return False
        try:
            elapsed = datetime.now().astimezone() - datetime.fromisoformat(started_at)
        except ValueError:
            return False
        return elapsed.total_seconds() > float(
            state["contract"]["budget"]["max_seconds"]
        )

    def _exhaust_time_budget(self, state: dict[str, Any]) -> None:
        self.storage.append_event(
            state["id"],
            "BudgetExhausted",
            {
                "status": "budget_exhausted",
                "finished_at": utc_now(),
                "error": {
                    "code": "BUDGET_EXHAUSTED",
                    "message": "time budget exceeded",
                },
            },
            state["state_version"],
        )

    def _finalize_quest(
        self, state: dict[str, Any], lease_lost: Event, owner: str
    ) -> None:
        if lease_lost.is_set():
            return
        if self._time_budget_exceeded(state):
            if lease_lost.is_set():
                return
            self._exhaust_time_budget(state)
            return
        if lease_lost.is_set():
            return
        event = self.storage.append_event(
            state["id"],
            "QuestVerificationStarted",
            {"status": "verifying"},
            state["state_version"],
        )
        state = self.get_quest(state["id"])
        criteria = [
            AcceptanceCriterion.model_validate(item)
            for item in state["contract"]["acceptance_criteria"]
        ]
        if lease_lost.is_set():
            return
        passed, results = self.verifier.verify_all(
            criteria,
            state["workspace"],
            quest_id=state["id"],
            criterion_version=int(state["contract"]["version"]),
            action_attempt=f"final:{state['state_version']}",
            event_sequence=event["sequence"],
        )
        if lease_lost.is_set():
            return
        for result in results:
            if lease_lost.is_set():
                return
            self.storage.append_evidence(
                state["id"],
                result.evidence.id,
                result.evidence.model_dump(mode="json"),
            )
            self.storage.append_verification_result(
                state["id"], result.id, result.model_dump(mode="json")
            )
        # Final verification is part of execution and therefore consumes the
        # same wall-clock budget.  Re-check after it finishes so a slow verifier
        # cannot turn an over-budget Quest into a successful completion.
        state = self.get_quest(state["id"])
        if lease_lost.is_set():
            return
        if self._time_budget_exceeded(state):
            if lease_lost.is_set():
                return
            self._exhaust_time_budget(state)
            return
        if passed:
            if lease_lost.is_set():
                return
            if state.get("artifact_review_required"):
                if lease_lost.is_set():
                    return
                manifest = self._freeze_artifacts(state, results)
                if lease_lost.is_set():
                    return
                if not manifest:
                    if lease_lost.is_set():
                        return
                    self.storage.append_event(
                        state["id"],
                        "ArtifactReviewUnavailable",
                        {
                            "status": "failed",
                            "artifact_disposition": "unavailable",
                            "finished_at": utc_now(),
                            "error": {
                                "code": "NO_PREVIEWABLE_ARTIFACTS",
                                "message": "Verification passed without a previewable text artifact",
                            },
                        },
                        state["state_version"],
                    )
                    return
                if lease_lost.is_set():
                    return
                self._request_artifact_review_with_provenance(
                    state, owner, manifest, lease_lost
                )
                return
            if lease_lost.is_set():
                return
            self.storage.append_event(
                state["id"],
                "QuestCompleted",
                {
                    "status": "completed",
                    "progress": 1.0,
                    "current_milestone_id": None,
                    "finished_at": utc_now(),
                    "error": None,
                },
                state["state_version"],
            )
            self.storage.save_checkpoint(state["id"])
            return
        failed_criterion = next(
            result.criterion_id for result in results if not result.passed
        )
        criterion_owner = next(
            (
                item
                for item in state["milestones"]
                if failed_criterion in item.get("acceptance_criteria", [])
            ),
            None,
        )
        if criterion_owner is None:
            if lease_lost.is_set():
                return
            self.storage.append_event(
                state["id"],
                "QuestVerificationNeedsUser",
                {
                    "status": "waiting_user",
                    "error": {
                        "code": "FINAL_VERIFICATION_FAILED",
                        "message": failed_criterion,
                    },
                },
                state["state_version"],
            )
            return
        if lease_lost.is_set():
            return
        self._handle_replan(
            state,
            criterion_owner,
            [
                result.reason or "final verification failed"
                for result in results
                if not result.passed
            ],
            lease_lost,
        )

    def _request_artifact_review_with_provenance(
        self,
        state: dict[str, Any],
        owner: str,
        manifest: list[dict[str, Any]],
        lease_lost: Event,
    ) -> None:
        """Atomically store final shadow data and enter the existing review flow."""

        if lease_lost.is_set():
            return
        baseline = self.storage.get_baseline_snapshot(state["id"])
        if baseline is None:
            raise RuntimeError("artifact review provenance baseline is missing")
        baseline_entries = self.storage.list_workspace_snapshot_entries(
            baseline["snapshot_id"]
        )
        final_snapshot_value = scan_sandbox_workspace(self.sandbox, state["workspace"])
        final_snapshot = final_snapshot_value.to_dict()
        final_snapshot["quest_id"] = state["id"]
        final_entries = list(final_snapshot["entries"])
        final_snapshot_id = f"final_{
            stable_hash(
                {
                    'quest_id': state['id'],
                    'state_version': state['state_version'],
                    'workspace': state['workspace'],
                    'policy_version': final_snapshot_value.policy_version,
                    'status': final_snapshot_value.status,
                    'root_hash': final_snapshot_value.root_hash,
                    'artifacts': [
                        {
                            key: item[key]
                            for key in (
                                'artifact_id',
                                'path',
                                'hash',
                                'size',
                                'evidence_id',
                            )
                        }
                        for item in manifest
                    ],
                }
            )[:24]
        }"
        actions = self.storage.list_tool_actions(state["id"])
        observations = self.storage.list_tool_file_observations(state["id"])
        provenance: list[dict[str, Any]] = []
        enriched_manifest: list[dict[str, Any]] = []
        for item in manifest:
            classification = classify_artifact_provenance(
                item,
                baseline,
                baseline_entries,
                final_snapshot,
                final_entries,
                actions,
                observations,
            )
            provenance_status = str(classification["provenance_status"])
            provenance_id = f"prov_{
                stable_hash(
                    {
                        'quest_id': state['id'],
                        'artifact_id': item['artifact_id'],
                        'artifact_hash': item['hash'],
                        'evidence_id': item['evidence_id'],
                        'baseline_snapshot_id': baseline['snapshot_id'],
                        'final_snapshot_id': final_snapshot_id,
                        'provenance_status': provenance_status,
                        'reason_code': classification['reason_code'],
                        'action_id': classification['terminal_action_id'],
                        'committed_event_id': classification[
                            'terminal_committed_event_id'
                        ],
                    }
                )[:24]
            }"
            enriched = {
                **item,
                "provenance_id": provenance_id,
                "provenance_status": provenance_status,
                "provenance_reason_code": classification["reason_code"],
                "provenance_mode": "compatibility_shadow",
                "baseline_snapshot_id": baseline["snapshot_id"],
                "final_snapshot_id": final_snapshot_id,
            }
            enriched_manifest.append(enriched)
            provenance.append(
                {
                    "provenance_id": provenance_id,
                    "artifact_id": item["artifact_id"],
                    "path": item["path"],
                    "artifact_hash": item["hash"],
                    "evidence_id": item["evidence_id"],
                    "status": classification["storage_status"],
                    "provenance_status": provenance_status,
                    "provenance_mode": "compatibility_shadow",
                    "baseline_snapshot_id": baseline["snapshot_id"],
                    "final_snapshot_id": final_snapshot_id,
                    "action_id": (
                        classification["terminal_action_id"]
                        if str(provenance_status).startswith("shadow_observed_")
                        else None
                    ),
                    "committed_event_id": (
                        classification["terminal_committed_event_id"]
                        if str(provenance_status).startswith("shadow_observed_")
                        else None
                    ),
                }
            )
        if lease_lost.is_set():
            return
        try:
            self.storage.request_artifact_review_with_provenance(
                state["id"],
                owner=owner,
                review_id=f"review_{uuid.uuid4().hex[:16]}",
                manifest=enriched_manifest,
                manifest_hash=stable_hash(enriched_manifest),
                final_snapshot_id=final_snapshot_id,
                final_snapshot=final_snapshot,
                provenance=provenance,
                expected_state_version=state["state_version"],
            )
        except ValueError as exc:
            message = str(exc)
            if message in {
                "state version conflict",
                "live execution lease is not held by owner",
            }:
                # The owner/CAS check is part of the atomic write.  Losing it
                # is a worker stop, not a final-verifier failure.
                return
            if message == "artifact review was already requested" and any(
                event["event_type"] == "ArtifactReviewRequested"
                for event in self.storage.list_events(state["id"])
            ):
                return
            raise

    def _freeze_artifacts(
        self, state: dict[str, Any], results: list[Any]
    ) -> list[dict[str, Any]]:
        """Freeze only distinct, passing verifier-backed regular UTF-8 text files."""
        evidence_by_path = {
            r.evidence.artifact_path: r.evidence.id
            for r in results
            if r.passed and r.evidence.artifact_path
        }
        manifest: list[dict[str, Any]] = []
        for relative, evidence_id in sorted(evidence_by_path.items()):
            assert relative is not None
            item = {"path": relative, "created_by_quest": True}
            try:
                path = self._artifact_lexical_path(state, item)
            except AppError:
                continue
            if not path.is_file() or path.is_symlink():
                continue
            try:
                content = path.read_bytes()
                content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(content) > min(self.sandbox.max_file_bytes, ARTIFACT_PREVIEW_BYTES):
                continue
            digest = hashlib.sha256(content).hexdigest()
            manifest.append(
                {
                    "artifact_id": stable_hash(
                        {
                            "path": relative,
                            "hash": digest,
                            "evidence_id": evidence_id,
                        }
                    )[:24],
                    "path": relative,
                    "hash": digest,
                    "size": len(content),
                    "evidence_id": evidence_id,
                    "created_by_quest": True,
                }
            )
        return manifest

    def _record_progress_and_check_loop(
        self,
        state: dict[str, Any],
        milestone: dict[str, Any],
        event_sequence: int,
    ) -> bool:
        evidence_ids = sorted(
            {
                evidence_id
                for item in state["milestones"]
                for evidence_id in item.get("evidence_ids", [])
            }
        )
        world_state_hash = stable_hash(
            {
                "evidence_ids": evidence_ids,
                "progress": state["progress"],
                "contract_version": state["contract"]["version"],
            }
        )
        entry = {
            "event_sequence": event_sequence,
            "action_signature": stable_hash(
                {
                    "tool_name": milestone["tool_name"],
                    "arguments": milestone["tool_args"],
                }
            ),
            "world_state_hash": world_state_hash,
            "evidence_ids": evidence_ids,
            "progress": state["progress"],
        }
        entry_id = f"progress_{state['id']}_{event_sequence}"
        self.storage.append_progress(state["id"], entry_id, entry)
        watchdog = ProgressWatchdog(self.watchdog_threshold)
        decision = None
        history = sorted(
            self.storage.list_progress(state["id"]),
            key=lambda item: int(item["event_sequence"]),
        )
        for previous in history:
            decision = watchdog.observe(
                previous["action_signature"],
                previous["world_state_hash"],
                previous["evidence_ids"],
            )
        if decision and decision.triggered:
            current = self.get_quest(state["id"])
            self.storage.append_event(
                state["id"],
                "LoopDetected",
                {
                    "status": "waiting_user",
                    "error": {
                        "code": "LOOP_DETECTED",
                        "message": "Repeated actions produced no new WorldState or Evidence",
                    },
                },
                current["state_version"],
            )
            return True
        return False

    @staticmethod
    def _milestone(state: dict[str, Any], milestone_id: str) -> dict[str, Any]:
        return next(item for item in state["milestones"] if item["id"] == milestone_id)

    @staticmethod
    def _updated_milestones(
        state: dict[str, Any], milestone_id: str, **changes: Any
    ) -> list[dict[str, Any]]:
        result = copy.deepcopy(state["milestones"])
        for milestone in result:
            if milestone["id"] == milestone_id:
                milestone.update(copy.deepcopy(changes))
                return result
        raise KeyError(milestone_id)

    def _action_is_approved(self, quest_id: str, action_id: str) -> bool:
        for decision in reversed(self.storage.list_decisions(quest_id)):
            if decision.get("kind") != "approve":
                continue
            patch = decision.get("contract_patch", {})
            if patch.get("action_id") == action_id:
                return True
        return False

    def _fail_quest(
        self,
        state: dict[str, Any],
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.storage.append_event(
                state["id"],
                "QuestFailed",
                {
                    "status": "failed",
                    "finished_at": utc_now(),
                    "error": {
                        "code": code,
                        "message": message,
                        "details": details or {},
                    },
                },
                state["state_version"],
            )
        except ValueError:
            # A concurrent pause/version change wins; the next worker pass will
            # observe it instead of overwriting the newer state.
            return

    def _recover_interrupted(self) -> dict[str, int]:
        cleared_quest_leases, cleared_resource_leases = (
            self.storage.clear_runtime_leases_on_startup()
        )
        ambiguous_actions = self.storage.mark_dispatched_actions_unknown()
        interrupted = 0
        for state in self.storage.list_quests():
            if state.get("status") == "discarding":
                try:
                    self._discard_artifacts(state)
                except AppError:
                    # Keep the durable discard intent; user must resolve a tamper conflict.
                    pass
                continue
            if state.get("status") not in ACTIVE_STATUSES:
                continue
            self.storage.append_event(
                state["id"],
                "ProcessInterrupted",
                {
                    "status": "paused",
                    "pause_requested": False,
                    "recovery_required": True,
                    "milestones": state.get("milestones", []),
                    "error": {
                        "code": "PROCESS_INTERRUPTED",
                        "message": "Resume will replay and reconcile the last action",
                    },
                },
                state["state_version"],
            )
            self.storage.save_checkpoint(state["id"])
            interrupted += 1
        return {
            "interrupted_quests": interrupted,
            "ambiguous_actions": ambiguous_actions,
            "cleared_quest_leases": cleared_quest_leases,
            "cleared_resource_leases": cleared_resource_leases,
            **self.storage.action_recovery_summary(),
        }
