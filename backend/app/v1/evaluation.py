"""Reproducible local v1 benchmark simulator and report CLI.

The runner exercises the feature/fault matrix without calling an LLM or
executing user code. Every result is explicitly marked ``runtime_simulation``.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

SIMULATOR_VERSION = "1.0"
DEFAULT_SEED = 1729
CONFIGS: dict[str, dict[str, Any]] = {
    "B0": {
        "name": "react_no_ledger",
        "ledger": False,
        "verifier": False,
        "checkpoint": False,
        "idempotency": False,
        "watchdog": False,
        "restricted_replan": False,
        "multi_agent": False,
    },
    "B1": {
        "name": "fixed_planner_executor",
        "ledger": True,
        "verifier": False,
        "checkpoint": False,
        "idempotency": False,
        "watchdog": False,
        "restricted_replan": False,
        "multi_agent": False,
    },
    "B2": {
        "name": "ledger_verifier",
        "ledger": True,
        "verifier": True,
        "checkpoint": False,
        "idempotency": False,
        "watchdog": False,
        "restricted_replan": False,
        "multi_agent": False,
    },
    "B3": {
        "name": "full_runtime",
        "ledger": True,
        "verifier": True,
        "checkpoint": True,
        "idempotency": True,
        "watchdog": True,
        "restricted_replan": True,
        "multi_agent": False,
    },
    "B4": {
        "name": "controlled_multi_agent",
        "ledger": True,
        "verifier": True,
        "checkpoint": True,
        "idempotency": True,
        "watchdog": True,
        "restricted_replan": True,
        "multi_agent": True,
    },
}
ABLATIONS = (
    "no_verifier",
    "no_checkpoint",
    "no_idempotency",
    "no_watchdog",
    "full_replan",
    "free_chat",
    "single_vs_three_role",
)
KEY_INDICES = {1, 5, 8}


def _catalog_path() -> Path:
    return Path(__file__).resolve().parents[3] / "benchmark" / "quests" / "catalog.json"


def load_catalog_document(path: Path | None = None) -> dict[str, Any]:
    selected = path or _catalog_path()
    return json.loads(selected.read_text(encoding="utf-8"))


def load_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    document = load_catalog_document(path)
    templates = document.get("task_templates", {})
    quests: list[dict[str, Any]] = []
    for raw in document["quests"]:
        quest = copy.deepcopy(raw)
        template = copy.deepcopy(templates.get(quest["family"], {}))
        quest.setdefault("title", template.get("title", quest["id"]))
        goal_template = template.get(
            "goal", "Complete deterministic local task {id} ({length})."
        )
        quest.setdefault(
            "goal",
            goal_template.format(id=quest["id"], length=quest["length"]),
        )
        quest.setdefault("expected_artifacts", template.get("expected_artifacts", []))
        quest.setdefault("action_plan", _action_plan(int(quest["required_actions"])))
        quests.append(quest)
    return quests


def _action_plan(count: int) -> list[dict[str, Any]]:
    tools = ("read", "inspect", "write")
    actions = []
    for index in range(1, count + 1):
        actions.append(
            {
                "id": f"action-{index:02d}",
                "tool": tools[(index - 1) % len(tools)],
                "depends_on": [] if index == 1 else [f"action-{index - 1:02d}"],
            }
        )
    return actions


def agent_view(quest: dict[str, Any]) -> dict[str, Any]:
    """Return strategy-visible fields; Gold remains external to the policy."""

    hidden = {"gold_constraints", "gold", "external_verifier"}
    return {
        key: copy.deepcopy(value) for key, value in quest.items() if key not in hidden
    }


def _features(config: str, ablation: str) -> dict[str, Any]:
    if config not in CONFIGS:
        raise ValueError(f"unknown config: {config}")
    if ablation != "none" and ablation not in ABLATIONS:
        raise ValueError(f"unknown ablation: {ablation}")
    result = copy.deepcopy(CONFIGS[config])
    overrides: dict[str, dict[str, Any]] = {
        "no_verifier": {"verifier": False},
        "no_checkpoint": {"checkpoint": False},
        "no_idempotency": {"idempotency": False},
        "no_watchdog": {"watchdog": False},
        "full_replan": {"restricted_replan": False, "full_replan": True},
        "free_chat": {"sparse_messages": False, "free_chat": True},
        "single_vs_three_role": {"multi_agent": not result["multi_agent"]},
    }
    result.update(overrides.get(ablation, {}))
    result.setdefault("full_replan", False)
    result.setdefault("free_chat", False)
    result.setdefault("sparse_messages", True)
    return result


def configuration_fingerprint(config: str, ablation: str = "none") -> str:
    payload = {
        "simulator_version": SIMULATOR_VERSION,
        "config": config,
        "ablation": ablation,
        "features": _features(config, ablation),
        "temperature": 0,
        "tools": ["read", "write", "inspect"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def score_metrics(
    *,
    success: bool,
    progress: float,
    false_completion: bool = False,
    plan_drift: float = 0.0,
    loop_actions: int = 0,
    total_actions: int = 0,
    constraint_violations: int = 0,
    recovery_attempted: int = 0,
    recovery_succeeded: int = 0,
    duplicate_side_effects: int = 0,
    tool_calls: int = 0,
    messages: int = 0,
    latency_ms: int = 0,
) -> dict[str, Any]:
    total = max(0, total_actions)
    loops = max(0, loop_actions)
    return {
        "success": int(bool(success and not false_completion)),
        "progress": max(0.0, min(1.0, progress)),
        "false_completion": int(false_completion),
        "plan_drift_rate": max(0.0, min(1.0, plan_drift)),
        "loop_rate": loops / total if total else 0.0,
        "constraint_violations": max(0, constraint_violations),
        "recovery_attempted": max(0, recovery_attempted),
        "recovery_succeeded": max(0, recovery_succeeded),
        "duplicate_side_effects": max(0, duplicate_side_effects),
        "tool_calls": max(0, tool_calls),
        "messages": max(0, messages),
        "model_calls": 0,
        "model_tokens": 0,
        "latency_ms_simulated": max(0, latency_ms),
        "pass^k": 0,
    }


def simulate(
    quest: dict[str, Any],
    config: str,
    seed: int,
    ablation: str = "none",
) -> dict[str, Any]:
    features = _features(config, ablation)
    action_count = int(quest["required_actions"])
    fault = str(quest.get("fault_profile", "clean"))
    digest = int(
        hashlib.sha256(
            f"{quest['id']}:{config}:{ablation}:{seed}".encode()
        ).hexdigest()[:8],
        16,
    )
    fault_injected = fault != "clean"
    recovered = False
    duplicate_side_effects = 0
    loop_actions = 0

    if fault == "transient":
        recovered = bool(features["checkpoint"] and features["restricted_replan"])
    elif fault == "duplicate":
        recovered = bool(features["checkpoint"] and features["idempotency"])
        duplicate_side_effects = 0 if features["idempotency"] else 1
    elif fault == "watchdog":
        recovered = bool(features["watchdog"] and features["restricted_replan"])
        loop_actions = 1 if features["watchdog"] else min(3, action_count)
    elif fault == "process_exit":
        recovered = bool(features["checkpoint"])
    elif fault == "false_completion":
        recovered = bool(features["verifier"])

    failure = fault_injected and not recovered
    plan_drift = 0.0
    if features["full_replan"]:
        plan_drift = 0.1 + (digest % 3) * 0.05
    if features["free_chat"]:
        plan_drift = max(plan_drift, 0.15)
    constraint_violations = int(plan_drift > 0 and not features["verifier"])
    false_completion = bool(failure and not features["verifier"])
    success = not failure and constraint_violations == 0
    completed_actions = action_count if success else max(0, action_count - 1)
    progress = completed_actions / max(action_count, 1)
    message_multiplier = 3 if features["free_chat"] else 1
    role_messages = 2 if features["multi_agent"] else 0
    return score_metrics(
        success=success,
        progress=progress,
        false_completion=false_completion,
        plan_drift=plan_drift,
        loop_actions=loop_actions,
        total_actions=action_count + loop_actions,
        constraint_violations=constraint_violations,
        recovery_attempted=int(fault_injected),
        recovery_succeeded=int(recovered),
        duplicate_side_effects=duplicate_side_effects,
        tool_calls=action_count + loop_actions,
        messages=(action_count + role_messages) * message_multiplier,
        latency_ms=action_count * 4 + role_messages + loop_actions * 4,
    )


def expected_run_count(profile: str) -> int:
    if profile == "smoke":
        return len(CONFIGS) * 30
    if profile != "formal":
        raise ValueError(f"unknown profile: {profile}")
    repeats_per_matrix = 9 * 5 + 21 * 3
    return len(CONFIGS) * (1 + len(ABLATIONS)) * repeats_per_matrix


def run(
    profile: str = "smoke",
    output: Path | None = None,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    if profile not in {"smoke", "formal"}:
        raise ValueError(f"unknown profile: {profile}")
    quests = load_catalog()
    ablations = ("none",) if profile == "smoke" else ("none", *ABLATIONS)
    rows: list[dict[str, Any]] = []
    for config in CONFIGS:
        for ablation in ablations:
            fingerprint = configuration_fingerprint(config, ablation)
            for quest in quests:
                repeat_count = (
                    5
                    if profile == "formal" and int(quest["index"]) in KEY_INDICES
                    else 3
                    if profile == "formal"
                    else 1
                )
                for repeat in range(repeat_count):
                    run_seed = seed + repeat
                    metrics = simulate(quest, config, run_seed, ablation)
                    run_id = f"{quest['id']}-{config}-{ablation}-{repeat}"
                    rows.append(
                        {
                            "run_id": run_id,
                            "workspace_id": f"mem-{run_id}-{fingerprint}",
                            "quest_id": quest["id"],
                            "family": quest["family"],
                            "length": quest["length"],
                            "config": config,
                            "config_name": CONFIGS[config]["name"],
                            "ablation": ablation,
                            "repeat": repeat,
                            "seed": run_seed,
                            "runtime_simulation": True,
                            "config_fingerprint": fingerprint,
                            **metrics,
                        }
                    )
    _annotate_pass_k(rows)
    assert len(rows) == expected_run_count(profile)
    if output is not None:
        write_artifacts(rows, output, profile, seed)
    return rows


def _annotate_pass_k(rows: list[dict[str, Any]], k: int = 3) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["quest_id"], row["config"], row["ablation"])].append(row)
    for items in groups.values():
        ordered = sorted(items, key=lambda item: item["repeat"])
        passed = len(ordered) >= k and all(item["success"] for item in ordered[:k])
        for item in ordered:
            item["pass^k"] = int(passed)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[f"{row['config']}:{row['ablation']}"].append(row)
    result: dict[str, Any] = {
        "runtime_simulation": True,
        "rows": len(rows),
        "configurations": {},
    }
    for key, items in sorted(groups.items()):
        count = len(items)
        recovery_attempts = sum(item["recovery_attempted"] for item in items)
        result["configurations"][key] = {
            "runs": count,
            "success_rate": sum(item["success"] for item in items) / count,
            "progress_rate": sum(item["progress"] for item in items) / count,
            "false_completion_rate": sum(item["false_completion"] for item in items)
            / count,
            "plan_drift_rate": sum(item["plan_drift_rate"] for item in items) / count,
            "loop_rate": sum(item["loop_rate"] for item in items) / count,
            "recovery_success_rate": (
                sum(item["recovery_succeeded"] for item in items) / recovery_attempts
                if recovery_attempts
                else 0.0
            ),
            "duplicate_side_effects": sum(
                item["duplicate_side_effects"] for item in items
            ),
            "pass^3": sum(item["pass^k"] for item in items) / count,
        }
    return result


def write_artifacts(
    rows: list[dict[str, Any]], output: Path, profile: str, seed: int
) -> None:
    if not rows:
        raise ValueError("benchmark produced no rows")
    output.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output / "results.json").write_text(
        json.dumps(rows, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    summary = _summary(rows)
    report_lines = [
        f"# ProjectTown v1 benchmark ({profile})",
        "",
        "This is a deterministic Runtime simulation, not an LLM or token benchmark.",
        "",
        f"Raw rows: {len(rows)}",
        "",
        "| Configuration | Runs | Success | Progress | Recovery | Duplicates |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, metrics in summary["configurations"].items():
        report_lines.append(
            f"| {key} | {metrics['runs']} | {metrics['success_rate']:.3f} | "
            f"{metrics['progress_rate']:.3f} | "
            f"{metrics['recovery_success_rate']:.3f} | "
            f"{metrics['duplicate_side_effects']} |"
        )
    report_lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- [Raw results (CSV)](results.csv)",
            "- [Raw results (JSON)](results.json)",
            "- [Success-rate chart](success.svg)",
            "- [Reproducibility manifest](manifest.json)",
            "",
            "## Failures and limitations",
            "",
            "- No external model, model latency, or model token use was measured.",
            "- The committed results exercise a deterministic feature/fault matrix.",
            "- Use real model adapters only in a separately labelled evaluation.",
            "",
        ]
    )
    (output / "report.md").write_text(
        "\n".join(report_lines), encoding="utf-8", newline="\n"
    )

    full_rates = {
        config: summary["configurations"][f"{config}:none"]["success_rate"]
        for config in CONFIGS
    }
    bars = []
    for index, (config, rate) in enumerate(full_rates.items()):
        y = 25 + index * 28
        width = int(360 * rate)
        bars.append(
            f'<text x="10" y="{y + 14}">{config}</text>'
            f'<rect x="45" y="{y}" width="{width}" height="18" fill="#4472c4"/>'
            f'<text x="{50 + width}" y="{y + 14}">{rate:.3f}</text>'
        )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="180">'
        '<text x="10" y="16">Runtime simulation success rate</text>'
        + "".join(bars)
        + "</svg>"
    )
    (output / "success.svg").write_text(svg + "\n", encoding="utf-8", newline="\n")

    artifact_names = ("results.csv", "results.json", "report.md", "success.svg")
    manifest = {
        "command": (
            "python -m backend.app.v1.evaluation --output "
            f"<output> --profile {profile} --seed {seed}"
        ),
        "profile": profile,
        "row_count": len(rows),
        "runtime_simulation": True,
        "seed": seed,
        "sha256": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in artifact_names
        },
        "simulator_version": SIMULATOR_VERSION,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("smoke", "formal"), default="smoke")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args()
    run(arguments.profile, arguments.output, arguments.seed)


if __name__ == "__main__":
    main()
