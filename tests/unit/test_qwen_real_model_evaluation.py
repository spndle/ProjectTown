from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.provider_secrets import ResolvedProviderConnection
from backend.app.runtime import stable_hash
from backend.app.v1.model_adapter import (
    ModelResponse,
    ModelUsage,
    PlanningCandidate,
    PlanningStepCandidate,
    validate_planning_candidate,
)
from benchmark.real_model_evaluation.qwen_runner import run_evaluation

_ENV = {
    "PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true",
    "PROJECTTOWN_QWEN_MODEL_EVAL_ENABLED": "true",
    "DASHSCOPE_BASE_URL": "https://workspace-1.cn-beijing.maas.aliyuncs.com/api/v1",
    "DASHSCOPE_API_KEY": "CANARY_QWEN_KEY_NEVER_PERSIST",
    "DASHSCOPE_MODEL": "qwen-plus",
}


class _FakeQwenAdapter:
    def __init__(self, connection: ResolvedProviderConnection) -> None:
        self.connection = connection

    def close(self) -> None:
        return None

    def create_planning_candidate(self, request):  # type: ignore[no-untyped-def]
        candidate = PlanningCandidate(
            id="candidate_eval",
            version=1,
            summary="Synthetic.",
            steps=[
                PlanningStepCandidate(
                    id="step_eval", title="Read", tool_name=request.allowed_tools[0]
                )
            ],
        )
        validated = validate_planning_candidate(
            candidate, allowed_tools=request.allowed_tools
        )
        return ModelResponse(
            request_hash=stable_hash(request.model_dump(mode="json")),
            candidate=validated.candidate,
            candidate_hash=validated.candidate_hash,
            usage=ModelUsage(
                input_tokens=3, output_tokens=2, total_tokens=5, cost_microunits=7
            ),
        )


def _adapter(connection: ResolvedProviderConnection) -> _FakeQwenAdapter:
    return _FakeQwenAdapter(connection)


def test_qwen_runner_gates_never_create_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    database, results = (
        Path("sandbox/qwen-real-eval/qwen.db"),
        Path("sandbox/qwen-real-eval/results"),
    )
    report = run_evaluation(
        live=False, database_path=database, results_root=results, environ=_ENV
    )
    assert report["status"] == "NOT_RUN" and report["reason"] == "LIVE_FLAG_REQUIRED"
    report = run_evaluation(
        live=True, database_path=database, results_root=results, environ={}
    )
    assert report["status"] == "NOT_RUN" and report["reason"] == "ENV_OPT_IN_REQUIRED"
    report = run_evaluation(
        live=True,
        database_path=database,
        results_root=results,
        environ={"PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true"},
    )
    assert (
        report["status"] == "NOT_RUN" and report["reason"] == "QWEN_ENV_OPT_IN_REQUIRED"
    )
    assert not database.exists() and not results.exists()


def test_qwen_runner_mock_report_is_hash_only_and_sandboxed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = 0

    def factory(connection: ResolvedProviderConnection) -> _FakeQwenAdapter:
        nonlocal calls
        calls += 1
        return _adapter(connection)

    database, results = (
        Path("sandbox/qwen-real-eval/qwen.db"),
        Path("sandbox/qwen-real-eval/results"),
    )
    report = run_evaluation(
        live=True,
        database_path=database,
        results_root=results,
        environ=_ENV,
        adapter_factory=factory,
    )
    assert (
        report["status"] == "COMPLETED" and report["adapter"] == "qwen-dashscope-native"
    )
    assert (
        report["execution_mode"] == "fresh"
        and report["dispatch_count"] == 2
        and report["replay_count"] == 0
    )
    assert all(item["idempotent_replay"] is False for item in report["results"])
    replay = run_evaluation(
        live=True,
        database_path=database,
        results_root=results,
        environ=_ENV,
        adapter_factory=factory,
    )
    assert calls == 2
    assert (
        replay["execution_mode"] == "cached"
        and replay["dispatch_count"] == 0
        and replay["replay_count"] == 2
    )
    assert all(item["idempotent_replay"] is True for item in replay["results"])
    assert all(item["cost_micro_cny"] == 7 for item in report["results"])
    content = "".join(
        path.read_text(encoding="utf-8") for path in results.glob("*.json")
    )
    assert (
        "CANARY_QWEN_KEY_NEVER_PERSIST" not in content and "Synthetic." not in content
    )


def test_qwen_runner_rejects_non_sandbox_targets(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="below sandbox"):
        run_evaluation(
            live=True,
            database_path=Path("outside.db"),
            results_root=Path("sandbox/qwen-real-eval/results"),
            environ=_ENV,
            adapter_factory=_adapter,
        )
    assert not Path("outside.db").exists()
