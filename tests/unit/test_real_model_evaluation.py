from __future__ import annotations

import json
import sqlite3
import time
from hashlib import sha256
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from backend.app import provider_secrets
from backend.app.provider_secrets import ResolvedProviderConnection
from backend.app.v1.openai_adapter import OPENAI_MODEL_SNAPSHOT, OpenAIResponsesAdapter
from benchmark.real_model_evaluation import runner as runner_module
from benchmark.real_model_evaluation.runner import main, run_evaluation

_URL = "https://api.openai.com/v1"
_MODEL = OPENAI_MODEL_SNAPSHOT


def _connection() -> ResolvedProviderConnection:
    from backend.app.provider_secrets import resolve_provider_connection

    return resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "CANARY_KEY_NEVER_PERSIST",
            "OPENAI_MODEL": _MODEL,
        },
    )


def _success_adapter(
    *, connection: ResolvedProviderConnection
) -> OpenAIResponsesAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        summary = json.loads(
            json.loads(request.content)["input"][0]["content"][0]["text"]
        )
        candidate = {
            "schema_version": 1,
            "id": "synthetic_candidate",
            "version": 1,
            "summary": "Synthetic planning result.",
            "steps": [
                {
                    "id": "synthetic_step",
                    "title": "Inspect",
                    "description": "",
                    "tool_name": summary["allowed_tools"][0],
                    "tool_args": {},
                    "dependencies": [],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "model": OPENAI_MODEL_SNAPSHOT,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(candidate)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 19, "output_tokens": 7, "total_tokens": 26},
            },
        )

    return OpenAIResponsesAdapter(
        connection=connection, transport=httpx.MockTransport(handler)
    )


def _env() -> dict[str, str]:
    return {
        "PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true",
        "OPENAI_BASE_URL": _URL,
        "OPENAI_API_KEY": "CANARY_KEY_NEVER_PERSIST",
        "OPENAI_MODEL": _MODEL,
    }


def test_gate_requires_full_connection_and_zero_transport(tmp_path: Path) -> None:
    calls = 0

    def factory(*, connection: ResolvedProviderConnection) -> OpenAIResponsesAdapter:
        nonlocal calls
        calls += 1
        raise AssertionError("must not construct")

    for env in (
        {},
        {"PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true", "OPENAI_API_KEY": "x"},
        {"PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true", "OPENAI_BASE_URL": _URL},
        {
            "PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true",
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "x",
        },
        {
            "PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true",
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "x",
            "OPENAI_MODEL": "qwen-plus",
        },
    ):
        result = run_evaluation(
            live=True,
            database_path=tmp_path / "eval.db",
            results_root=tmp_path / "results",
            environ=env,
            adapter_factory=factory,
        )
        assert result["status"] == "NOT_RUN"
    assert calls == 0 and not (tmp_path / "results").exists()


def test_hash_only_report_and_database_do_not_persist_connection(
    tmp_path: Path,
) -> None:
    database, results = tmp_path / "eval.db", tmp_path / "results"
    calls = 0

    def factory(*, connection: ResolvedProviderConnection) -> OpenAIResponsesAdapter:
        nonlocal calls
        calls += 1
        return _success_adapter(connection=connection)

    result = run_evaluation(
        live=True,
        database_path=database,
        results_root=results,
        environ=_env(),
        adapter_factory=factory,
    )
    assert (
        result["status"] == "COMPLETED"
        and result["destination_config_hash"] == _connection().destination_config_hash
    )
    assert result["execution_mode"] == "fresh"
    assert result["dispatch_count"] == 2 and result["replay_count"] == 0
    assert all(item["idempotent_replay"] is False for item in result["results"])
    replay = run_evaluation(
        live=True,
        database_path=database,
        results_root=results,
        environ=_env(),
        adapter_factory=factory,
    )
    assert calls == 2
    assert replay["status"] == "COMPLETED" and replay["execution_mode"] == "cached"
    assert replay["dispatch_count"] == 0 and replay["replay_count"] == 2
    assert all(item["idempotent_replay"] is True for item in replay["results"])
    assert result["connection_config_hash"] == _connection().connection_config_hash
    assert result["model"]["model_snapshot"] == _MODEL
    report = next(results.glob("*.json")).read_text(encoding="utf-8")
    assert "CANARY_KEY_NEVER_PERSIST" not in report and _URL not in report
    db = sqlite3.connect(database)
    try:
        dumped = " ".join(
            str(value or "")
            for row in db.execute(
                "SELECT parameters_json, candidate_json FROM v1_model_attempts"
            )
            for value in row
        )
        assert "CANARY_KEY_NEVER_PERSIST" not in dumped and _URL not in dumped
    finally:
        db.close()


def test_not_run_cli_without_url_or_key(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PROJECTTOWN_REAL_MODEL_EVAL_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert main(["--live", "--database-path", str(tmp_path / "eval.db")]) == 2


def test_formal_and_non_sandbox_database_targets_are_rejected_before_factory(
    tmp_path: Path,
) -> None:
    manifest = Path("benchmark/results/formal-v1.0/manifest.json")
    before = sha256(manifest.read_bytes()).hexdigest()

    def forbidden(*, connection: ResolvedProviderConnection) -> OpenAIResponsesAdapter:
        raise AssertionError("factory must not run")

    try:
        run_evaluation(
            live=True,
            database_path=tmp_path / "eval.db",
            results_root=Path("benchmark/results/formal-v1.0/x"),
            environ=_env(),
            adapter_factory=forbidden,
        )
    except ValueError as error:
        assert "formal-v1.0" in str(error)
    else:
        raise AssertionError("formal result target accepted")
    assert sha256(manifest.read_bytes()).hexdigest() == before
    try:
        run_evaluation(
            live=True,
            database_path=Path("README.md"),
            results_root=tmp_path / "results",
            environ=_env(),
            adapter_factory=forbidden,
        )
    except ValueError as error:
        assert "under sandbox" in str(error)
    else:
        raise AssertionError("workspace database target accepted")


def test_runner_exception_report_does_not_leak_connection_or_constructed_error(
    tmp_path: Path,
) -> None:
    canary = "CANARY_FACTORY_EXCEPTION"

    def failing(*, connection: ResolvedProviderConnection) -> OpenAIResponsesAdapter:
        raise RuntimeError(canary)

    result = run_evaluation(
        live=True,
        database_path=_sandbox_database(f"failing-{time.time_ns()}.db"),
        results_root=tmp_path / "reports",
        environ=_env(),
        adapter_factory=failing,
    )
    report = next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8")
    assert result["reason"] == "RUNNER_EXCEPTION"
    assert (
        canary not in report
        and "CANARY_KEY_NEVER_PERSIST" not in report
        and _URL not in report
    )


def test_local_file_source_requires_test_profile_and_never_leaks(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    local = tmp_path / "model-providers.local.toml"
    local.write_text(
        f'version = 3\n[providers.openai]\nbase_url = "{_URL}"\napi_key = "CANARY_LOCAL"\nmodel = "{_MODEL}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    result = run_evaluation(
        live=True,
        database_path=Path("sandbox/tmp/provider-connection-20260821/local.db"),
        results_root=tmp_path / "reports",
        environ={
            "PROJECTTOWN_REAL_MODEL_EVAL_ENABLED": "true",
            "PROJECTTOWN_SECRET_SOURCE": "local_file",
            "PROJECTTOWN_PROFILE": "test",
        },
        adapter_factory=_success_adapter,
    )
    assert result["status"] == "COMPLETED"
    assert "CANARY_LOCAL" not in next((tmp_path / "reports").glob("*.json")).read_text(
        encoding="utf-8"
    )


class _FakeStorage:
    instances: ClassVar[list[_FakeStorage]] = []

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.close_calls = 0
        self.__class__.instances.append(self)

    @staticmethod
    def phase1c_cost_reservation(
        input_tokens: int,
        output_tokens: int,
        *,
        provider: str = "openai",
        model: str = _MODEL,
    ) -> dict[str, int]:
        assert provider == "openai" and model == _MODEL
        return {"reserved_micro_cny": input_tokens + output_tokens}

    def model_cost_usage(self) -> dict[str, int]:
        return {"reserved_micro_cny": 0}

    def get_quest(self, quest_id: str) -> None:
        del quest_id

    def create_draft(
        self,
        quest_id: str,
        contract: dict[str, object],
        plan: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        del contract, plan, kwargs
        return {"id": quest_id, "state_version": 1}

    def close(self) -> None:
        self.close_calls += 1


def _sandbox_database(name: str) -> Path:
    return Path("sandbox") / "tmp" / "provider-connection-20260821" / name


def test_factory_exception_closes_storage_without_closing_uncreated_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _FakeStorage.instances.clear()
    monkeypatch.setattr(runner_module, "V1Storage", _FakeStorage)
    canary = "CANARY_FACTORY_EXCEPTION"
    factory_calls = 0

    def factory(*, connection: ResolvedProviderConnection) -> OpenAIResponsesAdapter:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError(canary)

    result = run_evaluation(
        live=True,
        database_path=_sandbox_database(f"factory-close-{time.time_ns()}.db"),
        results_root=tmp_path / "reports",
        environ=_env(),
        adapter_factory=factory,
    )
    report = next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert result["reason"] == "RUNNER_EXCEPTION"
    assert factory_calls == 1 and _FakeStorage.instances[0].close_calls == 1
    assert canary not in report + captured.out + captured.err
    assert _URL not in report and "CANARY_KEY_NEVER_PERSIST" not in report


def test_close_failure_cannot_skip_storage_close_or_leak_canaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _FakeStorage.instances.clear()
    monkeypatch.setattr(runner_module, "V1Storage", _FakeStorage)
    evaluation_canary = "CANARY_EVALUATION_EXCEPTION"
    close_canary = "CANARY_CLOSE_EXCEPTION"

    class FailingCoordinator:
        def __init__(self, storage: _FakeStorage, adapter: object) -> None:
            self.storage = storage
            self.adapter = adapter

        def run(self, **kwargs: object) -> object:
            raise RuntimeError(evaluation_canary)

    class CloseFailingAdapter:
        def close(self) -> None:
            raise RuntimeError(close_canary)

    monkeypatch.setattr(runner_module, "ModelCallCoordinator", FailingCoordinator)
    result = run_evaluation(
        live=True,
        database_path=_sandbox_database("close-failure.db"),
        results_root=tmp_path / "reports",
        environ=_env(),
        adapter_factory=lambda *, connection: CloseFailingAdapter(),
    )
    report = next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert result["reason"] == "RUNNER_EXCEPTION"
    assert _FakeStorage.instances[0].close_calls == 1
    assert evaluation_canary not in report + captured.out + captured.err
    assert close_canary not in report + captured.out + captured.err
    assert _URL not in report and "CANARY_KEY_NEVER_PERSIST" not in report
