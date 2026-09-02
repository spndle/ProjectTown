from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import backend.app.v1.gateway as gateway_module
from backend.app.errors import AppError
from backend.app.tools import Sandbox, ToolRegistry, build_default_registry
from backend.app.v1.gateway import FaultInjector, ToolGateway
from backend.app.v1.storage import V1Storage


class Store:
    def __init__(self):
        self.rows = {}

    def prepare_action(
        self,
        action_id,
        quest_id,
        milestone_id,
        idempotency_key,
        tool_name,
        arguments_hash,
        arguments,
        expected_state_version,
        pre_effect_hash=None,
    ):
        key = (quest_id, idempotency_key)
        row = self.rows.get(key)
        if row is not None:
            return row
        row = {
            "action_id": action_id,
            "quest_id": quest_id,
            "tool_name": tool_name,
            "arguments_hash": arguments_hash,
            "arguments": dict(arguments),
            "pre_effect_hash": pre_effect_hash,
            "status": "prepared",
        }
        self.rows[key] = row
        return row

    def _row(self, action_id):
        return next(row for row in self.rows.values() if row["action_id"] == action_id)

    def mark_action_dispatched(self, action_id):
        row = self._row(action_id)
        row["status"] = "dispatched"
        return row

    def commit_action(self, action_id, result):
        row = self._row(action_id)
        row.update(status="committed", result=dict(result), error=None)
        return row

    def commit_action_with_event(self, action_id, result, *, file_observation=None):
        return self.commit_action(action_id, result)

    def fail_action(self, action_id, error):
        row = self._row(action_id)
        row.update(status="failed", error=dict(error))
        return row

    def mark_action_unknown(self, action_id, error=None):
        row = self._row(action_id)
        row.update(status="unknown_effect", error=error)
        return row

    def get_action(self, action_id):
        return self._row(action_id)


def make_gateway(tmp_path, *, faults=None, allowlist=None, high_risk=None):
    sandbox = Sandbox(tmp_path)
    registry = ToolRegistry(sandbox)
    calls = {"count": 0}

    def tool(workspace, args):
        calls["count"] += 1
        return {"ok": True, "value": args.get("value")}

    registry.register("echo", "test", tool)
    registry.register(
        "write_file", "write", lambda ws, args: {"path": args["path"], "created": True}
    )
    return ToolGateway(
        registry,
        Store(),
        allowlist=allowlist,
        high_risk_tools=high_risk,
        read_only_tools={"echo"},
        fault_injector=FaultInjector(faults),
    ), calls


def run(gateway, **kwargs):
    defaults = {
        "action_id": "a1",
        "quest_id": "q1",
        "idempotency_key": "k1",
        "expected_state_version": 0,
        "workspace": "ws",
        "tool_name": "echo",
        "arguments": {"value": 1},
    }
    defaults.update(kwargs)
    return gateway.execute(**defaults)


def test_same_key_executes_once(tmp_path):
    gateway, calls = make_gateway(tmp_path)
    assert run(gateway)["status"] == "committed"
    assert run(gateway)["status"] == "committed"
    assert calls["count"] == 1


def test_missing_atomic_commit_interface_does_not_fall_back_to_plain_commit(tmp_path):
    class MissingAtomicStore(Store):
        commit_action_with_event = None

    sandbox = Sandbox(tmp_path)
    registry = ToolRegistry(sandbox)
    registry.register("echo", "test", lambda _workspace, _args: {"ok": True})
    gateway = ToolGateway(registry, MissingAtomicStore(), read_only_tools={"echo"})
    receipt = run(gateway)
    assert receipt["status"] == "failed"
    assert gateway.store._row("a1")["status"] == "failed"


def test_same_key_different_arguments_conflict(tmp_path):
    gateway, _ = make_gateway(tmp_path)
    run(gateway)
    with pytest.raises(AppError) as exc:
        run(gateway, arguments={"value": 2})
    assert exc.value.status_code == 409


def test_write_reconciles_without_repeat(tmp_path):
    gateway, _ = make_gateway(tmp_path, faults={"after_effect_before_receipt"})
    path = tmp_path / "ws" / "result.txt"
    # Replace the test write implementation with an actual write and count it.
    count = {"n": 0}
    gateway.registry._tools["write_file"] = lambda ws, args: (
        count.__setitem__("n", count["n"] + 1),
        path.parent.mkdir(parents=True, exist_ok=True),
        path.write_text(args["content"]),
        {"path": args["path"], "created": True},
    )[-1]
    first = gateway.execute(
        action_id="w",
        quest_id="q",
        idempotency_key="w",
        expected_state_version=0,
        workspace="ws",
        tool_name="write_file",
        arguments={"path": "result.txt", "content": "ok"},
    )
    second = gateway.execute(
        action_id="w",
        quest_id="q",
        idempotency_key="w",
        expected_state_version=0,
        workspace="ws",
        tool_name="write_file",
        arguments={"path": "result.txt", "content": "ok"},
    )
    assert (
        first["status"] == "unknown_effect"
        and second["status"] == "committed"
        and count["n"] == 1
    )


def test_write_content_conflict_stays_unknown(tmp_path):
    gateway, _ = make_gateway(tmp_path, faults={"after_effect_before_receipt"})
    result = gateway.execute(
        action_id="w",
        quest_id="q",
        idempotency_key="w",
        expected_state_version=0,
        workspace="ws",
        tool_name="write_file",
        arguments={"path": "result.txt", "content": "ok"},
    )
    assert result["status"] == "unknown_effect"
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws" / "result.txt").write_text("different")
    assert (
        gateway.execute(
            action_id="w",
            quest_id="q",
            idempotency_key="w",
            expected_state_version=0,
            workspace="ws",
            tool_name="write_file",
            arguments={"path": "result.txt", "content": "ok"},
        )["status"]
        == "unknown_effect"
    )


def test_malformed_result_fails(tmp_path):
    gateway, _ = make_gateway(tmp_path, faults={"malformed_result"})
    assert run(gateway)["status"] == "failed"


def test_allowlist_and_approval(tmp_path):
    gateway, _ = make_gateway(tmp_path, allowlist={"echo"}, high_risk={"echo"})
    with pytest.raises(AppError):
        run(gateway)
    assert run(gateway, approved=True)["status"] == "committed"
    with pytest.raises(AppError):
        run(
            gateway,
            action_id="x",
            idempotency_key="x",
            tool_name="write_file",
            approved=True,
        )


def _persistent_write_gateway(tmp_path, *, faults=None):
    sandbox = Sandbox(tmp_path / "sandbox")
    storage = V1Storage(tmp_path / "store.db")
    storage.create_draft(
        "q1",
        {"id": "contract-q1", "goal": "write", "version": 1},
        {"id": "plan-q1", "version": 1, "milestones": [{"id": "m1"}]},
        workspace="ws",
    )
    return (
        ToolGateway(
            build_default_registry(sandbox),
            storage,
            fault_injector=FaultInjector(faults),
        ),
        storage,
    )


def _write_request(gateway, *, action_id="write", idempotency_key="write"):
    return gateway.execute(
        action_id=action_id,
        quest_id="q1",
        milestone_id="m1",
        idempotency_key=idempotency_key,
        expected_state_version=1,
        workspace="ws",
        tool_name="write_file",
        arguments={"path": "result.txt", "content": "actual bytes"},
    )


def _observations(storage):
    return storage._conn.execute(
        """
        SELECT action_id, quest_id, committed_event_id, relative_path,
               before_sha256, after_sha256, after_size_bytes, change_kind, status
        FROM v1_tool_file_observations
        ORDER BY observation_id
        """
    ).fetchall()


def test_persistent_write_file_commits_actual_file_observation_atomically(tmp_path):
    gateway, storage = _persistent_write_gateway(tmp_path)
    receipt = _write_request(gateway)
    action = storage.get_action("write")
    observations = _observations(storage)
    assert receipt["status"] == "committed"
    assert action is not None and action["committed_event_id"] is not None
    assert len(observations) == 1
    observation = observations[0]
    assert observation["action_id"] == "write"
    assert observation["quest_id"] == "q1"
    assert observation["committed_event_id"] == action["committed_event_id"]
    assert observation["relative_path"] == "result.txt"
    assert observation["before_sha256"] is None
    assert observation["after_size_bytes"] == len(b"actual bytes")
    assert observation["change_kind"] == "created"
    assert observation["status"] == "observed"


def test_write_unknown_then_successful_reconcile_records_one_observation(tmp_path):
    gateway, storage = _persistent_write_gateway(
        tmp_path, faults={"after_effect_before_receipt"}
    )
    assert _write_request(gateway)["status"] == "unknown_effect"
    assert _observations(storage) == []
    assert _write_request(gateway)["status"] == "committed"
    assert len(_observations(storage)) == 1
    assert _write_request(gateway)["status"] == "committed"
    assert len(_observations(storage)) == 1


def test_write_hash_mismatch_stays_unknown_without_observation(tmp_path):
    gateway, storage = _persistent_write_gateway(
        tmp_path, faults={"after_effect_before_receipt"}
    )
    assert _write_request(gateway)["status"] == "unknown_effect"
    (tmp_path / "sandbox" / "ws" / "result.txt").write_text(
        "different", encoding="utf-8"
    )
    assert _write_request(gateway)["status"] == "unknown_effect"
    assert _observations(storage) == []


def test_write_actual_bytes_mismatch_does_not_commit_or_observe(tmp_path):
    gateway, storage = _persistent_write_gateway(tmp_path)
    path = tmp_path / "sandbox" / "ws" / "result.txt"

    def malicious_write(_workspace, _arguments):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"wrong bytes")
        return {"path": "result.txt", "size_bytes": len(b"wrong bytes")}

    gateway.registry._tools["write_file"] = malicious_write
    assert _write_request(gateway)["status"] == "unknown_effect"
    assert _observations(storage) == []


def test_write_observation_refuses_replaced_file_descriptor_before_read(
    tmp_path, monkeypatch
):
    gateway, storage = _persistent_write_gateway(tmp_path)
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("actual bytes", encoding="utf-8")
    real_open = os.open
    real_read = os.read
    read_calls = {"count": 0}

    def open_replacement(_path, flags):
        return real_open(replacement, flags)

    def record_read(descriptor, size):
        read_calls["count"] += 1
        return real_read(descriptor, size)

    monkeypatch.setattr(gateway_module.os, "open", open_replacement)
    monkeypatch.setattr(gateway_module.os, "read", record_read)

    assert _write_request(gateway)["status"] == "unknown_effect"
    assert read_calls["count"] == 0
    assert storage.get_action("write")["status"] == "unknown_effect"
    assert _observations(storage) == []


def test_write_observation_reads_at_most_expected_bytes_plus_one(tmp_path, monkeypatch):
    gateway, storage = _persistent_write_gateway(tmp_path)
    path = tmp_path / "sandbox" / "ws" / "result.txt"
    expected_size = len(b"actual bytes")
    read_sizes: list[int] = []
    real_read = os.read

    def oversized_write(_workspace, _arguments):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"actual bytes" + (b"x" * 4096))
        return {"path": "result.txt", "size_bytes": path.stat().st_size}

    def record_read(descriptor, size):
        read_sizes.append(size)
        return real_read(descriptor, size)

    gateway.registry._tools["write_file"] = oversized_write
    monkeypatch.setattr(gateway_module.os, "read", record_read)

    assert _write_request(gateway)["status"] == "unknown_effect"
    assert read_sizes == [expected_size + 1]
    assert storage.get_action("write")["status"] == "unknown_effect"
    assert _observations(storage) == []


def test_write_observation_rejects_fd_mtime_ctime_drift_after_read(
    tmp_path, monkeypatch
):
    gateway, storage = _persistent_write_gateway(tmp_path)
    real_fstat = os.fstat
    calls = {"count": 0}

    def drift_second_fstat(descriptor):
        calls["count"] += 1
        metadata = real_fstat(descriptor)
        if calls["count"] % 2:
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns + 1,
            st_ctime_ns=metadata.st_ctime_ns + 1,
            st_ino=metadata.st_ino,
            st_dev=metadata.st_dev,
            st_file_attributes=getattr(metadata, "st_file_attributes", 0),
        )

    monkeypatch.setattr(gateway_module.os, "fstat", drift_second_fstat)

    assert _write_request(gateway)["status"] == "unknown_effect"
    assert calls["count"] == 4
    assert storage.get_action("write")["status"] == "unknown_effect"
    assert _observations(storage) == []


def test_reconcile_second_read_drift_does_not_commit_or_observe(tmp_path, monkeypatch):
    gateway, storage = _persistent_write_gateway(
        tmp_path, faults={"after_effect_before_receipt"}
    )
    assert _write_request(gateway)["status"] == "unknown_effect"
    original = gateway._write_file_observation

    def drift_before_observation(record, workspace, arguments):
        (tmp_path / "sandbox" / "ws" / "result.txt").write_text(
            "drift", encoding="utf-8"
        )
        return original(record, workspace, arguments)

    monkeypatch.setattr(gateway, "_write_file_observation", drift_before_observation)
    assert _write_request(gateway)["status"] == "unknown_effect"
    assert _observations(storage) == []
