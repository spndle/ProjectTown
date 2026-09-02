"""Pure deterministic v1 planning and coordination helpers."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from ..agent import PlanStep, RuleBasedAgent
from ..runtime import CapabilityRouter, stable_hash
from .models import AcceptanceCriterion, GoalContract, QuestCreate


def _id(prefix: str, value: Any) -> str:
    return f"{prefix}_{stable_hash(value)[:12]}"


def _criterion_for_path(path: str) -> AcceptanceCriterion:
    lower = path.lower()
    if lower.endswith(".py"):
        kind = "python_syntax"
    elif lower.endswith((".md", ".markdown")):
        kind = "markdown"
    elif lower.endswith(".json"):
        kind = "json_schema"
    else:
        kind = "file_exists_nonempty"
    return AcceptanceCriterion(
        id=_id("criterion", {"path": path, "kind": kind}),
        kind=kind,
        description=f"{kind} check for {path}",
        path=path,
    )


def _normalize_criteria(
    payload: QuestCreate, steps: Sequence[PlanStep]
) -> list[AcceptanceCriterion]:
    generated: dict[str, AcceptanceCriterion] = {}
    for step in steps:
        if step.tool_name == "write_file":
            path = str(step.tool_args.get("path", ""))
            if path:
                generated[path] = _criterion_for_path(path)

    result = list(generated.values())
    by_path = {item.path: index for index, item in enumerate(result) if item.path}
    by_id = {item.id: index for index, item in enumerate(result)}
    for criterion in payload.acceptance_criteria:
        index = by_id.get(criterion.id)
        if index is None and criterion.path:
            index = by_path.get(criterion.path)
        if index is None:
            result.append(criterion)
        else:
            result[index] = criterion
    return result


def _compile_nodes(steps: Sequence[PlanStep]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    roots: list[str] = []
    writes: dict[str, str] = {}
    previous: str | None = None
    for position, step in enumerate(steps, 1):
        node_id = _id(
            "milestone",
            {
                "position": position,
                "title": step.title,
                "tool": step.tool_name,
                "args": step.tool_args,
            },
        )
        path = (
            str(step.tool_args.get("path", ""))
            if isinstance(step.tool_args, Mapping)
            else ""
        )
        if position == 1:
            dependencies: list[str] = []
            roots.append(node_id)
        elif step.tool_name == "write_file":
            dependencies = list(roots)
        elif step.tool_name.startswith("check_") and path in writes:
            dependencies = [writes[path]]
        elif previous:
            dependencies = [previous]
        else:
            dependencies = []
        nodes.append(
            {
                "id": node_id,
                "plan_version": 1,
                "position": position,
                "title": step.title,
                "description": step.description,
                "tool_name": step.tool_name,
                "tool_args": copy.deepcopy(step.tool_args),
                "dependencies": dependencies,
                "acceptance_criteria": [],
                "status": "pending",
                "evidence_ids": [],
                "attempt": 0,
            }
        )
        if step.tool_name == "write_file" and path:
            writes[path] = node_id
        previous = node_id
    return nodes


def compile_draft(
    agent: RuleBasedAgent,
    payload: QuestCreate,
    quest_id: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[str]]:
    """Compile a Goal Contract and executable DAG without side effects."""

    resolved_goal, template_id, steps = agent.plan(
        goal=payload.goal, template_id=payload.template_id
    )
    criteria = _normalize_criteria(payload, steps)
    contract = GoalContract(
        id=_id("contract", {"quest_id": quest_id, "goal": resolved_goal}),
        quest_id=quest_id,
        version=1,
        goal=resolved_goal,
        constraints=list(
            dict.fromkeys(
                [
                    "All tool access must stay inside the Quest workspace.",
                    "Only configured allowlisted tools may execute.",
                    *payload.constraints,
                ]
            )
        ),
        non_goals=list(
            dict.fromkeys(
                ["No network or external-account side effects.", *payload.non_goals]
            )
        ),
        budget=payload.budget,
        acceptance_criteria=criteria,
    ).model_dump(mode="json")
    nodes = _compile_nodes(steps)
    criterion_by_path = {
        criterion["path"]: criterion["id"]
        for criterion in contract["acceptance_criteria"]
        if criterion.get("path")
    }
    for node in nodes:
        path = node["tool_args"].get("path")
        if path in criterion_by_path:
            node["acceptance_criteria"] = [criterion_by_path[path]]

    plan = {
        "id": _id("plan", {"quest_id": quest_id}),
        "version": 1,
        "milestones": nodes,
        "metadata": {
            "template_id": template_id,
            "roots": [node["id"] for node in nodes if not node["dependencies"]],
        },
    }
    if template_id == "python_starter":
        plan["metadata"]["branches"] = [
            {"when": "python_syntax_pass", "next": "README"},
            {"when": "python_syntax_fail", "next": "repair"},
        ]
    route = CapabilityRouter().route(len(nodes), "normal", payload.force_multi_agent)
    return resolved_goal, template_id, contract, plan, route


def build_handoff(
    task_id: str,
    state_version: int,
    evidence_ids: Sequence[str],
    expected_reply: str | None,
    *,
    sender: str = "planner",
    recipient: str = "executor",
    payload: Any = None,
) -> dict[str, Any]:
    """Construct an artifact-first handoff without hidden reasoning."""

    return {
        "task_id": task_id,
        "state_version": state_version,
        "sender": sender,
        "recipient": recipient,
        "evidence_ids": sorted({str(item) for item in evidence_ids}),
        "expected_reply": expected_reply,
        "payload": copy.deepcopy(payload) if payload is not None else {},
    }


def replan_plan(
    current_state: Mapping[str, Any],
    failed_node: str,
    failure_hypothesis: str,
    *,
    budget: int | None = None,
) -> dict[str, Any]:
    """Create a new plan version while preserving every completed node."""

    if "plan" in current_state:
        plan = copy.deepcopy(current_state["plan"])
    else:
        plan = {
            "id": current_state.get(
                "plan_id", f"plan_{current_state.get('id', 'quest')}"
            ),
            "version": current_state.get("plan_version", 1),
            "milestones": copy.deepcopy(current_state.get("milestones", [])),
            "metadata": copy.deepcopy(current_state.get("plan_metadata", {})),
        }
    version = int(plan.get("version", current_state.get("plan_version", 1)))
    milestones = list(plan.get("milestones", []))
    completed = {
        milestone["id"]
        for milestone in milestones
        if milestone.get("status") == "completed"
    }
    all_ids = {milestone.get("id") for milestone in milestones}
    if failed_node not in all_ids:
        raise ValueError("failed milestone is not in the current plan")
    if failed_node in completed:
        raise ValueError("completed milestones cannot be replanned")

    used = int(
        current_state.get(
            "replans", current_state.get("budget_usage", {}).get("replans", 0)
        )
    )
    contract_budget = current_state.get("contract", {}).get("budget", {})
    limit = (
        budget
        if budget is not None
        else current_state.get("budget", contract_budget).get("max_replans")
    )
    if limit is not None and used >= int(limit):
        return {
            "requires_user": True,
            "status": "requires_user",
            "plan": None,
            "reason": "replan_budget_exhausted",
        }

    unfinished_ids = [
        milestone["id"] for milestone in milestones if milestone["id"] not in completed
    ]
    remap = {
        old_id: _id(
            "milestone",
            {
                "version": version + 1,
                "old": old_id,
                "hypothesis": failure_hypothesis,
            },
        )
        for old_id in unfinished_ids
    }
    next_milestones: list[dict[str, Any]] = []
    for original in milestones:
        milestone = copy.deepcopy(original)
        old_id = milestone["id"]
        if old_id in completed:
            next_milestones.append(milestone)
            continue
        milestone["id"] = remap[old_id]
        milestone["plan_version"] = version + 1
        milestone["status"] = "pending"
        milestone["evidence_ids"] = []
        milestone["attempt"] = int(milestone.get("attempt", 0)) + 1
        milestone["dependencies"] = [
            remap.get(dependency, dependency)
            for dependency in milestone.get("dependencies", [])
        ]
        next_milestones.append(milestone)

    metadata = copy.deepcopy(plan.get("metadata", {}))
    metadata.setdefault("superseded", []).append(
        {
            "plan_version": version,
            "node_ids": unfinished_ids,
            "failure_hypothesis": failure_hypothesis,
        }
    )
    return {
        "requires_user": False,
        "status": "replanned",
        "plan": {
            "id": plan.get("id", f"plan_{current_state.get('id', 'quest')}"),
            "version": version + 1,
            "milestones": next_milestones,
            "metadata": metadata,
        },
    }


construct_replan = replan_plan
replan = replan_plan
