from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from backend.app.config import Settings
from backend.app.telemetry import (
    BoundedTelemetry,
    InMemoryTelemetrySink,
    NoOpTelemetry,
    TelemetryRecord,
)


def _record() -> TelemetryRecord:
    return TelemetryRecord(
        name="http.request",
        kind="span",
        timestamp_ns=time.time_ns(),
        duration_ms=1,
        trace_id="a" * 32,
        span_id="b" * 16,
        parent_span_id=None,
        attributes={
            "correlation_id": "a" * 32,
            "http.method": "GET",
            "http.status_code": 200,
        },
    )


def _wait_until(predicate, timeout: float = 0.5) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def test_record_contract_is_immutable_and_rejects_free_form_fields() -> None:
    record = _record()
    assert record.attributes["http.method"] == "GET"
    with pytest.raises(TypeError):
        record.attributes["prompt"] = "not allowed"  # type: ignore[index]
    with pytest.raises(ValueError):
        TelemetryRecord(
            name="model.call",
            kind="span",
            timestamp_ns=time.time_ns(),
            duration_ms=1,
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id=None,
            attributes={"prompt": "CANARY_SECRET"},
        )
    with pytest.raises(ValueError):
        TelemetryRecord(
            name="http.request",
            kind="span",
            timestamp_ns=time.time_ns(),
            duration_ms=1,
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id=None,
            attributes={"http.method": "X" * 161},
        )


def test_noop_starts_no_thread_and_never_raises() -> None:
    telemetry = NoOpTelemetry()
    telemetry.emit(_record())
    telemetry.close()


def test_close_racing_with_producers_has_a_stable_final_snapshot() -> None:
    for _ in range(100):
        sink = InMemoryTelemetrySink()
        telemetry = BoundedTelemetry(sink, queue_size=8, export_timeout_seconds=0.02)
        start = threading.Event()

        def producer(
            producer_start: threading.Event = start,
            producer_telemetry: BoundedTelemetry = telemetry,
        ) -> None:
            producer_start.wait()
            for _ in range(50):
                producer_telemetry.emit(_record())

        producers = [threading.Thread(target=producer) for _ in range(4)]
        for producer_thread in producers:
            producer_thread.start()
        start.set()
        telemetry.close(timeout_seconds=0.1)
        for producer_thread in producers:
            producer_thread.join(0.2)
            assert not producer_thread.is_alive()
        before = telemetry.counters()
        telemetry.emit(_record())
        time.sleep(0.002)
        assert telemetry.counters() == before
        assert not telemetry.worker_alive
        assert before.in_flight == 0


def test_telemetry_config_has_hard_bounds(tmp_path) -> None:  # type: ignore[no-untyped-def]
    base = {
        "database_path": tmp_path / "db.sqlite",
        "sandbox_root": tmp_path / "sandbox",
    }
    assert (
        Settings.from_mapping(
            {**base, "telemetry_queue_size": 4_096}
        ).telemetry_queue_size
        == 4_096
    )
    with pytest.raises(ValueError, match="telemetry_queue_size"):
        Settings.from_mapping({**base, "telemetry_queue_size": 4_097})
    with pytest.raises(ValueError, match="telemetry_export_timeout_seconds"):
        Settings.from_mapping({**base, "telemetry_export_timeout_seconds": 5.1})
    with pytest.raises(ValueError, match="telemetry_sample_every_n"):
        Settings.from_mapping({**base, "telemetry_sample_every_n": 10_001})


def test_sampler_is_deterministic_for_the_same_trace_and_counts_sampled_out() -> None:
    sink = InMemoryTelemetrySink()
    telemetry = BoundedTelemetry(sink, sample_every_n=2, export_timeout_seconds=0.05)
    sampled = _record()
    skipped = replace(sampled, trace_id="0" * 31 + "1")
    telemetry.emit(sampled)
    telemetry.emit(skipped)
    _wait_until(lambda: len(sink.snapshot()) == 1)
    counters = telemetry.counters()
    assert counters.submitted == 2
    assert counters.emitted == 1
    assert counters.sampled_out == 1
    telemetry.close(timeout_seconds=0.1)


def test_constructor_and_close_use_hard_timeout_caps() -> None:
    with pytest.raises(ValueError, match="timeout"):
        BoundedTelemetry(InMemoryTelemetrySink(), export_timeout_seconds=5.1)

    entered = threading.Event()
    release = threading.Event()
    telemetry = BoundedTelemetry(InMemoryTelemetrySink(), export_timeout_seconds=0.01)

    def stuck_export(record: TelemetryRecord) -> str:
        del record
        entered.set()
        release.wait(1)
        return "timeout"

    telemetry._export_once = stuck_export  # type: ignore[method-assign]
    telemetry.emit(_record())
    assert entered.wait(0.2)
    started = time.monotonic()
    telemetry.close(timeout_seconds=999)
    assert time.monotonic() - started < 0.2
    stable = telemetry.counters()
    assert stable.closed and stable.circuit_open
    assert stable.queued == stable.in_flight == 0
    assert stable.export_timeouts == 1
    time.sleep(0.02)
    assert telemetry.counters() == stable
    release.set()


def test_bounded_pump_exports_to_test_sink_and_is_bounded_on_close() -> None:
    sink = InMemoryTelemetrySink()
    telemetry = BoundedTelemetry(sink, queue_size=2, export_timeout_seconds=0.05)
    telemetry.emit(_record())
    _wait_until(lambda: len(sink.snapshot()) == 1)
    telemetry.close(timeout_seconds=0.1)
    assert not telemetry.worker_alive


def test_close_flushes_records_accepted_before_lifespan_shutdown() -> None:
    for _ in range(100):
        sink = InMemoryTelemetrySink()
        telemetry = BoundedTelemetry(sink, export_timeout_seconds=0.05)
        telemetry.emit(_record())
        telemetry.emit(_record())
        telemetry.close()
        assert len(sink.snapshot()) == 2
        assert not telemetry.worker_alive


def test_queue_full_and_exporter_failure_are_isolated() -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_exporter(record: TelemetryRecord) -> None:
        del record
        entered.set()
        release.wait(0.2)

    telemetry = BoundedTelemetry(
        slow_exporter, queue_size=1, export_timeout_seconds=0.1
    )
    telemetry.emit(_record())
    assert entered.wait(0.2)
    telemetry.emit(_record())
    telemetry.emit(_record())
    release.set()
    telemetry.close(timeout_seconds=0.2)

    def raises(record: TelemetryRecord) -> None:
        del record
        raise RuntimeError("CANARY_EXPORTER_EXCEPTION")

    failed = BoundedTelemetry(raises, export_timeout_seconds=0.05)
    failed.emit(_record())
    _wait_until(lambda: failed.counters().export_failures == 1)
    assert not failed.circuit_open
    failed.close(timeout_seconds=0.05)
    assert not failed.worker_alive


def test_blocking_exporter_opens_circuit_with_at_most_one_helper() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def blocked(record: TelemetryRecord) -> None:
        nonlocal calls
        del record
        with lock:
            calls += 1
        entered.set()
        release.wait(1)

    telemetry = BoundedTelemetry(blocked, queue_size=3, export_timeout_seconds=0.02)
    telemetry.emit(_record())
    assert entered.wait(0.2)
    _wait_until(lambda: telemetry.circuit_open)
    telemetry.emit(_record())
    telemetry.emit(_record())
    time.sleep(0.04)
    with lock:
        assert calls == 1
    started = time.monotonic()
    telemetry.close(timeout_seconds=0.02)
    assert time.monotonic() - started < 0.15
    release.set()


def test_ten_thousand_record_flood_has_exact_bounded_counters() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked(record: TelemetryRecord) -> None:
        del record
        entered.set()
        release.wait(1)

    telemetry = BoundedTelemetry(blocked, queue_size=3, export_timeout_seconds=0.02)
    telemetry.emit(_record())
    assert entered.wait(0.2)
    for _ in range(9_999):
        telemetry.emit(_record())
    _wait_until(lambda: telemetry.circuit_open)
    counters = telemetry.counters()
    assert counters.emitted == 4
    assert counters.dropped == 9_999
    assert counters.export_timeouts == 1
    assert counters.queue_capacity == 3
    assert counters.circuit_open
    telemetry.close(timeout_seconds=0.02)
    release.set()


def test_nested_secret_canary_is_never_present_in_model_record() -> None:
    sink = InMemoryTelemetrySink()
    telemetry = BoundedTelemetry(sink, export_timeout_seconds=0.05)
    canary = "CANARY_NESTED_TELEMETRY_SECRET"
    telemetry.emit_model_call(
        quest_id="quest-1",
        adapter="fake",
        model="offline",
        prompt_version="planning-v1",
        call_id="call-1",
        attempt_id="attempt-1",
        outcome="failed",
        validation_status="invalid",
        error_type="INVALID_RESPONSE",
        idempotent_replay=False,
        reserved_tokens=20,
        max_output_tokens=10,
        usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        duration_ms=1,
    )
    _wait_until(lambda: len(sink.snapshot()) == 2)
    assert canary not in repr(sink.snapshot())
    telemetry.close(timeout_seconds=0.1)
