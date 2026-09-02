"""Failure-isolated, local OpenTelemetry-shaped records for Phase 1B.

This is deliberately not an OpenTelemetry SDK integration. It has a closed,
immutable record contract, a bounded in-process pump, and no network exporter.
Telemetry errors, saturation, or exporter timeouts never propagate to Quest or
HTTP work.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

_AttributeValue = str | int | bool
_RecordKind = Literal["span", "metric"]
_ALLOWED_ATTRIBUTES = frozenset(
    {
        "correlation_id",
        "error.type",
        "http.method",
        "http.status_code",
        "model.adapter",
        "model.attempt_id",
        "model.call_id",
        "model.cost_microunits",
        "model.idempotent_replay",
        "model.input_tokens",
        "model.max_output_tokens",
        "model.name",
        "model.operation",
        "model.outcome",
        "model.output_tokens",
        "model.prompt_version",
        "model.quest_id",
        "model.reserved_tokens",
        "model.total_tokens",
        "model.validation_status",
    }
)
_TRACE_ID = ContextVar("projecttown_telemetry_trace_id", default=None)


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """A timestamped immutable record with a closed, secret-safe schema."""

    name: str
    kind: _RecordKind
    timestamp_ns: int
    duration_ms: int
    trace_id: str
    span_id: str
    parent_span_id: str | None
    attributes: Mapping[str, _AttributeValue]

    def __post_init__(self) -> None:
        if self.name not in {"http.request", "model.call"}:
            raise ValueError("telemetry record name is not allowed")
        if self.kind not in {"span", "metric"} or self.duration_ms < 0:
            raise ValueError("telemetry record metadata is invalid")
        if not _is_hex(self.trace_id, 32) or not _is_hex(self.span_id, 16):
            raise ValueError("telemetry trace identity is invalid")
        if self.parent_span_id is not None and not _is_hex(self.parent_span_id, 16):
            raise ValueError("telemetry parent span identity is invalid")
        values = dict(self.attributes)
        if len(values) > 20 or set(values) - _ALLOWED_ATTRIBUTES:
            raise ValueError("telemetry attributes are not allowed")
        if any(
            not isinstance(value, (str, int, bool))
            or isinstance(value, float)
            or (isinstance(value, str) and len(value) > 160)
            for value in values.values()
        ):
            raise ValueError("telemetry attribute values must be scalar")
        object.__setattr__(self, "attributes", MappingProxyType(values))


@dataclass(frozen=True, slots=True)
class TelemetryCounters:
    submitted: int
    emitted: int
    queued: int
    in_flight: int
    exported: int
    dropped: int
    export_failures: int
    export_timeouts: int
    sampled_out: int
    queue_capacity: int
    closed: bool
    circuit_open: bool


class Telemetry:
    """Best-effort facade. Implementations must never raise into callers."""

    def emit(self, record: TelemetryRecord) -> None:
        raise NotImplementedError

    def close(self, timeout_seconds: float | None = None) -> None:
        raise NotImplementedError

    @property
    def enabled(self) -> bool:
        return True

    @contextmanager
    def bind_trace(self, trace_id: str) -> Iterator[None]:
        token = _TRACE_ID.set(trace_id)
        try:
            yield
        finally:
            _TRACE_ID.reset(token)

    def emit_http_request(
        self, *, correlation_id: str, method: str, status_code: int, duration_ms: int
    ) -> None:
        try:
            self._emit_pair(
                name="http.request",
                duration_ms=duration_ms,
                trace_id=_current_trace_id(),
                span_attributes={
                    "correlation_id": correlation_id,
                    "http.method": method,
                    "http.status_code": status_code,
                },
                metric_attributes={
                    "http.method": method,
                    "http.status_code": status_code,
                },
            )
        except Exception:  # noqa: BLE001 - record construction is also isolated.
            return

    def emit_model_call(
        self,
        *,
        quest_id: str,
        adapter: str,
        model: str,
        prompt_version: str,
        call_id: str | None,
        attempt_id: str | None,
        outcome: str,
        validation_status: str,
        error_type: str | None,
        idempotent_replay: bool,
        reserved_tokens: int,
        max_output_tokens: int,
        usage: Mapping[str, int] | None,
        duration_ms: int,
    ) -> None:
        try:
            span_attributes: dict[str, _AttributeValue] = {
                "model.quest_id": quest_id,
                "model.adapter": adapter,
                "model.name": model,
                "model.operation": "planning",
                "model.prompt_version": prompt_version,
                "model.outcome": outcome,
                "model.validation_status": validation_status,
                "model.idempotent_replay": idempotent_replay,
                "model.reserved_tokens": reserved_tokens,
                "model.max_output_tokens": max_output_tokens,
            }
            if call_id is not None:
                span_attributes["model.call_id"] = call_id
            if attempt_id is not None:
                span_attributes["model.attempt_id"] = attempt_id
            if error_type is not None:
                span_attributes["error.type"] = error_type
            if usage is not None:
                for source, target in (
                    ("input_tokens", "model.input_tokens"),
                    ("output_tokens", "model.output_tokens"),
                    ("total_tokens", "model.total_tokens"),
                    ("cost_microunits", "model.cost_microunits"),
                ):
                    value = usage.get(source)
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and value >= 0
                    ):
                        span_attributes[target] = value
            metric_attributes = {
                key: value
                for key, value in span_attributes.items()
                if key
                in {
                    "model.adapter",
                    "model.name",
                    "model.operation",
                    "model.outcome",
                    "model.validation_status",
                    "error.type",
                }
            }
            self._emit_pair(
                name="model.call",
                duration_ms=duration_ms,
                trace_id=_current_trace_id(),
                span_attributes=span_attributes,
                metric_attributes=metric_attributes,
            )
        except Exception:  # noqa: BLE001 - record construction is also isolated.
            return

    def _emit_pair(
        self,
        *,
        name: str,
        duration_ms: int,
        trace_id: str,
        span_attributes: Mapping[str, _AttributeValue],
        metric_attributes: Mapping[str, _AttributeValue],
    ) -> None:
        timestamp_ns = time.time_ns()
        self._best_effort(
            TelemetryRecord(
                name=name,
                kind="span",
                timestamp_ns=timestamp_ns,
                duration_ms=duration_ms,
                trace_id=trace_id,
                span_id=uuid.uuid4().hex[:16],
                parent_span_id=None,
                attributes=span_attributes,
            )
        )
        self._best_effort(
            TelemetryRecord(
                name=name,
                kind="metric",
                timestamp_ns=timestamp_ns,
                duration_ms=duration_ms,
                trace_id=trace_id,
                span_id=uuid.uuid4().hex[:16],
                parent_span_id=None,
                attributes=metric_attributes,
            )
        )

    def _best_effort(self, record: TelemetryRecord) -> None:
        try:
            self.emit(record)
        except Exception:  # noqa: BLE001 - telemetry must be isolated.
            return


class NoOpTelemetry(Telemetry):
    """Default implementation: no queue and no thread."""

    def emit(self, record: TelemetryRecord) -> None:
        del record

    @property
    def enabled(self) -> bool:
        return False

    def emit_http_request(
        self, *, correlation_id: str, method: str, status_code: int, duration_ms: int
    ) -> None:
        del correlation_id, method, status_code, duration_ms

    def emit_model_call(self, **_: object) -> None:
        return

    def close(self, timeout_seconds: float | None = None) -> None:
        del timeout_seconds


class InMemoryTelemetrySink:
    """Thread-safe local test sink, not a production collector."""

    def __init__(self) -> None:
        self._records: list[TelemetryRecord] = []
        self._lock = threading.Lock()

    def __call__(self, record: TelemetryRecord) -> None:
        with self._lock:
            self._records.append(record)

    def snapshot(self) -> tuple[TelemetryRecord, ...]:
        with self._lock:
            return tuple(self._records)


class BoundedTelemetry(Telemetry):
    """State-locked local pump; after ``close`` returns its counters are stable."""

    def __init__(
        self,
        exporter: Callable[[TelemetryRecord], None],
        *,
        queue_size: int = 128,
        export_timeout_seconds: float = 0.05,
        sample_every_n: int = 1,
    ) -> None:
        if queue_size < 1 or not 0 < export_timeout_seconds <= 5.0:
            raise ValueError("telemetry capacity and timeout must be positive")
        if not 1 <= sample_every_n <= 10_000:
            raise ValueError("telemetry sample_every_n must be between 1 and 10000")
        self._exporter = exporter
        self._queue: queue.Queue[TelemetryRecord] = queue.Queue(queue_size)
        self._timeout_seconds = export_timeout_seconds
        self._sample_every_n = sample_every_n
        self._state_lock = threading.RLock()
        self._closed = False
        self._finalized = False
        self._circuit_open = False
        self._submitted = self._emitted = self._queued = self._in_flight = 0
        self._exported = self._dropped = self._export_failures = (
            self._export_timeouts
        ) = 0
        self._sampled_out = 0
        self._worker = threading.Thread(
            target=self._pump, name="projecttown-telemetry", daemon=True
        )
        self._worker.start()

    @property
    def circuit_open(self) -> bool:
        with self._state_lock:
            return self._circuit_open

    @property
    def worker_alive(self) -> bool:
        return self._worker.is_alive()

    def counters(self) -> TelemetryCounters:
        with self._state_lock:
            return TelemetryCounters(
                self._submitted,
                self._emitted,
                self._queued,
                self._in_flight,
                self._exported,
                self._dropped,
                self._export_failures,
                self._export_timeouts,
                self._sampled_out,
                self._queue.maxsize,
                self._closed,
                self._circuit_open,
            )

    def emit(self, record: TelemetryRecord) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._submitted += 1
            if not self._is_sampled(record.trace_id):
                self._sampled_out += 1
                return
            if self._circuit_open:
                self._dropped += 1
                return
            try:
                self._queue.put_nowait(record)
            except queue.Full:
                self._dropped += 1
                return
            self._emitted += 1
            self._queued += 1

    def close(self, timeout_seconds: float | None = None) -> None:
        with self._state_lock:
            if self._closed:
                return
            # Closing rejects new records but gives the single worker its
            # bounded join window to export records already accepted.
            self._closed = True
        # The caller may shorten the join but cannot stretch it past the
        # exporter deadline plus a small scheduler grace.
        requested_timeout = (
            self._timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        join_timeout = min(
            5.0,
            self._timeout_seconds + 0.05,
            max(0.0, requested_timeout) if timeout_seconds is not None else 5.0,
        )
        self._worker.join(join_timeout)
        with self._state_lock:
            self._drain_dropped_locked()
            if self._worker.is_alive():
                self._circuit_open = True
                self._export_timeouts += self._in_flight
                self._in_flight = 0
            if self._queued:
                self._dropped += self._queued
                self._queued = 0
            self._finalized = True

    def _pump(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.02)
            except queue.Empty:
                with self._state_lock:
                    if self._closed or self._circuit_open:
                        return
                continue
            with self._state_lock:
                if self._finalized:
                    return
                self._queued -= 1
                if self._circuit_open:
                    self._dropped += 1
                    continue
                self._in_flight += 1
            result = self._export_once(item)
            with self._state_lock:
                if self._finalized:
                    return
                self._in_flight -= 1
                if result == "success":
                    self._exported += 1
                elif result == "failure":
                    self._export_failures += 1
                else:
                    self._export_timeouts += 1
                    self._circuit_open = True
                    self._drain_dropped_locked()
                    return

    def _export_once(
        self, record: TelemetryRecord
    ) -> Literal["success", "failure", "timeout"]:
        done = threading.Event()
        failed = threading.Event()

        def invoke() -> None:
            try:
                self._exporter(record)
            except Exception:  # noqa: BLE001 - exporter failure is isolated.
                failed.set()
            finally:
                done.set()

        helper = threading.Thread(
            target=invoke, name="projecttown-telemetry-export", daemon=True
        )
        helper.start()
        if not done.wait(self._timeout_seconds):
            return "timeout"
        return "failure" if failed.is_set() else "success"

    def _drain_dropped_locked(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            self._dropped += 1
            self._queued -= 1

    def _is_sampled(self, trace_id: str) -> bool:
        return int(trace_id, 16) % self._sample_every_n == 0


def _current_trace_id() -> str:
    return _TRACE_ID.get() or uuid.uuid4().hex


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "BoundedTelemetry",
    "InMemoryTelemetrySink",
    "NoOpTelemetry",
    "Telemetry",
    "TelemetryCounters",
    "TelemetryRecord",
]
