from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.app import controlled_write
from backend.app.controlled_write import (
    AttentionRecord,
    ControlledWriteAttention,
    ControlledWriteError,
    DispatchStarted,
    ExecutionIntent,
    PreflightObservation,
    PreIntentRecovery,
    apply,
    check,
    create_authorization,
    create_restore_authorization,
    parse_authorization_bytes,
    parse_event_bytes,
    parse_receipt_bytes,
    reconcile,
    restore,
    serialize_authorization,
)
from tests.controlled_write_support import ready


def apply_authorized(case: dict[str, object], *, fail_at: str | None = None):
    auth = case["auth"]
    return apply(
        case["root"],
        case["auth_path"],
        case["result"],
        case["proposal_path"],
        case["target"],
        case["plan"],
        case["ledger"],
        Path(auth.backup_path),
        Path(auth.receipt_path),
        fail_at=fail_at,
    )


def reconcile_authorized(case: dict[str, object]):
    auth = case["auth"]
    return reconcile(
        case["root"],
        case["auth_path"],
        case["target"],
        case["ledger"],
        Path(auth.backup_path),
        Path(auth.receipt_path),
    )


def test_authorization_is_canonical_and_binds_exact_preimage(tmp_path: Path) -> None:
    case = ready(tmp_path)
    raw = case["auth_path"].read_bytes()
    assert parse_authorization_bytes(raw) == case["auth"]
    assert serialize_authorization(case["auth"]) == raw
    assert (
        case["auth"].before_sha256
        == hashlib.sha256(case["target"].read_bytes()).hexdigest()
    )
    with pytest.raises(ControlledWriteError):
        parse_authorization_bytes(raw[:-1] + b" ")
    duplicate = raw[:-1] + b',"schema_version":"v3-controlled-write-authorization-v1"}'
    with pytest.raises(ControlledWriteError):
        parse_authorization_bytes(duplicate)


def test_apply_chain_and_check_commit(tmp_path: Path) -> None:
    case = ready(tmp_path)
    before = case["target"].read_bytes()
    receipt = apply_authorized(case)
    auth = case["auth"]
    assert receipt.state == "COMMITTED"
    assert case["target"].read_bytes().startswith(before)
    assert (
        hashlib.sha256(case["target"].read_bytes()).hexdigest()
        == case["proposal"].post_image_sha256
    )
    assert Path(auth.backup_path).read_bytes() == before
    assert parse_receipt_bytes(Path(auth.receipt_path).read_bytes()) == receipt
    events = [
        parse_event_bytes(path.read_bytes())
        for path in sorted((case["ledger"] / auth.operation_id).glob("*.json"))
    ]
    assert [type(event).__name__ for event in events] == [
        "PreflightObservation",
        "BackupManifest",
        "ExecutionIntent",
        "DispatchStarted",
        "PostWriteObservation",
        "WriteReceipt",
    ]
    assert check(case["auth_path"], case["ledger"]) == "COMMITTED"


def test_post_receipt_target_replacement_is_detected(tmp_path: Path) -> None:
    case = ready(tmp_path)
    apply_authorized(case)
    committed = case["target"].read_bytes()
    replacement = case["target"].with_suffix(".replacement")
    replacement.write_bytes(committed)
    replacement.replace(case["target"])
    assert check(case["auth_path"], case["ledger"]) == "TARGET_CHANGED_AFTER_RECEIPT"


@pytest.mark.parametrize(
    ("fail_at", "expected"),
    [("before_replace", "FAILED_NO_EFFECT"), ("after_replace", "COMMITTED")],
)
def test_reconcile_after_interruption_never_redispatch(
    tmp_path: Path, fail_at: str, expected: str
) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply(
            case["root"],
            case["auth_path"],
            case["result"],
            case["proposal_path"],
            case["target"],
            case["plan"],
            case["ledger"],
            Path(auth.backup_path),
            Path(auth.receipt_path),
            fail_at=fail_at,
        )
    receipt = reconcile_authorized(case)
    assert receipt.state == expected
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case)


@pytest.mark.parametrize("mutation", ["target", "proposal", "authorization"])
def test_binding_drift_and_tamper_fail_closed(tmp_path: Path, mutation: str) -> None:
    case = ready(tmp_path)
    if mutation == "target":
        case["target"].write_bytes(b"changed\n")
    elif mutation == "proposal":
        case["proposal_path"].write_bytes(case["proposal_path"].read_bytes() + b" ")
    else:
        case["auth_path"].write_bytes(case["auth_path"].read_bytes() + b" ")
    with pytest.raises((ControlledWriteError, ControlledWriteAttention)):
        apply_authorized(case)


def test_create_only_ledger_and_lock_rejections(tmp_path: Path) -> None:
    case = ready(tmp_path)
    with pytest.raises(ControlledWriteError):
        create_authorization(
            case["root"],
            case["result"],
            case["target"],
            case["plan"],
            case["proposal_path"],
            case["ledger"],
            case["auth_path"],
            "operation-002",
            "b" * 32,
        )
    lock = case["target"].with_name(case["target"].name + ".projecttown.lock")
    lock.write_bytes(b"not-an-authorized-lock")
    with pytest.raises((ControlledWriteError, ControlledWriteAttention)):
        apply_authorized(case)


def test_invalid_authorization_keys_fail_before_operation_directory(
    tmp_path: Path,
) -> None:
    case = ready(tmp_path)
    invalid_operation = "Invalid/Operation"
    with pytest.raises(ControlledWriteError) as raised:
        create_authorization(
            case["root"],
            case["result"],
            case["target"],
            case["plan"],
            case["proposal_path"],
            case["ledger"],
            case["evidence"] / "invalid-auth.json",
            invalid_operation,
            "short",
        )
    assert raised.value.code == "INVALID_AUTHORIZATION_FIELDS"
    assert not (case["ledger"] / invalid_operation).exists()


def test_attention_is_recorded_without_false_effect_claim(tmp_path: Path) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply(
            case["root"],
            case["auth_path"],
            case["result"],
            case["proposal_path"],
            case["target"],
            case["plan"],
            case["ledger"],
            Path(auth.backup_path),
            Path(auth.receipt_path),
            fail_at="backup",
        )
    events = [
        parse_event_bytes(path.read_bytes())
        for path in sorted((case["ledger"] / auth.operation_id).glob("*.json"))
    ]
    attention = [event for event in events if isinstance(event, AttentionRecord)][-1]
    assert attention.target_effect in {
        "unknown",
        "none",
        "effect_present",
        "external_drift",
    }


@pytest.mark.parametrize("effect_occurs", [False, True])
def test_replace_oserror_is_attention_and_reconciles_exact_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effect_occurs: bool
) -> None:
    case = ready(tmp_path)
    before = case["target"].read_bytes()
    real_replace = controlled_write.os.replace

    def ambiguous_replace(source: Path, destination: Path) -> None:
        if effect_occurs:
            real_replace(source, destination)
        raise OSError("injected ambiguous replace")

    monkeypatch.setattr(controlled_write.os, "replace", ambiguous_replace)
    with pytest.raises(ControlledWriteAttention) as raised:
        apply_authorized(case)
    assert raised.value.code == "DISPATCH_OUTCOME_UNKNOWN"
    monkeypatch.setattr(controlled_write.os, "replace", real_replace)
    receipt = reconcile_authorized(case)
    assert receipt.state == ("COMMITTED" if effect_occurs else "FAILED_NO_EFFECT")
    if not effect_occurs:
        assert case["target"].read_bytes() == before


def test_attention_publication_failure_leaves_recoverable_exact_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    original = controlled_write._append_attention

    def fail_attention(*_args, **_kwargs):
        raise ControlledWriteError("EVENT_PUBLICATION_FAILED")

    monkeypatch.setattr(controlled_write, "_append_attention", fail_attention)
    with pytest.raises(ControlledWriteAttention) as raised:
        apply(
            case["root"],
            case["auth_path"],
            case["result"],
            case["proposal_path"],
            case["target"],
            case["plan"],
            case["ledger"],
            Path(auth.backup_path),
            Path(auth.receipt_path),
            fail_at="before_replace",
        )
    assert raised.value.code == "ATTENTION_RECORD_UNAVAILABLE"
    assert Path(auth.lock_path).is_file()
    monkeypatch.setattr(controlled_write, "_append_attention", original)
    assert reconcile_authorized(case).state == "FAILED_NO_EFFECT"


def test_release_failure_never_reports_success_and_terminal_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = ready(tmp_path)
    original = controlled_write._release_lock

    def fail_release(fd, _path, _record, *, owned):
        assert owned
        controlled_write._abandon_lock(fd)
        raise ControlledWriteAttention("LOCK_RELEASE_FAILED")

    monkeypatch.setattr(controlled_write, "_release_lock", fail_release)
    with pytest.raises(ControlledWriteAttention) as raised:
        apply_authorized(case)
    assert raised.value.code == "LOCK_RELEASE_FAILED"
    monkeypatch.setattr(controlled_write, "_release_lock", original)
    assert reconcile_authorized(case).state == "COMMITTED"


def test_target_changed_after_dispatch_record_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = ready(tmp_path)
    original = controlled_write._append_event

    def mutate_after_dispatch(*args, **kwargs):
        event = original(*args, **kwargs)
        if args[3] is DispatchStarted:
            case["target"].write_bytes(b"external writer\n")
        return event

    monkeypatch.setattr(controlled_write, "_append_event", mutate_after_dispatch)
    with pytest.raises(ControlledWriteAttention) as raised:
        apply_authorized(case)
    assert raised.value.code == "EXTERNAL_DRIFT_BLOCKED"
    assert case["target"].read_bytes() == b"external writer\n"
    assert not Path(case["auth"].receipt_path).exists()


def test_explicit_restore_requires_new_authorization(tmp_path: Path) -> None:
    case = ready(tmp_path)
    before = case["target"].read_bytes()
    original = apply_authorized(case)
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = case["evidence"] / "restore-auth.json"
    restore_auth = create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-001",
        "c" * 32,
    )
    receipt = restore(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    assert original.state == receipt.state == "COMMITTED"
    assert case["target"].read_bytes() == before
    assert check(restore_auth_path, restore_ledger) == "COMMITTED"


def test_restore_authorization_rejects_same_bytes_at_a_different_target(
    tmp_path: Path,
) -> None:
    case = ready(tmp_path)
    apply_authorized(case)
    other = case["root"] / "OTHER.md"
    other.write_bytes(case["target"].read_bytes())
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    with pytest.raises(ControlledWriteError):
        create_restore_authorization(
            case["root"],
            Path(case["auth"].receipt_path),
            other,
            restore_ledger,
            case["evidence"] / "restore-auth.json",
            "restore-001",
            "d" * 32,
        )


def test_restore_rechecks_source_backup_after_authorization(tmp_path: Path) -> None:
    case = ready(tmp_path)
    apply_authorized(case)
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = case["evidence"] / "restore-auth.json"
    restore_auth = create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-001",
        "e" * 32,
    )
    Path(restore_auth.source_backup_path).write_bytes(b"tampered backup")
    with pytest.raises(ControlledWriteError) as raised:
        restore(
            case["root"],
            restore_auth_path,
            case["target"],
            restore_ledger,
            Path(restore_auth.backup_path),
            Path(restore_auth.receipt_path),
        )
    assert raised.value.code == "RESTORE_SOURCE_CHANGED"


@pytest.mark.parametrize(
    ("fail_at", "expected"),
    [("before_replace", "FAILED_NO_EFFECT"), ("after_replace", "COMMITTED")],
)
def test_restore_interruption_requires_reconcile_without_redispatch(
    tmp_path: Path, fail_at: str, expected: str
) -> None:
    case = ready(tmp_path)
    original = case["target"].read_bytes()
    apply_authorized(case)
    applied = case["target"].read_bytes()
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = case["evidence"] / "restore-auth.json"
    restore_auth = create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-001",
        "f" * 32,
    )
    with pytest.raises(ControlledWriteAttention):
        restore(
            case["root"],
            restore_auth_path,
            case["target"],
            restore_ledger,
            Path(restore_auth.backup_path),
            Path(restore_auth.receipt_path),
            fail_at=fail_at,
        )
    receipt = reconcile(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    assert receipt.state == expected
    assert case["target"].read_bytes() == (
        original if expected == "COMMITTED" else applied
    )


def test_ledger_tamper_is_rejected(tmp_path: Path) -> None:
    case = ready(tmp_path)
    apply_authorized(case)
    auth = case["auth"]
    dispatch = next((case["ledger"] / auth.operation_id).glob("*dispatch.json"))
    dispatch.write_bytes(dispatch.read_bytes() + b" ")
    with pytest.raises(ControlledWriteError):
        check(case["auth_path"], case["ledger"])


@pytest.mark.parametrize("failure", ["backup", "after_manifest"])
def test_apply_pre_intent_recovery_is_audited_and_never_duplicates_intent(
    tmp_path: Path, failure: str
) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply(
            case["root"],
            case["auth_path"],
            case["result"],
            case["proposal_path"],
            case["target"],
            case["plan"],
            case["ledger"],
            Path(auth.backup_path),
            Path(auth.receipt_path),
            fail_at=failure,
        )
    receipt = apply_authorized(case)
    assert receipt.state == "COMMITTED"
    events = [
        parse_event_bytes(path.read_bytes())
        for path in sorted((case["ledger"] / auth.operation_id).glob("*.json"))
    ]
    assert sum(isinstance(event, PreIntentRecovery) for event in events) == 1
    assert sum(isinstance(event, ExecutionIntent) for event in events) == 1
    assert sum(isinstance(event, DispatchStarted) for event in events) == 1


def test_unmanifested_exact_backup_can_continue_with_same_authorization(
    tmp_path: Path,
) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case, fail_at="backup")
    controlled_write._publish_exact(
        case["root"], Path(auth.backup_path), case["target"].read_bytes(), "TEST"
    )
    assert apply_authorized(case).state == "COMMITTED"
    recovery = next(
        (case["ledger"] / auth.operation_id).glob("*preintent-recovery.json")
    )
    event = parse_event_bytes(recovery.read_bytes())
    assert isinstance(event, PreIntentRecovery)
    assert event.backup_state == "exact-unmanifested"


def test_pre_intent_drift_is_rejected_and_existing_intent_never_resumes(
    tmp_path: Path,
) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case, fail_at="backup")
    case["target"].write_bytes(b"external drift\n")
    with pytest.raises((ControlledWriteError, ControlledWriteAttention)):
        apply_authorized(case)
    assert not list((case["ledger"] / auth.operation_id).glob("*intent.json"))

    intent_root = tmp_path / "intent"
    intent_root.mkdir()
    case = ready(intent_root)
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case, fail_at="before_replace")
    with pytest.raises(ControlledWriteAttention) as raised:
        apply_authorized(case)
    assert raised.value.code == "RECONCILE_REQUIRED"


def test_restore_pre_intent_recovery_preserves_original_visible_mode(
    tmp_path: Path,
) -> None:
    case = ready(tmp_path)
    original_mode = controlled_write._mode(case["target"].stat())
    apply_authorized(case)
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = case["evidence"] / "restore-auth.json"
    restore_auth = create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-002",
        "9" * 32,
    )
    with pytest.raises(ControlledWriteAttention):
        restore(
            case["root"],
            restore_auth_path,
            case["target"],
            restore_ledger,
            Path(restore_auth.backup_path),
            Path(restore_auth.receipt_path),
            fail_at="backup",
        )
    receipt = restore(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    assert receipt.state == "COMMITTED"
    assert controlled_write._mode(case["target"].stat()) == original_mode


def test_stage_mode_mismatch_never_becomes_committed(
    tmp_path: Path, monkeypatch
) -> None:
    case = ready(tmp_path)
    original_stage = controlled_write._stage

    def wrong_mode(target: Path, data: bytes, mode: int):
        staged, metadata = original_stage(target, data, mode)
        staged.chmod(0o444)
        return staged, metadata

    monkeypatch.setattr(controlled_write, "_stage", wrong_mode)
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case)
    auth = case["auth"]
    events = [
        parse_event_bytes(path.read_bytes())
        for path in sorted((case["ledger"] / auth.operation_id).glob("*.json"))
    ]
    assert any(isinstance(event, DispatchStarted) for event in events)
    assert not any(type(event).__name__ == "WriteReceipt" for event in events)


def test_chain_rejects_self_hashed_semantic_preflight_and_intent(
    tmp_path: Path,
) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case, fail_at="before_replace")
    events = [
        parse_event_bytes(path.read_bytes())
        for path in sorted((case["ledger"] / auth.operation_id).glob("*.json"))
    ]
    preflight = next(
        event for event in events if isinstance(event, PreflightObservation)
    )
    preflight_payload = preflight.model_dump()
    preflight_payload.pop("event_hash")
    preflight_payload["observed_size_bytes"] += 1
    bad_preflight = controlled_write._make(PreflightObservation, **preflight_payload)
    with pytest.raises(ControlledWriteError):
        controlled_write._validate_chain([bad_preflight, *events[1:]], auth)

    intent = next(event for event in events if isinstance(event, ExecutionIntent))
    intent_payload = intent.model_dump()
    intent_payload.pop("event_hash")
    intent_payload["intended_size_bytes"] += 1
    bad_intent = controlled_write._make(ExecutionIntent, **intent_payload)
    index = events.index(intent)
    with pytest.raises(ControlledWriteError):
        controlled_write._validate_chain(
            [*events[:index], bad_intent, *events[index + 1 :]], auth
        )


def test_restore_rejects_source_backup_storage_mode_drift(tmp_path: Path) -> None:
    case = ready(tmp_path)
    apply_authorized(case)
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = case["evidence"] / "restore-auth.json"
    restore_auth = create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-003",
        "8" * 32,
    )
    source = Path(restore_auth.source_backup_path)
    before_mode = controlled_write._mode(source.stat())
    source.chmod(0o444)
    if controlled_write._mode(source.stat()) == before_mode:
        pytest.skip("platform did not expose chmod as a Python-visible mode change")
    with pytest.raises(ControlledWriteError) as raised:
        restore(
            case["root"],
            restore_auth_path,
            case["target"],
            restore_ledger,
            Path(restore_auth.backup_path),
            Path(restore_auth.receipt_path),
        )
    assert raised.value.code == "RESTORE_SOURCE_CHANGED"


def test_check_rejects_nonterminal_manifest_backup_mode_drift(tmp_path: Path) -> None:
    case = ready(tmp_path)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply_authorized(case, fail_at="after_manifest")
    backup = Path(auth.backup_path)
    observed_before = controlled_write._mode(backup.stat())
    backup.chmod(0o444)
    if controlled_write._mode(backup.stat()) == observed_before:
        pytest.skip("platform did not expose chmod as a Python-visible mode change")
    with pytest.raises(ControlledWriteError) as raised:
        check(case["auth_path"], case["ledger"])
    assert raised.value.code == "INVALID_BACKUP"
