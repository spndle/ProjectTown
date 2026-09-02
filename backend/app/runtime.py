"""Deterministic, database-independent runtime primitives for v1."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


def stable_hash(value: Any) -> str:
    """Return a stable SHA-256 hash of a JSON-compatible value."""
    if is_dataclass(value):
        value = asdict(value)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _merge(base: Any, patch: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(patch, Mapping):
        result = copy.deepcopy(dict(base))
        for key, value in patch.items():
            result[key] = (
                _merge(result[key], value) if key in result else copy.deepcopy(value)
            )
        return result
    return copy.deepcopy(patch)


@dataclass(frozen=True)
class Event:
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)


class EventReducer:
    """Apply explicit nested patches without mutating state or events."""

    def apply(
        self,
        state: Mapping[str, Any] | None,
        event: Event | Mapping[str, Any],
    ) -> dict[str, Any]:
        current = {} if state is None else copy.deepcopy(dict(state))
        payload = (
            event.payload if isinstance(event, Event) else event.get("payload", event)
        )
        if not isinstance(payload, Mapping):
            raise TypeError("event payload must be a mapping")
        patch = payload.get("patch", payload)
        if not isinstance(patch, Mapping):
            raise TypeError("event patch must be a mapping")
        return _merge(current, patch)

    def replay(
        self,
        events: Sequence[Event | Mapping[str, Any]],
        initial: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = {} if initial is None else copy.deepcopy(dict(initial))
        for event in events:
            state = self.apply(state, event)
        return state


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class ExecutionBudget:
    max_steps: int | None = None
    max_tool_calls: int | None = None
    max_messages: int | None = None
    max_tokens: int | None = None
    max_replans: int | None = None
    max_seconds: float | None = None


class BudgetGuard:
    def __init__(self, budget: ExecutionBudget, *, clock=time.monotonic):
        self.budget = budget
        self._clock = clock
        self.started_at = clock()
        self.steps = 0
        self.tool_calls = 0
        self.messages = 0
        self.tokens = 0
        self.replans = 0

    def check(self, kind: str, amount: int = 1) -> bool:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        counters = {
            "step": self.steps,
            "tool_call": self.tool_calls,
            "message": self.messages,
            "token": self.tokens,
            "replan": self.replans,
        }
        limits = {
            "step": self.budget.max_steps,
            "tool_call": self.budget.max_tool_calls,
            "message": self.budget.max_messages,
            "token": self.budget.max_tokens,
            "replan": self.budget.max_replans,
        }
        current = counters.get(kind)
        limit = limits.get(kind)
        if current is None:
            raise ValueError(f"unknown budget kind: {kind}")
        elif limit is not None and current + amount > limit:
            return False
        return (
            self.budget.max_seconds is None
            or self._clock() - self.started_at <= self.budget.max_seconds
        )

    def consume(self, kind: str, amount: int = 1) -> None:
        if not self.check(kind, amount):
            raise BudgetExceeded(f"{kind} budget exceeded")
        if kind == "step":
            self.steps += amount
        elif kind == "tool_call":
            self.tool_calls += amount
        elif kind == "message":
            self.messages += amount
        elif kind == "token":
            self.tokens += amount
        elif kind == "replan":
            self.replans += amount

    def consume_step(self, amount: int = 1) -> None:
        self.consume("step", amount)

    def consume_tool_call(self, amount: int = 1) -> None:
        self.consume("tool_call", amount)

    def consume_message(self, amount: int = 1) -> None:
        self.consume("message", amount)

    def consume_tokens(self, amount: int) -> None:
        self.consume("token", amount)

    def consume_replan(self, amount: int = 1) -> None:
        self.consume("replan", amount)

    def remaining(self, kind: str) -> int | float | None:
        limits = {
            "step": self.budget.max_steps,
            "tool_call": self.budget.max_tool_calls,
            "message": self.budget.max_messages,
            "token": self.budget.max_tokens,
            "replan": self.budget.max_replans,
        }
        if kind == "time":
            return (
                None
                if self.budget.max_seconds is None
                else max(
                    0.0, self.budget.max_seconds - (self._clock() - self.started_at)
                )
            )
        if kind not in limits:
            raise ValueError(f"unknown budget kind: {kind}")
        limit = limits[kind]
        counters = {
            "step": self.steps,
            "tool_call": self.tool_calls,
            "message": self.messages,
            "token": self.tokens,
            "replan": self.replans,
        }
        return None if limit is None else max(0, limit - counters[kind])


@dataclass(frozen=True)
class WatchdogDecision:
    triggered: bool
    reason: str
    consecutive: int
    has_new_evidence: bool


class ProgressWatchdog:
    def __init__(self, threshold: int = 3):
        if threshold < 1:
            raise ValueError("threshold must be positive")
        self.threshold = threshold
        self._last: tuple[str, str, frozenset[str]] | None = None
        self._seen_evidence: set[str] = set()
        self.consecutive = 0

    def observe(
        self,
        action_signature: Any,
        world_state_hash: str,
        evidence_ids: Sequence[str] | set[str],
    ) -> WatchdogDecision:
        evidence = frozenset(str(x) for x in evidence_ids)
        action_hash = stable_hash(action_signature)
        same = (
            self._last is not None
            and self._last[0] == action_hash
            and self._last[1] == world_state_hash
        )
        new_evidence = bool(evidence - self._seen_evidence)
        self._seen_evidence.update(evidence)
        self.consecutive = self.consecutive + 1 if same and not new_evidence else 1
        self._last = (action_hash, world_state_hash, evidence)
        triggered = self.consecutive >= self.threshold and not new_evidence
        return WatchdogDecision(
            triggered,
            "no_progress" if triggered else "progress",
            self.consecutive,
            new_evidence,
        )


class CapabilityRouter:
    def route(
        self, step_count: int, risk_level: str, force_multi: bool | None = None
    ) -> list[str]:
        if step_count < 1:
            raise ValueError("step_count must be positive")
        multi = (
            force_multi
            if force_multi is not None
            else step_count >= 4 or str(risk_level).lower() in {"high", "critical"}
        )
        return ["planner", "executor", "verifier"] if multi else ["agent"]


@dataclass(frozen=True)
class SparseMessage:
    task_id: str
    state_version: int
    sender: str
    recipient: str
    evidence_ids: tuple[str, ...]
    expected_reply: str | None
    payload: Any


@dataclass(frozen=True)
class PublishResult:
    accepted: bool
    reason: str
    message: SparseMessage


class SparseMessageBus:
    def __init__(self):
        self.messages: list[SparseMessage] = []
        self._fingerprints: set[str] = set()

    def publish(
        self,
        task_id: str,
        state_version: int,
        sender: str,
        recipient: str,
        evidence_ids: Sequence[str],
        expected_reply: str | None,
        payload: Any,
    ) -> PublishResult:
        message = SparseMessage(
            task_id,
            state_version,
            sender,
            recipient,
            tuple(sorted({str(item) for item in evidence_ids})),
            expected_reply,
            copy.deepcopy(payload),
        )
        # State version is intentionally omitted: a newer version without new
        # evidence or changed content is still a redundant message.
        fingerprint = stable_hash(
            {
                "task_id": task_id,
                "sender": sender,
                "recipient": recipient,
                "payload": payload,
                "evidence_ids": message.evidence_ids,
                "expected_reply": expected_reply,
            }
        )
        if fingerprint in self._fingerprints:
            return PublishResult(False, "duplicate_no_new_evidence", message)
        self._fingerprints.add(fingerprint)
        self.messages.append(message)
        return PublishResult(True, "accepted", message)
