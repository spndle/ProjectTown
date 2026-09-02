from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any

from .agent import RuleBasedAgent
from .database import Database
from .errors import AppError, ToolError
from .tools import Sandbox, ToolRegistry


class QuestService:
    def __init__(
        self,
        *,
        database: Database,
        agent: RuleBasedAgent,
        sandbox: Sandbox,
        tools: ToolRegistry,
        max_workers: int,
    ) -> None:
        self.database = database
        self.agent = agent
        self.sandbox = sandbox
        self.tools = tools
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="projecttown-quest"
        )
        self._futures: dict[str, Future[None]] = {}
        self._futures_lock = Lock()
        self._closed = False

    def create_quest(
        self,
        *,
        goal: str | None,
        template_id: str | None,
        workspace: str | None,
    ) -> dict[str, Any]:
        if not goal and not template_id:
            raise AppError(
                "GOAL_OR_TEMPLATE_REQUIRED",
                "Provide either 'goal' or 'template_id'",
                status_code=422,
            )
        started = time.perf_counter()
        resolved_goal, resolved_template, plan = self.agent.plan(
            goal=goal, template_id=template_id
        )
        quest_id = f"q_{uuid.uuid4().hex[:12]}"
        resolved_workspace = workspace or f"quests/{quest_id}"
        try:
            self.sandbox.workspace_path(resolved_workspace, create=True)
        except ToolError as exc:
            raise AppError(
                exc.code, exc.message, status_code=422, details=exc.details
            ) from exc
        milestones = [
            {
                "id": f"m_{uuid.uuid4().hex[:12]}",
                "title": step.title,
                "description": step.description,
                "tool_name": step.tool_name,
                "tool_args": step.tool_args,
            }
            for step in plan
        ]
        unknown_tools = sorted(
            {item["tool_name"] for item in milestones} - set(self.tools.names)
        )
        if unknown_tools:
            raise AppError(
                "INVALID_PLAN",
                "Plan references tools that are not registered",
                status_code=500,
                details={"unknown_tools": unknown_tools},
            )
        quest = self.database.create_quest(
            quest_id=quest_id,
            goal=resolved_goal,
            template_id=resolved_template,
            workspace=resolved_workspace,
            milestones=milestones,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.database.add_trace(
            quest_id,
            trace_type="quest_created",
            message="Quest created and linear plan generated",
            data={
                "agent": self.agent.name,
                "model": self.agent.model,
                "template_id": resolved_template,
                "step_count": len(plan),
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
            duration_ms=duration_ms,
        )
        return quest

    def list_quests(self) -> list[dict[str, Any]]:
        return self.database.list_quests()

    def get_quest(self, quest_id: str) -> dict[str, Any]:
        return self.database.require_quest(quest_id)

    def get_traces(self, quest_id: str) -> list[dict[str, Any]]:
        return self.database.list_traces(quest_id)

    def start_quest(self, quest_id: str) -> dict[str, Any]:
        if self._closed:
            raise AppError(
                "SERVICE_UNAVAILABLE", "Quest service is shutting down", status_code=503
            )
        quest = self.database.claim_for_run(quest_id)
        try:
            future = self._executor.submit(self._execute_quest, quest_id)
        except Exception as exc:
            error = {
                "code": "SCHEDULING_FAILED",
                "message": "The Quest could not be scheduled",
                "details": {},
                "retryable": False,
            }
            self.database.fail_quest(quest_id, error)
            raise AppError(
                "SCHEDULING_FAILED",
                "The Quest could not be scheduled",
                status_code=503,
            ) from exc
        with self._futures_lock:
            self._futures[quest_id] = future
        future.add_done_callback(lambda _: self._forget_future(quest_id))
        return quest

    def close(self, wait: bool = True) -> None:
        self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _forget_future(self, quest_id: str) -> None:
        with self._futures_lock:
            self._futures.pop(quest_id, None)

    def _execute_quest(self, quest_id: str) -> None:
        quest = self.database.require_quest(quest_id)
        self.database.add_trace(
            quest_id,
            trace_type="quest_started",
            message="Background Quest execution started",
            data={"milestone_count": len(quest["milestones"])},
        )
        try:
            for milestone in quest["milestones"]:
                self._execute_milestone(quest, milestone)
            self.database.complete_quest(quest_id)
            self.database.add_trace(
                quest_id,
                trace_type="quest_completed",
                message="All milestones completed",
                data={"milestone_count": len(quest["milestones"])},
            )
        except ToolError as exc:
            # The milestone-specific method persists its failure first.
            self.database.fail_quest(quest_id, exc.as_dict())
            self.database.add_trace(
                quest_id,
                trace_type="quest_failed",
                level="error",
                message=exc.message,
                data={"error": exc.as_dict()},
            )
        # This is the background-worker boundary: every otherwise-unhandled
        # failure must become a persisted terminal state and diagnostic trace.
        except Exception as exc:  # noqa: BLE001
            error = {
                "code": "EXECUTION_ERROR",
                "message": "An unexpected error stopped Quest execution",
                "details": {"exception_type": type(exc).__name__},
                "retryable": False,
            }
            self.database.fail_quest(quest_id, error)
            self.database.add_trace(
                quest_id,
                trace_type="quest_failed",
                level="error",
                message=error["message"],
                data={"error": error},
            )

    def _execute_milestone(
        self, quest: dict[str, Any], milestone: dict[str, Any]
    ) -> None:
        quest_id = quest["id"]
        milestone_id = milestone["id"]
        self.database.start_milestone(
            quest_id, milestone_id, int(milestone["position"])
        )
        self.database.add_trace(
            quest_id,
            trace_type="milestone_started",
            message=f"Milestone {milestone['position']} started: {milestone['title']}",
            data={
                "milestone_id": milestone_id,
                "position": milestone["position"],
                "tool_name": milestone["tool_name"],
            },
        )
        self.database.add_trace(
            quest_id,
            trace_type="tool_started",
            message=f"Executing sandbox tool '{milestone['tool_name']}'",
            data={
                "milestone_id": milestone_id,
                "tool_name": milestone["tool_name"],
                "arguments": _safe_arguments(milestone["tool_args"]),
            },
        )
        started = time.perf_counter()
        try:
            result = self.tools.execute(
                milestone["tool_name"], quest["workspace"], milestone["tool_args"]
            )
        except ToolError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.database.fail_milestone(quest_id, milestone_id, exc.as_dict())
            self.database.add_trace(
                quest_id,
                trace_type="tool_failed",
                level="error",
                message=exc.message,
                data={"milestone_id": milestone_id, "error": exc.as_dict()},
                duration_ms=duration_ms,
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            tool_error = ToolError(
                "TOOL_EXECUTION_ERROR",
                "The sandbox tool stopped with an unexpected error",
                details={
                    "tool_name": milestone["tool_name"],
                    "exception_type": type(exc).__name__,
                },
                retryable=False,
            )
            self.database.fail_milestone(quest_id, milestone_id, tool_error.as_dict())
            self.database.add_trace(
                quest_id,
                trace_type="tool_failed",
                level="error",
                message=tool_error.message,
                data={"milestone_id": milestone_id, "error": tool_error.as_dict()},
                duration_ms=duration_ms,
            )
            raise tool_error from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.database.complete_milestone(quest_id, milestone_id, result)
        self.database.add_trace(
            quest_id,
            trace_type="tool_completed",
            message=f"Sandbox tool '{milestone['tool_name']}' completed",
            data={"milestone_id": milestone_id, "result": _safe_result(result)},
            duration_ms=duration_ms,
        )
        self.database.add_trace(
            quest_id,
            trace_type="milestone_completed",
            message=f"Milestone {milestone['position']} completed",
            data={"milestone_id": milestone_id, "position": milestone["position"]},
        )


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Trace action shape without duplicating generated file contents."""
    result = dict(arguments)
    if "content" in result:
        content = result.pop("content")
        result["content_size"] = (
            len(content.encode("utf-8")) if isinstance(content, str) else 0
        )
    return result


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    safe = dict(result)
    if "content" in safe:
        content = safe.pop("content")
        safe["content_size"] = (
            len(content.encode("utf-8")) if isinstance(content, str) else 0
        )
    return safe
