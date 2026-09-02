from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app import controlled_write, v3_loopback_service
from backend.app.v3_loopback_records import (
    make_binding,
    make_intent,
    publish_create_only,
    sha256,
)
from backend.app.v3_loopback_service import LoopbackService
from tests.v3_loopback_support import OPERATION_ID, loopback_ready


def test_preexisting_intent_never_redispatches(tmp_path, monkeypatch):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    binding = value["binding"]
    key = "z" * 16
    key_hash = sha256(key.encode("ascii"))
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    digest = sha256(
        v3_loopback_service.canonical_json(
            {
                "api": "v3-loopback-api-v1",
                "action": "apply",
                "binding_hash": binding.binding_hash,
                "authorization_hash": binding.authorization_hash,
                "web_operation_id": OPERATION_ID,
                "confirmation": confirmation,
            }
        )
    )
    intent = make_intent(
        web_operation_id=OPERATION_ID,
        key_sha256=key_hash,
        request_digest=digest,
        action="apply",
        binding_hash=binding.binding_hash,
        authorization_hash=binding.authorization_hash,
    )
    publish_create_only(
        value["work"] / "idempotency",
        service._intent_path(OPERATION_ID, key_hash),
        intent,
    )
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not dispatch")

    monkeypatch.setattr(service, "_dispatch", forbidden)
    status, result = service.mutate(OPERATION_ID, "apply", confirmation, key)
    assert status == 409 and result["code"] == "IDEMPOTENCY_ATTENTION"
    assert not called


def test_result_publication_failure_does_not_redispatch(tmp_path, monkeypatch):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    calls = 0
    original = v3_loopback_service.publish_create_only
    dispatch = service._dispatch

    def fail_result(root, path, record):
        if path.name.endswith(".r.json"):
            raise v3_loopback_service.LoopbackRecordError("PUBLICATION_FAILED")
        return original(root, path, record)

    def counted_dispatch(*args, **kwargs):
        nonlocal calls
        calls += 1
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(v3_loopback_service, "publish_create_only", fail_result)
    monkeypatch.setattr(service, "_dispatch", counted_dispatch)
    status, result = service.mutate(OPERATION_ID, "apply", confirmation, "y" * 16)
    assert status == 409 and result["code"] == "IDEMPOTENCY_ATTENTION"
    assert service.mutate(OPERATION_ID, "apply", confirmation, "y" * 16)[0] == 409
    assert calls == 1
    assert (
        controlled_write.check(value["authorization_path"], value["work"] / "ledger")
        == "COMMITTED"
    )


def test_concurrent_distinct_keys_replace_target_at_most_once(tmp_path, monkeypatch):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    replaces = 0
    original = controlled_write.os.replace

    def counted_replace(*args, **kwargs):
        nonlocal replaces
        replaces += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(controlled_write.os, "replace", counted_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda key: service.mutate(OPERATION_ID, "apply", confirmation, key),
                ("a" * 16, "b" * 16),
            )
        )
    assert replaces == 1
    assert any(result[1]["code"] == "COMMITTED" for result in results)
    assert (
        controlled_write.check(value["authorization_path"], value["work"] / "ledger")
        == "COMMITTED"
    )


def test_attention_can_be_reconciled_with_a_new_idempotent_request(tmp_path):
    value = loopback_ready(tmp_path)
    authorization = value["authorization"]
    with pytest.raises(controlled_write.ControlledWriteAttention):
        controlled_write.apply(
            value["root"],
            value["authorization_path"],
            value["result"],
            value["proposal_path"],
            value["target"],
            value["plan"],
            value["work"] / "ledger",
            Path(authorization.backup_path),
            Path(authorization.receipt_path),
            fail_at="after_replace",
        )
    service = LoopbackService(value["work"])
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["reconcile"]
    status, result = service.mutate(OPERATION_ID, "reconcile", confirmation, "r" * 16)
    assert status == 200
    assert result == {
        "code": "COMMITTED",
        "outcome": "completed",
        "write_performed": False,
    }


def test_independently_authorized_restore_is_available_only_on_restore_binding(
    tmp_path,
):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    apply_confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    assert (
        service.mutate(OPERATION_ID, "apply", apply_confirmation, "p" * 16)[1]["code"]
        == "COMMITTED"
    )
    apply_auth = value["authorization"]
    restore_id = "c" * 64
    restore_auth_path = value["work"] / "restore-authorization.json"
    restore_auth = controlled_write.create_restore_authorization(
        value["root"],
        Path(apply_auth.receipt_path),
        value["target"],
        value["work"] / "ledger",
        restore_auth_path,
        "loopback-restore-001",
        "c" * 32,
    )
    raw = restore_auth_path.read_bytes()
    metadata = value["work"].lstat()
    binding = make_binding(
        web_operation_id=restore_id,
        work_root=str(value["work"]),
        work_root_device=int(metadata.st_dev),
        work_root_inode=int(metadata.st_ino),
        authorization_path=str(restore_auth_path),
        authorization_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        authorization_hash=restore_auth.authorization_hash,
        authorization_schema_version=restore_auth.schema_version,
        controlled_operation_id=restore_auth.operation_id,
        material_root=restore_auth.material_root,
        target_relative_path=restore_auth.target_relative_path,
        target_path_sha256=hashlib.sha256(
            restore_auth.target_path.encode()
        ).hexdigest(),
        target_display=restore_auth.target_relative_path,
        allowed_mutations=("restore", "reconcile"),
    )
    publish_create_only(
        value["work"] / "bindings",
        value["work"] / "bindings" / f"{restore_id}.json",
        binding,
    )
    reloaded = LoopbackService(value["work"])
    assert "apply" not in reloaded.inspect(restore_id)["allowed_mutations"]
    confirmation = reloaded.inspect(restore_id)["confirmations"]["restore"]
    status, result = reloaded.mutate(restore_id, "restore", confirmation, "s" * 16)
    assert status == 200 and result["code"] == "COMMITTED"
    assert value["target"].read_bytes() == b"# Existing\n"


def test_idempotency_capacity_reserves_intent_and_result_slots(tmp_path, monkeypatch):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    directory = value["work"] / "idempotency"
    for index in range(510):
        (directory / f"reserved-{index}.json").write_text("{}", encoding="utf-8")

    status, result = service.mutate(OPERATION_ID, "apply", confirmation, "c" * 16)

    assert status == 200 and result["code"] == "COMMITTED"
    assert service._record_count() == 512
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("capacity rejection must precede dispatch")

    monkeypatch.setattr(service, "_dispatch", forbidden)
    with pytest.raises(
        v3_loopback_service.LoopbackServiceError,
        match="IDEMPOTENCY_CAPACITY_REACHED",
    ) as error:
        service.mutate(
            OPERATION_ID,
            "reconcile",
            confirmation.replace("APPLY", "RECONCILE"),
            "d" * 16,
        )
    assert error.value.status_code == 503
    assert not called
    assert service._record_count() == 512


def test_full_idempotency_directory_allows_existing_key_replay(tmp_path):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    key = "e" * 16
    assert service.mutate(OPERATION_ID, "apply", confirmation, key)[0] == 200
    directory = value["work"] / "idempotency"
    for index in range(510):
        (directory / f"reserved-{index}.json").write_text("{}", encoding="utf-8")
    assert service._record_count() == 512

    status, result = service.mutate(OPERATION_ID, "apply", confirmation, key)

    assert status == 200
    assert result == {
        "code": "COMMITTED",
        "outcome": "completed",
        "write_performed": True,
    }


def test_idempotency_capacity_rejects_when_only_one_slot_remains(tmp_path):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"])
    confirmation = service.inspect(OPERATION_ID)["confirmations"]["apply"]
    directory = value["work"] / "idempotency"
    for index in range(511):
        (directory / f"reserved-{index}.json").write_text("{}", encoding="utf-8")

    with pytest.raises(
        v3_loopback_service.LoopbackServiceError,
        match="IDEMPOTENCY_CAPACITY_REACHED",
    ) as error:
        service.mutate(OPERATION_ID, "apply", confirmation, "f" * 16)

    assert error.value.status_code == 503
    assert service._record_count() == 511
