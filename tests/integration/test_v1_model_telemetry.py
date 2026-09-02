from __future__ import annotations

import sqlite3

import pytest

from backend.app.runtime import stable_hash
from backend.app.telemetry import (
    BoundedTelemetry,
    InMemoryTelemetrySink,
    NoOpTelemetry,
    Telemetry,
)
from backend.app.v1.model_adapter import DeterministicFakeModelAdapter
from backend.app.v1.model_runtime import ModelCallCoordinator
from tests.integration.test_v1_model_runtime import _DriftAdapter, _planned, _run


def _wait_for_records(sink: InMemoryTelemetrySink, count: int) -> None:
    import time

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if len(sink.snapshot()) >= count:
            return
        time.sleep(0.005)
    assert len(sink.snapshot()) >= count


class _BrokenTelemetry(Telemetry):
    def emit(self, record) -> None:  # type: ignore[no-untyped-def]
        del record
        raise RuntimeError("CANARY_TELEMETRY_FAILURE")

    def close(self, timeout_seconds: float | None = None) -> None:
        del timeout_seconds


def test_model_outcomes_emit_only_auditable_summary_and_do_not_change_results(
    api,
) -> None:  # type: ignore[no-untyped-def]
    quest = _planned(api)
    sink = InMemoryTelemetrySink()
    telemetry = BoundedTelemetry(sink, export_timeout_seconds=0.05)
    adapter = DeterministicFakeModelAdapter(
        known_failure_input_hashes=[stable_hash({"goal": "known"})],
        unknown_outcome_input_hashes=[stable_hash({"goal": "unknown"})],
    )
    coordinator = ModelCallCoordinator(
        api.app.state.runtime_storage, adapter, telemetry=telemetry
    )
    success = _run(
        coordinator, quest, key="success", payload={"goal": "CANARY_PROMPT_SECRET"}
    )
    known = _run(coordinator, quest, key="known", payload={"goal": "known"})
    unknown = _run(coordinator, quest, key="unknown", payload={"goal": "unknown"})
    drift_adapter = _DriftAdapter(
        api.app.state.runtime_storage, quest["id"], quest["state_version"]
    )
    stale = _run(
        ModelCallCoordinator(
            api.app.state.runtime_storage, drift_adapter, telemetry=telemetry
        ),
        quest,
        key="stale",
        payload={"goal": "stale"},
    )
    replay = _run(
        coordinator, quest, key="success", payload={"goal": "CANARY_PROMPT_SECRET"}
    )
    _wait_for_records(sink, 10)
    telemetry.close(timeout_seconds=0.2)
    records = sink.snapshot()
    assert [result.outcome for result in (success, known, unknown, stale, replay)] == [
        "validated_current",
        "failed",
        "unknown_outcome",
        "stale",
        "validated_current",
    ]
    spans = [record for record in records if record.kind == "span"]
    metrics = [record for record in records if record.kind == "metric"]
    assert len(spans) == len(metrics) == 5
    assert {record.attributes["model.outcome"] for record in spans} >= {
        "validated_current",
        "failed",
        "unknown_outcome",
        "stale",
    }
    assert any(record.attributes["model.idempotent_replay"] is True for record in spans)
    assert all("model.call_id" not in record.attributes for record in metrics)
    assert all("model.call_id" in record.attributes for record in spans)
    assert "CANARY_PROMPT_SECRET" not in repr(records)
    assert all(
        "model.total_tokens" in record.attributes
        or record.attributes["model.outcome"] == "unknown_outcome"
        for record in spans
    )


def test_telemetry_failure_does_not_change_model_result(api) -> None:  # type: ignore[no-untyped-def]
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    coordinator = ModelCallCoordinator(
        api.app.state.runtime_storage, adapter, telemetry=_BrokenTelemetry()
    )
    result = _run(coordinator, quest, payload={"goal": "normal"})
    assert result.outcome == "validated_current"
    assert adapter.call_count == 1


@pytest.mark.parametrize("mode", ["noop", "broken", "bounded"])
def test_invalid_inputs_never_escape_or_leak_through_telemetry(api, mode: str) -> None:  # type: ignore[no-untyped-def]
    sink = InMemoryTelemetrySink()
    telemetry: Telemetry
    if mode == "noop":
        telemetry = NoOpTelemetry()
    elif mode == "broken":
        telemetry = _BrokenTelemetry()
    else:
        telemetry = BoundedTelemetry(sink, export_timeout_seconds=0.05)
    coordinator = ModelCallCoordinator(
        api.app.state.runtime_storage,
        DeterministicFakeModelAdapter(),
        telemetry=telemetry,
    )
    canary = "CANARY_INVALID_PAYLOAD_SECRET"
    result = coordinator.run(
        quest_id="q" * 161,
        idempotency_key="key-1",
        prompt_version="planning-v1",
        input_payload={"api_key": canary},
        allowed_tools=["write_file"],
        sanitized_parameters={},
        reserved_tokens=32,
        max_output_tokens=16,
        expected_state_version=1,
        adapter_label="fake",
        model_label="offline",
    )
    assert result.outcome == "invalid"
    assert result.error_code == "INVALID_REQUEST"
    if isinstance(telemetry, BoundedTelemetry):
        telemetry.close(timeout_seconds=0.1)
        assert canary not in repr(sink.snapshot())
        assert sink.snapshot() == ()


class _PostSettlementReadFailureStorage:
    def __init__(self, storage) -> None:  # type: ignore[no-untyped-def]
        self._storage = storage
        self.reads = 0

    def get_model_call(self, call_id: str):  # type: ignore[no-untyped-def]
        self.reads += 1
        if self.reads > 1:
            raise sqlite3.OperationalError("CANARY_POST_SETTLEMENT_READ")
        return self._storage.get_model_call(call_id)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._storage, name)


def test_post_settlement_telemetry_storage_read_cannot_change_result(api) -> None:  # type: ignore[no-untyped-def]
    quest = _planned(api)
    storage = _PostSettlementReadFailureStorage(api.app.state.runtime_storage)
    telemetry = BoundedTelemetry(InMemoryTelemetrySink(), export_timeout_seconds=0.05)
    result = _run(
        ModelCallCoordinator(
            storage, DeterministicFakeModelAdapter(), telemetry=telemetry
        ),
        quest,
    )
    telemetry.close(timeout_seconds=0.1)
    assert result.outcome == "validated_current"
    assert storage.reads == 2


def test_noop_telemetry_skips_post_result_storage_read(api) -> None:  # type: ignore[no-untyped-def]
    quest = _planned(api)
    storage = _PostSettlementReadFailureStorage(api.app.state.runtime_storage)
    result = _run(
        ModelCallCoordinator(
            storage, DeterministicFakeModelAdapter(), telemetry=NoOpTelemetry()
        ),
        quest,
    )
    assert result.outcome == "validated_current"
    assert storage.reads == 1
