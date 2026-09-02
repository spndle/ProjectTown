"""Default-off, sandbox-only native DashScope Qwen technical evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from backend.app.provider_secrets import (
    ResolvedProviderConnection,
    SecretResolutionError,
    resolve_provider_connection,
)
from backend.app.runtime import stable_hash
from backend.app.v1.model_runtime import ModelCallCoordinator
from backend.app.v1.prompt_registry import PROMPT_REGISTRY_HASH, PROMPT_VERSION
from backend.app.v1.provider_summary import parse_structured_goal_summary
from backend.app.v1.qwen_adapter import QwenDashScopeAdapter
from backend.app.v1.storage import V1Storage

_FIXTURES_PATH = Path(__file__).with_name("fixtures.json")
_DEFAULT_DATABASE = Path("sandbox") / "qwen-real-eval" / "qwen-phase2.db"
_DEFAULT_RESULTS = Path("sandbox") / "qwen-real-eval" / "results"
_ESTIMATED_INPUT_TOKENS = 4096
_MAX_OUTPUT_TOKENS = 512
_SCHEMA_VERSION = "planning-candidate-v1"


def _load_fixtures() -> tuple[str, list[dict[str, Any]]]:
    raw = json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or set(raw) != {"dataset_version", "fixtures"}:
        raise ValueError("evaluation fixtures are invalid")
    version, fixtures = raw["dataset_version"], raw["fixtures"]
    if (
        not isinstance(version, str)
        or not isinstance(fixtures, list)
        or len(fixtures) != 2
    ):
        raise ValueError("evaluation fixtures are invalid")
    parsed: list[dict[str, Any]] = []
    for item in fixtures:
        if not isinstance(item, Mapping) or set(item) != {"fixture_id", "summary"}:
            raise ValueError("evaluation fixtures are invalid")
        fixture_id = item["fixture_id"]
        if not isinstance(fixture_id, str) or not fixture_id.isidentifier():
            raise ValueError("evaluation fixtures are invalid")
        parsed.append(
            {
                "fixture_id": fixture_id,
                "summary": parse_structured_goal_summary(item["summary"]),
            }
        )
    return version, parsed


def _safe_sandbox_path(path: Path, *, kind: str) -> Path:
    resolved = path.resolve()
    sandbox = (Path.cwd() / "sandbox").resolve()
    formal = (Path.cwd() / "benchmark" / "results" / "formal-v1.0").resolve()
    if (
        sandbox not in resolved.parents
        or resolved == formal
        or formal in resolved.parents
    ):
        raise ValueError(f"Qwen evaluation {kind} must be located below sandbox")
    return resolved


def _gate(
    live: bool, environ: Mapping[str, str]
) -> tuple[bool, str, ResolvedProviderConnection | None]:
    if not live:
        return False, "LIVE_FLAG_REQUIRED", None
    if environ.get("PROJECTTOWN_REAL_MODEL_EVAL_ENABLED") != "true":
        return False, "ENV_OPT_IN_REQUIRED", None
    if environ.get("PROJECTTOWN_QWEN_MODEL_EVAL_ENABLED") != "true":
        return False, "QWEN_ENV_OPT_IN_REQUIRED", None
    try:
        return True, "READY", resolve_provider_connection("qwen", environ=environ)
    except SecretResolutionError as error:
        if error.code in {"SECRET_API_KEY_INVALID", "SECRET_CONNECTION_REQUIRED"}:
            return False, "QWEN_CONNECTION_REQUIRED", None
        return False, error.code, None


def _local_quest(storage: V1Storage, fixture_id: str) -> dict[str, Any]:
    quest_id = f"phase2_qwen_eval_{fixture_id}"
    existing = storage.get_quest(quest_id)
    if existing is not None:
        return existing
    contract = {
        "id": f"contract_{fixture_id}",
        "quest_id": quest_id,
        "version": 1,
        "goal": "Synthetic local evaluation binding",
        "constraints": [],
        "non_goals": [],
        "budget": {
            "max_steps": 1,
            "max_tool_calls": 1,
            "max_messages": 1,
            "max_tokens": 10000,
            "max_seconds": 60.0,
            "max_replans": 0,
        },
        "acceptance_criteria": [
            {
                "id": "criterion_local",
                "kind": "markdown",
                "description": "Synthetic local check",
                "required": True,
            }
        ],
        "confirmed": True,
    }
    plan = {
        "id": f"plan_{fixture_id}",
        "version": 1,
        "milestones": [
            {
                "id": "milestone_local",
                "plan_version": 1,
                "position": 1,
                "title": "Synthetic local binding",
                "description": "",
                "tool_name": "read",
                "tool_args": {},
                "dependencies": [],
                "acceptance_criteria": [],
                "status": "pending",
                "evidence_ids": [],
                "attempt": 0,
            }
        ],
        "metadata": {"template_id": "phase2_qwen_synthetic"},
    }
    return storage.create_draft(
        quest_id, contract, plan, workspace="phase2-qwen-local", route=["eval"]
    )


def run_evaluation(
    *,
    live: bool,
    database_path: Path = _DEFAULT_DATABASE,
    results_root: Path = _DEFAULT_RESULTS,
    environ: Mapping[str, str] | None = None,
    adapter_factory: Callable[
        [ResolvedProviderConnection], QwenDashScopeAdapter
    ] = QwenDashScopeAdapter,
) -> dict[str, Any]:
    """Run two synthetic fixtures only after all Qwen live gates have passed."""
    env = os.environ if environ is None else environ
    enabled, reason, connection = _gate(live, env)
    version, fixtures = _load_fixtures()
    quote = V1Storage.phase1c_cost_reservation(
        _ESTIMATED_INPUT_TOKENS, _MAX_OUTPUT_TOKENS, provider="qwen", model="qwen-plus"
    )
    if not enabled:
        return {
            "status": "NOT_RUN",
            "reason": reason,
            "dataset_version": version,
            "fixture_count": len(fixtures),
            "dry_run_max_per_call_micro_cny": quote["reserved_micro_cny"],
            "dry_run_max_total_micro_cny": int(quote["reserved_micro_cny"])
            * len(fixtures),
            **_execution_metadata([]),
        }
    report_root = _safe_sandbox_path(results_root, kind="results")
    database = _safe_sandbox_path(database_path, kind="database")
    started, results = time.perf_counter(), []
    storage: V1Storage | None = None
    adapter: QwenDashScopeAdapter | None = None
    try:
        if connection is None:
            raise RuntimeError("evaluation connection unavailable")
        database.parent.mkdir(parents=True, exist_ok=True)
        storage, adapter = V1Storage(database), adapter_factory(connection=connection)
        before = storage.model_cost_usage(provider="qwen", model=connection.model)
        for fixture in fixtures:
            summary, quest = (
                fixture["summary"],
                _local_quest(storage, fixture["fixture_id"]),
            )
            begin = time.perf_counter()
            result = ModelCallCoordinator(storage, adapter).run(
                quest_id=quest["id"],
                idempotency_key=f"phase2-qwen-{fixture['fixture_id']}-v1",
                prompt_version=PROMPT_VERSION,
                input_payload=summary.canonical_payload(),
                allowed_tools=list(summary.allowed_tools),
                sanitized_parameters={
                    "structured_goal_summary": summary.canonical_payload(),
                    "structured_goal_summary_hash": summary.summary_hash,
                },
                reserved_tokens=_ESTIMATED_INPUT_TOKENS + _MAX_OUTPUT_TOKENS,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                expected_state_version=quest["state_version"],
                adapter_label="qwen-dashscope-native",
                model_label=connection.model,
                cost_reservation=quote,
                retain_candidate=False,
            )
            record = storage.get_model_call(result.call_id or "")
            attempt = record["attempts"][-1] if record else {}
            usage, cost = attempt.get("usage") or {}, attempt.get("cost") or {}
            results.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "summary_hash": summary.summary_hash,
                    "status": result.outcome,
                    "error_code": result.error_code,
                    "input_hash": record["call"]["input_hash"] if record else None,
                    "candidate_hash": result.candidate_hash,
                    "response_hash": result.response_hash,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cost_micro_cny": cost.get("cost_microunits"),
                    "idempotent_replay": result.idempotent_replay,
                    "latency_ms": int((time.perf_counter() - begin) * 1000),
                }
            )
        after = storage.model_cost_usage(provider="qwen", model=connection.model)
        report: dict[str, Any] = {
            "status": "COMPLETED"
            if len(results) == 2
            and all(x["status"] == "validated_current" for x in results)
            else "FAILED",
            "dataset_version": version,
            "fixture_count": len(fixtures),
            "prompt_version": PROMPT_VERSION,
            "prompt_registry_hash": PROMPT_REGISTRY_HASH,
            "output_schema_version": _SCHEMA_VERSION,
            "adapter": "qwen-dashscope-native",
            "model": connection.model,
            "destination_config_hash": connection.destination_config_hash,
            "connection_config_hash": connection.connection_config_hash,
            "budget_before": before,
            "budget_after": after,
            "results": results,
            **_execution_metadata(results),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception:  # noqa: BLE001
        report = {
            "status": "FAILED",
            "reason": "RUNNER_EXCEPTION",
            "dataset_version": version,
            "fixture_count": len(fixtures),
            "adapter": "qwen-dashscope-native",
            "model": connection.model if connection else None,
            "results": results,
            **_execution_metadata(results),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if adapter is not None:
            adapter.close()
        if storage is not None:
            storage.close()
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"phase2-qwen-{int(time.time() * 1000)}.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    report["report_hash"] = stable_hash(
        json.loads(report_path.read_text(encoding="utf-8"))
    )
    return report


def _execution_metadata(results: list[dict[str, Any]]) -> dict[str, int | str]:
    replay_count = sum(item.get("idempotent_replay") is True for item in results)
    dispatch_count = len(results) - replay_count
    mode = (
        "fresh" if replay_count == 0 else "cached" if dispatch_count == 0 else "mixed"
    )
    return {
        "dispatch_count": dispatch_count,
        "replay_count": replay_count,
        "execution_mode": mode,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated ProjectTown Qwen technical evaluation"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--database-path", type=Path, default=_DEFAULT_DATABASE)
    parser.add_argument("--results-root", type=Path, default=_DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    report = run_evaluation(
        live=args.live, database_path=args.database_path, results_root=args.results_root
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return (
        0
        if report["status"] == "COMPLETED"
        else 2
        if report["status"] == "NOT_RUN"
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
