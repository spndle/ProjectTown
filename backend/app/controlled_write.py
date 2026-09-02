"""Phase 3C canonical controlled-write evidence and cooperative locks."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .executable_proposal import (
    ExecutableProposalError,
    load_executable_proposal,
    verify_executable_proposal,
)
from .material_workflow import (
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    publish_new_file,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

MAX_RECORD = 1_048_576
_H = "^[0-9a-f]{64}$"
_N = "^[0-9a-f]{32,}$"
_O = "^[a-z0-9][a-z0-9-]{2,79}$"


class ControlledWriteError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("controlled write rejected")


class ControlledWriteAttention(ControlledWriteError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Record(_Model):
    operation_id: str = Field(pattern=_O)


class UserAuthorization(_Record):
    schema_version: Literal["v3-controlled-write-authorization-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-authorization/v1"]
    action: Literal["apply-proposal-v1"]
    material_root: str
    target_path: str
    target_relative_path: str
    proposal_path: str
    proposal_hash: str = Field(pattern=_H)
    proposal_bytes_sha256: str = Field(pattern=_H)
    before_sha256: str = Field(pattern=_H)
    before_size_bytes: int = Field(ge=0)
    before_device: int = Field(ge=0)
    before_inode: int = Field(ge=0)
    # This protocol binds only Python-visible permission bits.  ACLs, owner,
    # group, xattrs, ADS and timestamps are intentionally out of scope.
    before_permission_mode: int = Field(ge=0, le=0o7777)
    parent_device: int = Field(ge=0)
    parent_inode: int = Field(ge=0)
    after_sha256: str = Field(pattern=_H)
    after_size_bytes: int = Field(gt=0)
    after_permission_mode: int = Field(ge=0, le=0o7777)
    ledger_root: str
    lock_path: str
    backup_path: str
    receipt_path: str
    result_path: str
    result_bytes_sha256: str = Field(pattern=_H)
    plan_path: str
    plan_bytes_sha256: str = Field(pattern=_H)
    caller: Literal["explicit-local-caller-v1"]
    authorization_semantics: Literal["single-use-until-first-intent-v1"]
    nonce: str = Field(pattern=_N)
    authorization_hash: str = Field(pattern=_H)


class RestoreAuthorization(_Record):
    schema_version: Literal["v3-controlled-write-restore-authorization-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-restore-authorization/v1"]
    action: Literal["restore-backup-v1"]
    material_root: str
    target_path: str
    target_relative_path: str
    current_sha256: str = Field(pattern=_H)
    current_size_bytes: int = Field(ge=0)
    current_device: int = Field(ge=0)
    current_inode: int = Field(ge=0)
    current_permission_mode: int = Field(ge=0, le=0o7777)
    parent_device: int = Field(ge=0)
    parent_inode: int = Field(ge=0)
    original_receipt_path: str
    original_receipt_bytes_sha256: str = Field(pattern=_H)
    original_receipt_hash: str = Field(pattern=_H)
    source_backup_manifest_path: str
    source_backup_manifest_hash: str = Field(pattern=_H)
    source_backup_path: str
    source_backup_sha256: str = Field(pattern=_H)
    source_backup_size_bytes: int = Field(ge=0)
    desired_sha256: str = Field(pattern=_H)
    desired_size_bytes: int = Field(ge=0)
    desired_permission_mode: int = Field(ge=0, le=0o7777)
    ledger_root: str
    lock_path: str
    backup_path: str
    receipt_path: str
    caller: Literal["explicit-local-caller-v1"]
    authorization_semantics: Literal["single-use-until-first-intent-v1"]
    nonce: str = Field(pattern=_N)
    authorization_hash: str = Field(pattern=_H)


class _Event(_Record):
    sequence: int = Field(ge=1)
    previous_event_hash: str = Field(pattern=_H)
    authorization_hash: str = Field(pattern=_H)
    event_hash: str = Field(pattern=_H)


class PreflightObservation(_Event):
    schema_version: Literal["v3-controlled-write-preflight-observation-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-preflight-observation/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    observed_sha256: str = Field(pattern=_H)
    observed_size_bytes: int = Field(ge=0)
    observed_device: int = Field(ge=0)
    observed_inode: int = Field(ge=0)
    observed_permission_mode: int = Field(ge=0, le=0o7777)


class PreIntentRecovery(_Event):
    """Audits an explicit, no-dispatch continuation before first intent."""

    schema_version: Literal["v3-controlled-write-pre-intent-recovery-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-pre-intent-recovery/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    observed_sha256: str = Field(pattern=_H)
    observed_size_bytes: int = Field(ge=0)
    observed_device: int = Field(ge=0)
    observed_inode: int = Field(ge=0)
    observed_permission_mode: int = Field(ge=0, le=0o7777)
    backup_state: Literal["absent", "exact-unmanifested", "exact-manifested"]
    backup_sha256: str | None = Field(default=None, pattern=_H)
    backup_size_bytes: int | None = Field(default=None, ge=0)
    backup_permission_mode: int | None = Field(default=None, ge=0, le=0o7777)


class BackupManifest(_Event):
    schema_version: Literal["v3-controlled-write-backup-manifest-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-backup-manifest/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    backup_path: str
    backup_sha256: str = Field(pattern=_H)
    backup_size_bytes: int = Field(ge=0)
    backup_permission_mode: int = Field(ge=0, le=0o7777)
    source_permission_mode: int = Field(ge=0, le=0o7777)
    permissions_restricted: bool
    fsync_performed: bool

    @property
    def manifest_hash(self) -> str:
        return self.event_hash


class ExecutionIntent(_Event):
    schema_version: Literal["v3-controlled-write-execution-intent-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-execution-intent/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    nonce_sha256: str = Field(pattern=_H)
    intended_sha256: str = Field(pattern=_H)
    intended_size_bytes: int = Field(ge=0)


class DispatchStarted(_Event):
    schema_version: Literal["v3-controlled-write-dispatch-started-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-dispatch-started/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    dispatch_nonce_sha256: str = Field(pattern=_H)
    temporary_sha256: str = Field(pattern=_H)
    temporary_size_bytes: int = Field(ge=0)
    temporary_device: int = Field(ge=0)
    temporary_inode: int = Field(ge=0)
    temporary_permission_mode: int = Field(ge=0, le=0o7777)
    target_device: int = Field(ge=0)
    target_inode: int = Field(ge=0)
    target_permission_mode: int = Field(ge=0, le=0o7777)
    parent_device: int = Field(ge=0)
    parent_inode: int = Field(ge=0)


class PostWriteObservation(_Event):
    schema_version: Literal["v3-controlled-write-post-write-observation-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-post-write-observation/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    observed_sha256: str = Field(pattern=_H)
    observed_size_bytes: int = Field(ge=0)
    observed_device: int = Field(ge=0)
    observed_inode: int = Field(ge=0)
    observed_permission_mode: int = Field(ge=0, le=0o7777)
    expected_permission_mode: int = Field(ge=0, le=0o7777)
    expected_match: bool
    scope_match: bool
    directory_fsync: Literal["completed", "unsupported", "failed"]


class AttentionRecord(_Event):
    schema_version: Literal["v3-controlled-write-attention-record-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-attention-record/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    attention_code: str = Field(pattern="^[A-Z0-9_]{3,80}$")
    target_effect: Literal["unknown", "none", "effect_present", "external_drift"]
    observed_sha256: str | None = Field(default=None, pattern=_H)
    observed_size_bytes: int | None = Field(default=None, ge=0)


class WriteReceipt(_Event):
    schema_version: Literal["v3-controlled-write-receipt-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-receipt/v1"]
    action: Literal["apply-proposal-v1", "restore-backup-v1"]
    target_before_sha256: str = Field(pattern=_H)
    target_after_sha256: str = Field(pattern=_H)
    authorization_path: str
    target_path: str
    target_relative_path: str
    manifest_path: str
    backup_path: str
    backup_sha256: str = Field(pattern=_H)
    state: Literal["COMMITTED", "FAILED_NO_EFFECT"]
    target_final_size_bytes: int = Field(ge=0)
    target_before_size_bytes: int = Field(ge=0)
    target_after_size_bytes: int = Field(ge=0)
    backup_size_bytes: int = Field(ge=0)
    target_before_permission_mode: int = Field(ge=0, le=0o7777)
    target_after_permission_mode: int = Field(ge=0, le=0o7777)
    backup_permission_mode: int = Field(ge=0, le=0o7777)
    target_final_permission_mode: int = Field(ge=0, le=0o7777)
    manifest_event_hash: str = Field(pattern=_H)
    final_observed_sha256: str = Field(pattern=_H)
    final_observation_event_hash: str = Field(pattern=_H)

    @property
    def receipt_hash(self) -> str:
        return self.event_hash


class LockRecord(_Record):
    schema_version: Literal["v3-controlled-write-lock-v1"]
    hash_domain: Literal["projecttown/v3/controlled-write-lock/v1"]
    authorization_hash: str = Field(pattern=_H)
    nonce_sha256: str = Field(pattern=_H)
    target_path: str
    ledger_root: str
    parent_device: int = Field(ge=0)
    parent_inode: int = Field(ge=0)
    lock_hash: str = Field(pattern=_H)


Record = (
    UserAuthorization
    | RestoreAuthorization
    | PreflightObservation
    | PreIntentRecovery
    | BackupManifest
    | ExecutionIntent
    | DispatchStarted
    | PostWriteObservation
    | AttentionRecord
    | WriteReceipt
    | LockRecord
)
_RECORDS = (
    UserAuthorization,
    RestoreAuthorization,
    PreflightObservation,
    PreIntentRecovery,
    BackupManifest,
    ExecutionIntent,
    DispatchStarted,
    PostWriteObservation,
    AttentionRecord,
    WriteReceipt,
    LockRecord,
)
_SCHEMA_MODELS: dict[str, type[_Record]] = {
    "v3-controlled-write-authorization-v1": UserAuthorization,
    "v3-controlled-write-restore-authorization-v1": RestoreAuthorization,
    "v3-controlled-write-preflight-observation-v1": PreflightObservation,
    "v3-controlled-write-pre-intent-recovery-v1": PreIntentRecovery,
    "v3-controlled-write-backup-manifest-v1": BackupManifest,
    "v3-controlled-write-execution-intent-v1": ExecutionIntent,
    "v3-controlled-write-dispatch-started-v1": DispatchStarted,
    "v3-controlled-write-post-write-observation-v1": PostWriteObservation,
    "v3-controlled-write-attention-record-v1": AttentionRecord,
    "v3-controlled-write-receipt-v1": WriteReceipt,
    "v3-controlled-write-lock-v1": LockRecord,
}


def _canonical(x: object) -> bytes:
    return json.dumps(
        x, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(x: bytes) -> str:
    return hashlib.sha256(x).hexdigest()


def _hash(domain: str, payload: object) -> str:
    return _sha(domain.encode() + b"\0" + _canonical(payload))


def _field(x: _Record) -> str:
    return (
        "authorization_hash"
        if isinstance(x, (UserAuthorization, RestoreAuthorization))
        else "lock_hash"
        if isinstance(x, LockRecord)
        else "event_hash"
    )


def serialize_record(x: Record) -> bytes:
    if (
        isinstance(x, _Event)
        and x.sequence == 1
        and x.previous_event_hash != x.authorization_hash
    ):
        raise ControlledWriteError("INVALID_LEDGER")
    p = x.model_dump(mode="json")
    got = p.pop(_field(x))
    if got != _hash(cast(str, p["hash_domain"]), p):
        raise ControlledWriteError("INVALID_RECORD")
    b = _canonical(x.model_dump(mode="json"))
    if len(b) > MAX_RECORD:
        raise ControlledWriteError("RECORD_LIMIT_EXCEEDED")
    return b


def parse_record_bytes(data: bytes) -> Record:
    if not isinstance(data, bytes) or len(data) > MAX_RECORD:
        raise ControlledWriteError("INVALID_RECORD")
    try:

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            d: dict[str, object] = {}
            for k, v in pairs:
                if k in d:
                    raise ValueError("duplicate")
                d[k] = v
            return d

        raw = json.loads(data.decode(), object_pairs_hook=unique)
        if not isinstance(raw, dict):
            raise TypeError()
        model = _SCHEMA_MODELS[cast(str, raw.get("schema_version"))]
        x = model.model_validate(raw)
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        ValidationError,
    ) as e:
        raise ControlledWriteError("INVALID_RECORD") from e
    value = cast(Record, x)
    if data != serialize_record(value):
        raise ControlledWriteError("INVALID_RECORD")
    return value


def serialize_authorization(x: UserAuthorization) -> bytes:
    return serialize_record(x)


def parse_authorization_bytes(x: bytes) -> UserAuthorization:
    v = parse_record_bytes(x)
    if not isinstance(v, UserAuthorization):
        raise ControlledWriteError("INVALID_AUTHORIZATION")
    return v


def serialize_restore_authorization(x: RestoreAuthorization) -> bytes:
    return serialize_record(x)


def parse_restore_authorization_bytes(x: bytes) -> RestoreAuthorization:
    v = parse_record_bytes(x)
    if not isinstance(v, RestoreAuthorization):
        raise ControlledWriteError("INVALID_RESTORE_AUTHORIZATION")
    return v


def serialize_event(x: _Event) -> bytes:
    return serialize_record(x)


def parse_event_bytes(x: bytes) -> _Event:
    v = parse_record_bytes(x)
    if not isinstance(v, _Event):
        raise ControlledWriteError("INVALID_LEDGER")
    return v


def serialize_receipt(x: WriteReceipt) -> bytes:
    return serialize_record(x)


def parse_receipt_bytes(x: bytes) -> WriteReceipt:
    v = parse_record_bytes(x)
    if not isinstance(v, WriteReceipt):
        raise ControlledWriteError("INVALID_RECEIPT")
    return v


def _safe_dir(p: Path, code: str) -> Path:
    try:
        m = p.lstat()
        c = p.resolve(strict=True)
    except OSError as e:
        raise ControlledWriteError(code) from e
    if not p.is_absolute() or c != p or not is_safe_directory(m):
        raise ControlledWriteError(code)
    return p


def _external(root: Path, path: Path, code: str) -> None:
    _safe_dir(root, "INVALID_ROOT")
    try:
        path.relative_to(root)
    except ValueError:
        return
    raise ControlledWriteError(code)


def publish_record(
    root: Path, path: Path, x: Record, code: str = "PUBLICATION_FAILED"
) -> None:
    _external(root, path, code)
    _publish_exact(root, path, serialize_record(x), code)


def _publish_exact(root: Path, path: Path, data: bytes, code: str) -> None:
    """Create one external file and never hide an ambiguous publication."""

    try:
        publish_new_file(root, path, data)
    except PublicationAttentionError as error:
        # The create-only publisher could not prove whether its final name is
        # cleanly committed.  A stable, exact one-link final is sufficient;
        # every other state needs reconciliation by a human/operator.
        try:
            observed, _ = _stable(path, code)
        except ControlledWriteError:
            raise ControlledWriteAttention(f"{code}_ATTENTION") from error
        if observed != data:
            raise ControlledWriteAttention(f"{code}_ATTENTION") from error
    except PublicationRollbackError as error:
        raise ControlledWriteError(code) from error
    except MaterialWorkflowError as error:
        raise ControlledWriteError(code) from error


def _permissions_restricted(metadata: os.stat_result) -> bool:
    # Windows' Python mode bits do not describe the effective ACL.  The
    # create-only publisher opens the file with 0600 and validates one-link,
    # non-reparse ownership; POSIX additionally proves group/world bits absent.
    return os.name == "nt" or stat.S_IMODE(metadata.st_mode) & 0o077 == 0


def _mode(metadata: os.stat_result) -> int:
    """The deliberately narrow, cross-platform observable permission contract."""

    return stat.S_IMODE(metadata.st_mode)


def _current_permission_mode(auth: UserAuthorization | RestoreAuthorization) -> int:
    return (
        auth.before_permission_mode
        if isinstance(auth, UserAuthorization)
        else auth.current_permission_mode
    )


def _desired_permission_mode(auth: UserAuthorization | RestoreAuthorization) -> int:
    return (
        auth.after_permission_mode
        if isinstance(auth, UserAuthorization)
        else auth.desired_permission_mode
    )


def _validate_authorization_keys(operation_id: str, nonce: str) -> None:
    if re.fullmatch(_O, operation_id) is None or re.fullmatch(_N, nonce) is None:
        raise ControlledWriteError("INVALID_AUTHORIZATION_FIELDS")


def _operation_dir(
    root: Path, ledger: Path, operation_id: str, *, create: bool
) -> Path:
    _external(root, ledger, "INVALID_LEDGER_ROOT")
    _safe_dir(ledger, "INVALID_LEDGER_ROOT")
    d = ledger / operation_id
    if d.parent != ledger:
        raise ControlledWriteError("INVALID_LEDGER")
    if create:
        try:
            d.mkdir(mode=0o700)
        except FileExistsError as e:
            raise ControlledWriteError("OPERATION_EXISTS") from e
        except OSError as e:
            raise ControlledWriteError("INVALID_LEDGER") from e
    return _safe_dir(d, "INVALID_LEDGER")


def _fd_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        raise ControlledWriteAttention("LOCK_BUSY") from e


def _fd_unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _verify_lock(path: Path, fd: int, record: LockRecord) -> None:
    try:
        a, b = path.lstat(), os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, MAX_RECORD + 1)
    except OSError as e:
        raise ControlledWriteAttention("INVALID_LOCK") from e
    if (
        not stat.S_ISREG(a.st_mode)
        or is_reparse(a)
        or a.st_nlink != 1
        or (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino)
    ):
        raise ControlledWriteAttention("INVALID_LOCK")
    try:
        parsed = parse_record_bytes(data)
    except ControlledWriteError as error:
        raise ControlledWriteAttention("INVALID_LOCK") from error
    if parsed != record:
        raise ControlledWriteAttention("INVALID_LOCK")


def _acquire_lock(path: Path, record: LockRecord, *, recover_existing: bool) -> int:
    parent = _safe_dir(path.parent, "INVALID_LOCK_PATH").lstat()
    if (int(parent.st_dev), int(parent.st_ino)) != (
        record.parent_device,
        record.parent_inode,
    ):
        raise ControlledWriteAttention("LOCK_PARENT_CHANGED")
    fd: int | None = None
    created = False
    locked = False
    acquired = False
    try:
        try:
            fd = os.open(
                path,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            created = True
        except FileExistsError:
            if not recover_existing:
                raise ControlledWriteAttention("LOCK_BUSY")
            fd = os.open(
                path,
                os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        _fd_lock(fd)
        locked = True
        if created:
            payload = serialize_record(record)
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError("short lock write")
                offset += written
            os.fsync(fd)
        _verify_lock(path, fd, record)
        acquired = True
        return fd
    except ControlledWriteAttention:
        raise
    except (ControlledWriteError, OSError) as error:
        raise ControlledWriteAttention("LOCK_UNAVAILABLE") from error
    finally:
        if fd is not None and not acquired:
            # This branch is reached only when acquisition failed.  A newly
            # created partial lock is removed only after proving its identity.
            try:
                identity = os.fstat(fd)
            except OSError:
                identity = None
            if locked:
                try:
                    _fd_unlock(fd)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
            if created and identity is not None:
                try:
                    current = path.lstat()
                    if (current.st_dev, current.st_ino) == (
                        identity.st_dev,
                        identity.st_ino,
                    ):
                        path.unlink()
                except OSError:
                    pass


def _release_lock(fd: int, path: Path, record: LockRecord, *, owned: bool) -> None:
    if not owned:
        return
    closed = False
    try:
        _verify_lock(path, fd, record)
        identity = os.fstat(fd)
        if os.name != "nt":
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise OSError("lock identity changed")
            path.unlink()
            _fd_unlock(fd)
            os.close(fd)
            closed = True
            return
        # Windows does not permit unlinking an open file.  Keep the advisory
        # lock through verification, then close and immediately recheck the
        # exact inode before deleting the cooperative lock name.
        _fd_unlock(fd)
        os.close(fd)
        closed = True
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise OSError("lock identity changed")
        path.unlink()
    except (ControlledWriteError, ControlledWriteAttention, OSError) as e:
        raise ControlledWriteAttention("LOCK_RELEASE_FAILED") from e
    finally:
        if not closed:
            try:
                _fd_unlock(fd)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


def _make(model: type[_Record], **payload: object) -> _Record:
    """Construct one self-authenticating canonical record."""
    field = (
        "authorization_hash"
        if model in (UserAuthorization, RestoreAuthorization)
        else ("lock_hash" if model is LockRecord else "event_hash")
    )
    payload[field] = _hash(cast(str, payload["hash_domain"]), payload)
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise ControlledWriteError("INVALID_RECORD") from error


def _stable(path: Path, code: str) -> tuple[bytes, os.stat_result]:
    try:
        meta = path.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise ControlledWriteError(code) from error
    if (
        canonical != path
        or not stat.S_ISREG(meta.st_mode)
        or is_reparse(meta)
        or meta.st_nlink != 1
    ):
        raise ControlledWriteError(code)
    stable = read_stable_regular_file(
        path, meta, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise ControlledWriteError("UNSTABLE_TARGET")
    _digest, size, data = stable
    if size != meta.st_size:
        raise ControlledWriteError("UNSTABLE_TARGET")
    return data, meta


def _record_data(path: Path, code: str) -> tuple[bytes, os.stat_result]:
    try:
        if path.lstat().st_size > MAX_RECORD:
            raise ControlledWriteError(code)
    except OSError as error:
        raise ControlledWriteError(code) from error
    return _stable(path, code)


def load_record(path: Path) -> Record:
    """Load one canonical, stable, ordinary controlled-write record."""

    return parse_record_bytes(_record_data(path, "INVALID_RECORD")[0])


def _target(
    root: Path, target: Path, relative: str
) -> tuple[bytes, os.stat_result, os.stat_result]:
    _safe_dir(root, "INVALID_ROOT")
    parts = relative.split("/")
    try:
        actual_relative = target.relative_to(root).as_posix()
    except ValueError as error:
        raise ControlledWriteError("TARGET_PATH_MISMATCH") from error
    if (
        not target.is_absolute()
        or not relative
        or any(part in ("", ".", "..") for part in parts)
        or actual_relative != relative
    ):
        raise ControlledWriteError("TARGET_PATH_MISMATCH")
    expected = root.joinpath(*parts)
    try:
        parent = target.parent.lstat()
        wanted, actual = expected.resolve(strict=True), target.resolve(strict=True)
    except OSError as error:
        raise ControlledWriteError("TARGET_UNAVAILABLE") from error
    if target != actual or actual != wanted or not is_safe_directory(parent):
        raise ControlledWriteError("TARGET_PATH_MISMATCH")
    data, meta = _stable(target, "INVALID_TARGET_PATH")
    return data, meta, parent


def _load_auth(root: Path, path: Path) -> UserAuthorization | RestoreAuthorization:
    _external(root, path, "INVALID_AUTHORIZATION_PATH")
    raw, _ = _record_data(path, "INVALID_AUTHORIZATION")
    value = parse_record_bytes(raw)
    if not isinstance(value, (UserAuthorization, RestoreAuthorization)):
        raise ControlledWriteError("INVALID_AUTHORIZATION")
    return value


def _auth_lock(auth: UserAuthorization | RestoreAuthorization) -> LockRecord:
    return cast(
        LockRecord,
        _make(
            LockRecord,
            schema_version="v3-controlled-write-lock-v1",
            hash_domain="projecttown/v3/controlled-write-lock/v1",
            operation_id=auth.operation_id,
            authorization_hash=auth.authorization_hash,
            nonce_sha256=_sha(auth.nonce.encode()),
            target_path=auth.target_path,
            ledger_root=auth.ledger_root,
            parent_device=auth.parent_device,
            parent_inode=auth.parent_inode,
        ),
    )


def _event_path(directory: Path, sequence: int, kind: str) -> Path:
    return directory / f"{sequence:04d}-{kind}.json"


def _read_events(
    root: Path, directory: Path, auth: UserAuthorization | RestoreAuthorization
) -> list[_Event]:
    try:
        entries = list(directory.iterdir())
    except OSError as error:
        raise ControlledWriteError("INVALID_LEDGER") from error
    expected_backup = Path(auth.backup_path)
    events: list[_Event] = []
    for entry in entries:
        if entry == expected_backup:
            _stable(entry, "INVALID_BACKUP")
            continue
        if entry.name == "receipt.json":
            if entry != Path(auth.receipt_path):
                raise ControlledWriteError("INVALID_LEDGER")
        elif not entry.name.endswith(".json"):
            raise ControlledWriteError("INVALID_LEDGER")
        raw, _ = _record_data(entry, "INVALID_LEDGER")
        event = parse_event_bytes(raw)
        expected = (
            Path(auth.receipt_path)
            if isinstance(event, WriteReceipt)
            else _event_path(directory, event.sequence, _event_kind(event))
        )
        if entry != expected:
            raise ControlledWriteError("INVALID_LEDGER")
        events.append(event)
    events.sort(key=lambda item: item.sequence)
    _validate_chain(events, auth)
    return events


def _validate_chain(
    events: list[_Event], auth: UserAuthorization | RestoreAuthorization
) -> None:
    previous = auth.authorization_hash
    preflight: PreflightObservation | None = None
    manifest: BackupManifest | None = None
    intent: ExecutionIntent | None = None
    dispatch: DispatchStarted | None = None
    latest_post: PostWriteObservation | None = None
    attention_seen = False
    terminal = False
    for number, event in enumerate(events, 1):
        if (
            event.sequence != number
            or event.operation_id != auth.operation_id
            or event.authorization_hash != auth.authorization_hash
            or event.previous_event_hash != previous
            or event.action != auth.action
            or terminal
        ):
            raise ControlledWriteError("INVALID_LEDGER")
        if isinstance(event, PreflightObservation):
            if preflight is not None or number != 1:
                raise ControlledWriteError("INVALID_LEDGER")
            if (
                event.observed_sha256,
                event.observed_size_bytes,
                event.observed_device,
                event.observed_inode,
                event.observed_permission_mode,
            ) != _authorized_current(auth):
                raise ControlledWriteError("INVALID_LEDGER")
            preflight = event
        elif isinstance(event, PreIntentRecovery):
            # A recovery is an auditable reset of the pre-intent barrier.  It
            # can follow an attention record, but never an intent or any
            # dispatch/post/terminal evidence.
            if (
                preflight is None
                or intent is not None
                or dispatch is not None
                or latest_post is not None
                or (
                    event.observed_sha256,
                    event.observed_size_bytes,
                    event.observed_device,
                    event.observed_inode,
                    event.observed_permission_mode,
                )
                != _authorized_current(auth)
            ):
                raise ControlledWriteError("INVALID_LEDGER")
            backup_values = (
                event.backup_sha256,
                event.backup_size_bytes,
                event.backup_permission_mode,
            )
            if event.backup_state == "absent":
                if manifest is not None or backup_values != (None, None, None):
                    raise ControlledWriteError("INVALID_LEDGER")
            elif event.backup_state == "exact-unmanifested":
                if (
                    manifest is not None
                    or backup_values[0] != _authorized_current(auth)[0]
                    or backup_values[1] != _authorized_current(auth)[1]
                    or backup_values[2] is None
                ):
                    raise ControlledWriteError("INVALID_LEDGER")
            elif event.backup_state == "exact-manifested":
                if manifest is None or backup_values != (
                    manifest.backup_sha256,
                    manifest.backup_size_bytes,
                    manifest.backup_permission_mode,
                ):
                    raise ControlledWriteError("INVALID_LEDGER")
            else:
                raise ControlledWriteError("INVALID_LEDGER")
            attention_seen = False
        elif isinstance(event, BackupManifest):
            if preflight is None or manifest is not None or attention_seen:
                raise ControlledWriteError("INVALID_LEDGER")
            if event.source_permission_mode != _current_permission_mode(auth):
                raise ControlledWriteError("INVALID_LEDGER")
            manifest = event
        elif isinstance(event, ExecutionIntent):
            if manifest is None or intent is not None or attention_seen:
                raise ControlledWriteError("INVALID_LEDGER")
            if event.nonce_sha256 != _sha(auth.nonce.encode()) or (
                event.intended_sha256,
                event.intended_size_bytes,
            ) != _expected_target(auth):
                raise ControlledWriteError("INVALID_LEDGER")
            intent = event
        elif isinstance(event, DispatchStarted):
            if (
                intent is None
                or dispatch is not None
                or attention_seen
                or latest_post is not None
                or event.temporary_permission_mode != _desired_permission_mode(auth)
                or event.target_permission_mode != _current_permission_mode(auth)
                or (event.temporary_sha256, event.temporary_size_bytes)
                != _expected_target(auth)
                or (event.target_device, event.target_inode)
                != _authorized_current(auth)[2:4]
                or (event.parent_device, event.parent_inode)
                != (auth.parent_device, auth.parent_inode)
            ):
                raise ControlledWriteError("INVALID_LEDGER")
            dispatch = event
        elif isinstance(event, AttentionRecord):
            if preflight is None:
                raise ControlledWriteError("INVALID_LEDGER")
            attention_seen = True
        elif isinstance(event, PostWriteObservation):
            if intent is None:
                raise ControlledWriteError("INVALID_LEDGER")
            if event.expected_permission_mode != _desired_permission_mode(auth):
                raise ControlledWriteError("INVALID_LEDGER")
            expected_identity = (
                (_expected_target(auth), _desired_permission_mode(auth))
                if event.expected_match
                else (_authorized_current(auth)[:2], _current_permission_mode(auth))
            )
            if (
                (event.observed_sha256, event.observed_size_bytes),
                event.observed_permission_mode,
            ) != expected_identity:
                raise ControlledWriteError("INVALID_LEDGER")
            if (
                dispatch is None
                and not attention_seen
                and (
                    event.expected_match
                    or manifest is None
                    or event.observed_sha256 != manifest.backup_sha256
                    or event.observed_size_bytes != manifest.backup_size_bytes
                )
            ):
                raise ControlledWriteError("INVALID_LEDGER")
            latest_post = event
        elif isinstance(event, WriteReceipt):
            if manifest is None or intent is None or latest_post is None:
                raise ControlledWriteError("INVALID_LEDGER")
            desired_hash = (
                auth.after_sha256
                if isinstance(auth, UserAuthorization)
                else auth.desired_sha256
            )
            desired_size = (
                auth.after_size_bytes
                if isinstance(auth, UserAuthorization)
                else auth.desired_size_bytes
            )
            if (
                event.manifest_event_hash != manifest.event_hash
                or event.final_observation_event_hash != latest_post.event_hash
                or event.target_before_sha256 != manifest.backup_sha256
                or event.target_before_size_bytes != manifest.backup_size_bytes
                or event.target_after_sha256 != desired_hash
                or event.target_after_size_bytes != desired_size
                or event.final_observed_sha256 != latest_post.observed_sha256
                or event.target_final_size_bytes != latest_post.observed_size_bytes
                or event.backup_sha256 != manifest.backup_sha256
                or event.backup_size_bytes != manifest.backup_size_bytes
                or event.target_before_permission_mode != _current_permission_mode(auth)
                or event.target_after_permission_mode != _desired_permission_mode(auth)
                or event.backup_permission_mode != manifest.backup_permission_mode
                or event.target_final_permission_mode
                != latest_post.observed_permission_mode
            ):
                raise ControlledWriteError("INVALID_LEDGER")
            if event.state == "COMMITTED":
                if (
                    dispatch is None
                    or not latest_post.expected_match
                    or latest_post.observed_sha256 != desired_hash
                    or latest_post.observed_size_bytes != desired_size
                    or latest_post.observed_permission_mode
                    != _desired_permission_mode(auth)
                ):
                    raise ControlledWriteError("INVALID_LEDGER")
            elif (
                latest_post.expected_match
                or latest_post.observed_sha256 != manifest.backup_sha256
                or latest_post.observed_size_bytes != manifest.backup_size_bytes
                or latest_post.observed_permission_mode
                != _current_permission_mode(auth)
            ):
                raise ControlledWriteError("INVALID_LEDGER")
            terminal = True
        else:
            raise ControlledWriteError("INVALID_LEDGER")
        previous = event.event_hash


def _event_kind(event: _Event) -> str:
    return {
        PreflightObservation: "preflight",
        PreIntentRecovery: "preintent-recovery",
        BackupManifest: "backup",
        ExecutionIntent: "intent",
        DispatchStarted: "dispatch",
        PostWriteObservation: "post",
        AttentionRecord: "attention",
    }.get(type(event), "receipt")


def _append_event(
    root: Path,
    directory: Path,
    auth: UserAuthorization | RestoreAuthorization,
    model: type[_Event],
    kind: str,
    **values: object,
) -> _Event:
    events = _read_events(root, directory, auth)
    payload = dict(values)
    payload.update(
        {
            "operation_id": auth.operation_id,
            "sequence": len(events) + 1,
            "previous_event_hash": auth.authorization_hash
            if not events
            else events[-1].event_hash,
            "authorization_hash": auth.authorization_hash,
        }
    )
    event = cast(_Event, _make(model, **payload))
    _validate_chain([*events, event], auth)
    path = (
        Path(auth.receipt_path)
        if model is WriteReceipt
        else _event_path(directory, event.sequence, kind)
    )
    _publish_exact(root, path, serialize_event(event), "EVENT_PUBLICATION_FAILED")
    confirmed = _read_events(root, directory, auth)
    if not confirmed or confirmed[-1] != event:
        raise ControlledWriteAttention("EVENT_PUBLICATION_ATTENTION")
    return event


def _fsync_dir(path: Path) -> Literal["completed", "unsupported", "failed"]:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return "completed"
    except OSError as error:
        return (
            "unsupported"
            if os.name == "nt" or getattr(error, "errno", None) in (22, 95)
            else "failed"
        )


def _stage(target: Path, data: bytes, mode: int) -> tuple[Path, os.stat_result]:
    parent = target.parent
    temp = parent / f".projecttown-controlled-{secrets.token_hex(16)}.tmp"
    try:
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            stat.S_IMODE(mode),
        )
        try:
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("short stage write")
                offset += written
            os.fsync(fd)
            identity = os.fstat(fd)
        finally:
            os.close(fd)
        os.chmod(temp, stat.S_IMODE(mode))
        reread, check = _stable(temp, "STAGE_VERIFY_FAILED")
    except OSError as error:
        raise ControlledWriteAttention("STAGE_FAILED") from error
    if reread != data or (check.st_dev, check.st_ino) != (
        identity.st_dev,
        identity.st_ino,
    ):
        raise ControlledWriteAttention("STAGE_VERIFY_FAILED")
    return temp, check


def _remove_owned_temp(path: Path, identity: os.stat_result) -> None:
    try:
        current = path.lstat()
        if (
            stat.S_ISREG(current.st_mode)
            and not is_reparse(current)
            and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
        ):
            path.unlink()
    except OSError:
        pass


def _expected_target(
    auth: UserAuthorization | RestoreAuthorization,
) -> tuple[str, int]:
    if isinstance(auth, UserAuthorization):
        return auth.after_sha256, auth.after_size_bytes
    return auth.desired_sha256, auth.desired_size_bytes


def _authorized_current(
    auth: UserAuthorization | RestoreAuthorization,
) -> tuple[str, int, int, int, int]:
    if isinstance(auth, UserAuthorization):
        return (
            auth.before_sha256,
            auth.before_size_bytes,
            auth.before_device,
            auth.before_inode,
            auth.before_permission_mode,
        )
    return (
        auth.current_sha256,
        auth.current_size_bytes,
        auth.current_device,
        auth.current_inode,
        auth.current_permission_mode,
    )


def _classify_target_effect(
    root: Path,
    target: Path,
    auth: UserAuthorization | RestoreAuthorization,
) -> tuple[
    Literal["unknown", "none", "effect_present", "external_drift"],
    bytes | None,
    os.stat_result | None,
]:
    try:
        data, metadata, parent = _target(root, target, auth.target_relative_path)
    except ControlledWriteError:
        return "unknown", None, None
    if (int(parent.st_dev), int(parent.st_ino)) != (
        auth.parent_device,
        auth.parent_inode,
    ):
        return "external_drift", data, metadata
    observed = (_sha(data), len(data))
    if observed == _expected_target(auth) and _mode(
        metadata
    ) == _desired_permission_mode(auth):
        return "effect_present", data, metadata
    if observed == _authorized_current(auth)[:2] and _mode(
        metadata
    ) == _current_permission_mode(auth):
        return "none", data, metadata
    return "external_drift", data, metadata


def _append_attention(
    root: Path,
    directory: Path,
    auth: UserAuthorization | RestoreAuthorization,
    target: Path,
    code: str,
) -> AttentionRecord:
    effect, observed, _ = _classify_target_effect(root, target, auth)
    return cast(
        AttentionRecord,
        _append_event(
            root,
            directory,
            auth,
            AttentionRecord,
            "attention",
            schema_version="v3-controlled-write-attention-record-v1",
            hash_domain="projecttown/v3/controlled-write-attention-record/v1",
            action=auth.action,
            attention_code=code,
            target_effect=effect,
            observed_sha256=_sha(observed) if observed is not None else None,
            observed_size_bytes=len(observed) if observed is not None else None,
        ),
    )


def _abandon_lock(fd: int) -> None:
    """Close an advisory lock but retain its exact on-disk recovery record."""

    try:
        _fd_unlock(fd)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _handle_operation_failure(
    *,
    root: Path,
    directory: Path,
    auth: UserAuthorization | RestoreAuthorization,
    target: Path,
    fd: int,
    lock: LockRecord,
    error: BaseException,
    temp: tuple[Path, os.stat_result] | None,
) -> NoReturn:
    if temp is not None:
        _remove_owned_temp(*temp)
    try:
        events = _read_events(root, directory, auth)
    except ControlledWriteError as ledger_error:
        _abandon_lock(fd)
        raise ControlledWriteAttention("LEDGER_UNAVAILABLE") from ledger_error

    if isinstance(error, ControlledWriteAttention):
        code = error.code
    elif any(isinstance(item, DispatchStarted) for item in events):
        code = "DISPATCH_OUTCOME_UNKNOWN"
    elif events:
        code = "EXECUTION_ATTENTION"
    else:
        code = "EXECUTION_REJECTED"

    if (
        events
        and not isinstance(events[-1], WriteReceipt)
        and not (
            isinstance(events[-1], AttentionRecord)
            and events[-1].attention_code == code
        )
    ):
        try:
            _append_attention(root, directory, auth, target, code)
        except (ControlledWriteError, OSError) as attention_error:
            _abandon_lock(fd)
            raise ControlledWriteAttention(
                "ATTENTION_RECORD_UNAVAILABLE"
            ) from attention_error
    _release_lock(fd, Path(auth.lock_path), lock, owned=True)

    if (
        not events
        and isinstance(error, ControlledWriteError)
        and not isinstance(error, ControlledWriteAttention)
    ):
        raise error
    if not events and isinstance(error, OSError):
        raise ControlledWriteError("EXECUTION_REJECTED") from error
    raise ControlledWriteAttention(code) from error


def _validate_immediate_replace_state(
    *,
    root: Path,
    target: Path,
    auth: UserAuthorization | RestoreAuthorization,
    before: bytes,
    before_metadata: os.stat_result,
    staged: Path,
    staged_metadata: os.stat_result,
    desired: bytes,
) -> None:
    staged_data, staged_live = _stable(staged, "STAGE_VERIFY_FAILED")
    live, target_live, parent_live = _target(root, target, auth.target_relative_path)
    if (
        staged_data != desired
        or (staged_live.st_dev, staged_live.st_ino)
        != (staged_metadata.st_dev, staged_metadata.st_ino)
        or _mode(staged_live) != _desired_permission_mode(auth)
        or live != before
        or (target_live.st_dev, target_live.st_ino)
        != (before_metadata.st_dev, before_metadata.st_ino)
        or _mode(target_live) != _current_permission_mode(auth)
        or (int(parent_live.st_dev), int(parent_live.st_ino))
        != (auth.parent_device, auth.parent_inode)
    ):
        raise ControlledWriteAttention("EXTERNAL_DRIFT_BLOCKED")


def create_authorization(
    root: Path,
    result_path: Path,
    target: Path,
    plan_path: Path,
    proposal_path: Path,
    ledger_root: Path,
    authorization_out: Path,
    operation_id: str,
    nonce: str,
) -> UserAuthorization:
    _validate_authorization_keys(operation_id, nonce)
    try:
        proposal = load_executable_proposal(proposal_path, material_root=root)
        if not verify_executable_proposal(
            root, proposal, result_path, target, plan_path
        ):
            raise ControlledWriteError("PREFLIGHT_BLOCKED")
    except ExecutableProposalError as error:
        raise ControlledWriteError(error.code) from error
    before, meta, parent = _target(root, target, proposal.target_relative_path)
    if authorization_out.parent == ledger_root / operation_id:
        raise ControlledWriteError("INVALID_AUTHORIZATION_PATH")
    directory = _operation_dir(root, ledger_root, operation_id, create=True)
    backup, receipt, lock = (
        directory / "target-before.bin",
        directory / "receipt.json",
        target.with_name(target.name + ".projecttown.lock"),
    )
    value = cast(
        UserAuthorization,
        _make(
            UserAuthorization,
            schema_version="v3-controlled-write-authorization-v1",
            hash_domain="projecttown/v3/controlled-write-authorization/v1",
            action="apply-proposal-v1",
            operation_id=operation_id,
            material_root=str(root),
            target_path=str(target),
            target_relative_path=proposal.target_relative_path,
            proposal_path=str(proposal_path),
            proposal_hash=proposal.proposal_hash,
            proposal_bytes_sha256=_sha(_stable(proposal_path, "INVALID_PROPOSAL")[0]),
            before_sha256=_sha(before),
            before_size_bytes=len(before),
            before_device=int(meta.st_dev),
            before_inode=int(meta.st_ino),
            before_permission_mode=_mode(meta),
            parent_device=int(parent.st_dev),
            parent_inode=int(parent.st_ino),
            after_sha256=proposal.post_image_sha256,
            after_size_bytes=proposal.post_image_size_bytes,
            after_permission_mode=_mode(meta),
            ledger_root=str(ledger_root),
            lock_path=str(lock),
            backup_path=str(backup),
            receipt_path=str(receipt),
            result_path=str(result_path),
            result_bytes_sha256=_sha(_stable(result_path, "INVALID_RESULT")[0]),
            plan_path=str(plan_path),
            plan_bytes_sha256=_sha(_stable(plan_path, "INVALID_PLAN")[0]),
            caller="explicit-local-caller-v1",
            authorization_semantics="single-use-until-first-intent-v1",
            nonce=nonce,
        ),
    )
    publish_record(root, authorization_out, value)
    return value


def _verify_apply_inputs(
    root: Path,
    auth: UserAuthorization,
    result: Path,
    proposal_path: Path,
    target: Path,
    plan: Path,
) -> tuple[bytes, os.stat_result, bytes]:
    if str(root) != auth.material_root or (
        str(result),
        str(proposal_path),
        str(target),
        str(plan),
    ) != (
        auth.result_path,
        auth.proposal_path,
        auth.target_path,
        auth.plan_path,
    ):
        raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH")
    try:
        proposal = load_executable_proposal(proposal_path, material_root=root)
        if not verify_executable_proposal(root, proposal, result, target, plan):
            raise ControlledWriteError("PREFLIGHT_BLOCKED")
    except ExecutableProposalError as error:
        raise ControlledWriteError(error.code) from error
    if (
        _sha(_stable(result, "INVALID_RESULT")[0]) != auth.result_bytes_sha256
        or _sha(_stable(proposal_path, "INVALID_PROPOSAL")[0])
        != auth.proposal_bytes_sha256
        or _sha(_stable(plan, "INVALID_PLAN")[0]) != auth.plan_bytes_sha256
        or proposal.proposal_hash != auth.proposal_hash
        or proposal.post_image_sha256 != auth.after_sha256
        or proposal.post_image_size_bytes != auth.after_size_bytes
    ):
        raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH")
    before, meta, parent = _target(root, target, auth.target_relative_path)
    if (
        _sha(before),
        len(before),
        int(meta.st_dev),
        int(meta.st_ino),
        _mode(meta),
        int(parent.st_dev),
        int(parent.st_ino),
    ) != (
        auth.before_sha256,
        auth.before_size_bytes,
        auth.before_device,
        auth.before_inode,
        auth.before_permission_mode,
        auth.parent_device,
        auth.parent_inode,
    ):
        raise ControlledWriteError("TARGET_BINDING_CHANGED")
    try:
        desired = base64.b64decode(proposal.post_image_base64, validate=True)
    except ValueError as error:
        raise ControlledWriteError("INVALID_PROPOSAL") from error
    if (_sha(desired), len(desired)) != (auth.after_sha256, auth.after_size_bytes):
        raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH")
    return before, meta, desired


def _check_paths(
    root: Path,
    auth: UserAuthorization | RestoreAuthorization,
    ledger: Path,
    backup: Path,
    receipt: Path,
) -> Path:
    if (
        str(ledger) != auth.ledger_root
        or str(backup) != auth.backup_path
        or str(receipt) != auth.receipt_path
    ):
        raise ControlledWriteError("OPERATION_PATH_MISMATCH")
    directory = _operation_dir(root, ledger, auth.operation_id, create=False)
    expected_lock = Path(auth.target_path).with_name(
        Path(auth.target_path).name + ".projecttown.lock"
    )
    if (
        backup.parent != directory
        or receipt.parent != directory
        or Path(auth.lock_path) != expected_lock
        or str(root) != auth.material_root
    ):
        raise ControlledWriteError("OPERATION_PATH_MISMATCH")
    return directory


def _backup_state(
    backup_path: Path, before: bytes
) -> tuple[
    Literal["absent", "exact-unmanifested", "exact-manifested"],
    bytes | None,
    os.stat_result | None,
]:
    """Observe a create-only backup without ever replacing it."""

    if not backup_path.exists():
        return "absent", None, None
    data, metadata = _stable(backup_path, "INVALID_BACKUP")
    if data != before or not _permissions_restricted(metadata):
        raise ControlledWriteAttention("BACKUP_VERIFY_FAILED")
    return "exact-unmanifested", data, metadata


def _append_pre_intent_recovery(
    root: Path,
    directory: Path,
    auth: UserAuthorization | RestoreAuthorization,
    before: bytes,
    metadata: os.stat_result,
    backup_path: Path,
) -> None:
    events = _read_events(root, directory, auth)
    if not events or not isinstance(events[0], PreflightObservation):
        raise ControlledWriteAttention("RECONCILE_REQUIRED")
    if any(
        isinstance(
            item, (ExecutionIntent, DispatchStarted, PostWriteObservation, WriteReceipt)
        )
        for item in events
    ):
        raise ControlledWriteAttention("RECONCILE_REQUIRED")
    state, backup, backup_meta = _backup_state(backup_path, before)
    manifests = [item for item in events if isinstance(item, BackupManifest)]
    if manifests:
        manifest = manifests[-1]
        if (
            backup is None
            or backup_meta is None
            or manifest.backup_path != str(backup_path)
            or manifest.action != auth.action
            or manifest.backup_sha256 != _sha(backup)
            or manifest.backup_size_bytes != len(backup)
            or manifest.backup_permission_mode != _mode(backup_meta)
            or manifest.source_permission_mode != _current_permission_mode(auth)
            or not manifest.permissions_restricted
            or not manifest.fsync_performed
        ):
            raise ControlledWriteAttention("INVALID_BACKUP")
        state = "exact-manifested"
    _append_event(
        root,
        directory,
        auth,
        PreIntentRecovery,
        "preintent-recovery",
        schema_version="v3-controlled-write-pre-intent-recovery-v1",
        hash_domain="projecttown/v3/controlled-write-pre-intent-recovery/v1",
        action=auth.action,
        observed_sha256=_sha(before),
        observed_size_bytes=len(before),
        observed_device=int(metadata.st_dev),
        observed_inode=int(metadata.st_ino),
        observed_permission_mode=_mode(metadata),
        backup_state=state,
        backup_sha256=_sha(backup) if backup is not None else None,
        backup_size_bytes=len(backup) if backup is not None else None,
        backup_permission_mode=_mode(backup_meta) if backup_meta is not None else None,
    )


def _ensure_backup(
    root: Path,
    backup_path: Path,
    before: bytes,
) -> tuple[bytes, os.stat_result]:
    state, data, metadata = _backup_state(backup_path, before)
    if state == "absent":
        _publish_exact(root, backup_path, before, "BACKUP_FAILED")
        data, metadata = _stable(backup_path, "INVALID_BACKUP")
    assert data is not None and metadata is not None
    if data != before or not _permissions_restricted(metadata):
        raise ControlledWriteAttention("BACKUP_VERIFY_FAILED")
    return data, metadata


def _receipt(
    root: Path,
    directory: Path,
    auth: UserAuthorization | RestoreAuthorization,
    backup: Path,
    manifest: BackupManifest,
    state: Literal["COMMITTED", "FAILED_NO_EFFECT"],
    final: bytes,
    authorization_path: Path,
    post: PostWriteObservation,
) -> WriteReceipt:
    if (
        manifest.action != auth.action
        or manifest.operation_id != auth.operation_id
        or manifest.authorization_hash != auth.authorization_hash
        or manifest.backup_path != str(backup)
        or manifest.backup_permission_mode < 0
        or not manifest.permissions_restricted
        or not manifest.fsync_performed
        or post.action != auth.action
        or post.observed_sha256 != _sha(final)
        or post.observed_size_bytes != len(final)
        or post.observed_permission_mode
        != _mode(_target(root, Path(auth.target_path), auth.target_relative_path)[1])
        or post.expected_permission_mode != _desired_permission_mode(auth)
    ):
        raise ControlledWriteError("INVALID_RECEIPT_BINDING")
    event = cast(
        WriteReceipt,
        _append_event(
            root,
            directory,
            auth,
            WriteReceipt,
            "receipt",
            schema_version="v3-controlled-write-receipt-v1",
            hash_domain="projecttown/v3/controlled-write-receipt/v1",
            action=auth.action,
            target_before_sha256=manifest.backup_sha256,
            target_after_sha256=(
                auth.after_sha256
                if isinstance(auth, UserAuthorization)
                else auth.desired_sha256
            ),
            authorization_path=str(authorization_path),
            target_path=auth.target_path,
            target_relative_path=auth.target_relative_path,
            manifest_path=str(_event_path(directory, manifest.sequence, "backup")),
            backup_path=str(backup),
            backup_sha256=manifest.backup_sha256,
            state=state,
            target_final_size_bytes=len(final),
            target_before_size_bytes=manifest.backup_size_bytes,
            target_after_size_bytes=(
                auth.after_size_bytes
                if isinstance(auth, UserAuthorization)
                else auth.desired_size_bytes
            ),
            backup_size_bytes=manifest.backup_size_bytes,
            target_before_permission_mode=_current_permission_mode(auth),
            target_after_permission_mode=_desired_permission_mode(auth),
            backup_permission_mode=manifest.backup_permission_mode,
            target_final_permission_mode=post.observed_permission_mode,
            manifest_event_hash=manifest.event_hash,
            final_observed_sha256=_sha(final),
            final_observation_event_hash=post.event_hash,
        ),
    )
    return event


def apply(
    root: Path,
    authorization_path: Path,
    result_path: Path,
    proposal_path: Path,
    target: Path,
    plan_path: Path,
    ledger_root: Path,
    backup_path: Path,
    receipt_out: Path,
    *,
    fail_at: str | None = None,
) -> WriteReceipt:
    auth = _load_auth(root, authorization_path)
    if not isinstance(auth, UserAuthorization):
        raise ControlledWriteError("INVALID_AUTHORIZATION")
    directory = _check_paths(root, auth, ledger_root, backup_path, receipt_out)
    lock = _auth_lock(auth)
    fd = _acquire_lock(Path(auth.lock_path), lock, recover_existing=True)
    temp: tuple[Path, os.stat_result] | None = None
    try:
        events = _read_events(root, directory, auth)
        if events and isinstance(events[-1], WriteReceipt):
            raise ControlledWriteAttention("ALREADY_TERMINAL")
        before, meta, desired = _verify_apply_inputs(
            root, auth, result_path, proposal_path, target, plan_path
        )
        if events:
            _append_pre_intent_recovery(
                root, directory, auth, before, meta, backup_path
            )
        else:
            _append_event(
                root,
                directory,
                auth,
                PreflightObservation,
                "preflight",
                schema_version="v3-controlled-write-preflight-observation-v1",
                hash_domain="projecttown/v3/controlled-write-preflight-observation/v1",
                action=auth.action,
                observed_sha256=_sha(before),
                observed_size_bytes=len(before),
                observed_device=int(meta.st_dev),
                observed_inode=int(meta.st_ino),
                observed_permission_mode=_mode(meta),
            )
        if fail_at == "before_intent":
            raise ControlledWriteError("INTERRUPTED_BEFORE_INTENT")
        if fail_at == "backup":
            raise ControlledWriteAttention("BACKUP_INTERRUPTED")
        backup_data, backup_meta = _ensure_backup(root, backup_path, before)
        restricted = _permissions_restricted(backup_meta)
        if backup_data != before or not restricted:
            raise ControlledWriteAttention("BACKUP_VERIFY_FAILED")
        manifests = [
            item
            for item in _read_events(root, directory, auth)
            if isinstance(item, BackupManifest)
        ]
        manifest = (
            manifests[-1]
            if manifests
            else cast(
                BackupManifest,
                _append_event(
                    root,
                    directory,
                    auth,
                    BackupManifest,
                    "backup",
                    schema_version="v3-controlled-write-backup-manifest-v1",
                    hash_domain="projecttown/v3/controlled-write-backup-manifest/v1",
                    action=auth.action,
                    backup_path=str(backup_path),
                    backup_sha256=_sha(backup_data),
                    backup_size_bytes=len(backup_data),
                    backup_permission_mode=_mode(backup_meta),
                    source_permission_mode=_current_permission_mode(auth),
                    permissions_restricted=restricted,
                    fsync_performed=True,
                ),
            )
        )
        if fail_at == "after_manifest":
            raise ControlledWriteAttention("INTERRUPTED_AFTER_MANIFEST")
        _append_event(
            root,
            directory,
            auth,
            ExecutionIntent,
            "intent",
            schema_version="v3-controlled-write-execution-intent-v1",
            hash_domain="projecttown/v3/controlled-write-execution-intent/v1",
            action=auth.action,
            nonce_sha256=_sha(auth.nonce.encode()),
            intended_sha256=_sha(desired),
            intended_size_bytes=len(desired),
        )
        if fail_at == "before_replace":
            raise ControlledWriteAttention("INTERRUPTED_BEFORE_DISPATCH")
        temp = _stage(target, desired, _desired_permission_mode(auth))
        live, live_meta, parent = _target(root, target, auth.target_relative_path)
        if (
            live != before
            or (live_meta.st_dev, live_meta.st_ino) != (meta.st_dev, meta.st_ino)
            or (int(parent.st_dev), int(parent.st_ino))
            != (auth.parent_device, auth.parent_inode)
        ):
            raise ControlledWriteAttention("EXTERNAL_DRIFT_BLOCKED")
        staged, staged_meta = temp
        _append_event(
            root,
            directory,
            auth,
            DispatchStarted,
            "dispatch",
            schema_version="v3-controlled-write-dispatch-started-v1",
            hash_domain="projecttown/v3/controlled-write-dispatch-started/v1",
            action=auth.action,
            dispatch_nonce_sha256=_sha((auth.nonce + "dispatch").encode()),
            temporary_sha256=_sha(desired),
            temporary_size_bytes=len(desired),
            temporary_device=int(staged_meta.st_dev),
            temporary_inode=int(staged_meta.st_ino),
            temporary_permission_mode=_mode(staged_meta),
            target_device=int(live_meta.st_dev),
            target_inode=int(live_meta.st_ino),
            target_permission_mode=_mode(live_meta),
            parent_device=int(parent.st_dev),
            parent_inode=int(parent.st_ino),
        )
        _validate_immediate_replace_state(
            root=root,
            target=target,
            auth=auth,
            before=before,
            before_metadata=meta,
            staged=staged,
            staged_metadata=staged_meta,
            desired=desired,
        )
        try:
            os.replace(staged, target)
        except OSError as error:
            raise ControlledWriteAttention("DISPATCH_OUTCOME_UNKNOWN") from error
        temp = None
        if fail_at == "after_replace":
            raise ControlledWriteAttention("EFFECT_PRESENT_ATTENTION")
        after, after_meta, after_parent = _target(
            root, target, auth.target_relative_path
        )
        scope_match = (int(after_parent.st_dev), int(after_parent.st_ino)) == (
            auth.parent_device,
            auth.parent_inode,
        )
        directory_fsync = _fsync_dir(target.parent)
        post = cast(
            PostWriteObservation,
            _append_event(
                root,
                directory,
                auth,
                PostWriteObservation,
                "post",
                schema_version="v3-controlled-write-post-write-observation-v1",
                hash_domain="projecttown/v3/controlled-write-post-write-observation/v1",
                action=auth.action,
                observed_sha256=_sha(after),
                observed_size_bytes=len(after),
                observed_device=int(after_meta.st_dev),
                observed_inode=int(after_meta.st_ino),
                observed_permission_mode=_mode(after_meta),
                expected_permission_mode=_desired_permission_mode(auth),
                expected_match=after == desired,
                scope_match=scope_match,
                directory_fsync=directory_fsync,
            ),
        )
        if (
            not post.expected_match
            or _mode(after_meta) != _desired_permission_mode(auth)
            or not scope_match
            or directory_fsync == "failed"
        ):
            raise ControlledWriteAttention("EFFECT_PRESENT_ATTENTION")
        if fail_at in ("post_observation", "receipt_publication"):
            raise ControlledWriteAttention("EFFECT_PRESENT_ATTENTION")
        receipt = _receipt(
            root,
            directory,
            auth,
            backup_path,
            manifest,
            "COMMITTED",
            after,
            authorization_path,
            post,
        )
        if check(authorization_path, ledger_root) != "COMMITTED":
            raise ControlledWriteAttention("POST_RECEIPT_VERIFY_FAILED")
    except (ControlledWriteError, MaterialWorkflowError, OSError) as error:
        _handle_operation_failure(
            root=root,
            directory=directory,
            auth=auth,
            target=target,
            fd=fd,
            lock=lock,
            error=error,
            temp=temp,
        )
    _release_lock(fd, Path(auth.lock_path), lock, owned=True)
    return receipt


def reconcile(
    root: Path,
    authorization_path: Path,
    target: Path,
    ledger_root: Path,
    backup_path: Path,
    receipt_out: Path,
) -> WriteReceipt:
    auth = _load_auth(root, authorization_path)
    if target != Path(auth.target_path):
        raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH")
    directory = _check_paths(root, auth, ledger_root, backup_path, receipt_out)
    lock = _auth_lock(auth)
    fd = _acquire_lock(Path(auth.lock_path), lock, recover_existing=True)
    releasing = False
    try:
        events = _read_events(root, directory, auth)
        if events and isinstance(events[-1], WriteReceipt):
            receipt = cast(WriteReceipt, events[-1])
            if check(authorization_path, ledger_root) != receipt.state:
                raise ControlledWriteAttention("TERMINAL_CHECK_FAILED")
            releasing = True
            _release_lock(fd, Path(auth.lock_path), lock, owned=True)
            return receipt
        manifests = [x for x in events if isinstance(x, BackupManifest)]
        intents = [x for x in events if isinstance(x, ExecutionIntent)]
        if not manifests or not intents:
            raise ControlledWriteAttention("RECONCILE_REQUIRED")
        manifest = manifests[-1]
        manifest_path = _event_path(directory, manifest.sequence, "backup")
        backup, backup_meta = _stable(backup_path, "INVALID_BACKUP")
        if (
            manifest_path.parent != directory
            or manifest.action != auth.action
            or manifest.operation_id != auth.operation_id
            or manifest.authorization_hash != auth.authorization_hash
            or manifest.backup_path != str(backup_path)
            or not manifest.permissions_restricted
            or not manifest.fsync_performed
            or not _permissions_restricted(backup_meta)
            or _mode(backup_meta) != manifest.backup_permission_mode
            or manifest.source_permission_mode != _current_permission_mode(auth)
            or (_sha(backup), len(backup))
            != (manifest.backup_sha256, manifest.backup_size_bytes)
        ):
            raise ControlledWriteAttention("INVALID_BACKUP")
        effect, current, metadata = _classify_target_effect(root, target, auth)
        if current is None or metadata is None:
            raise ControlledWriteAttention("TARGET_OBSERVATION_UNAVAILABLE")
        dispatch_seen = any(isinstance(item, DispatchStarted) for item in events)
        if effect == "effect_present" and not dispatch_seen:
            raise ControlledWriteAttention("EXTERNAL_DRIFT_BLOCKED")
        if effect == "external_drift":
            raise ControlledWriteAttention("EXTERNAL_DRIFT_BLOCKED")
        directory_fsync = _fsync_dir(target.parent)
        post = cast(
            PostWriteObservation,
            _append_event(
                root,
                directory,
                auth,
                PostWriteObservation,
                "post",
                schema_version="v3-controlled-write-post-write-observation-v1",
                hash_domain="projecttown/v3/controlled-write-post-write-observation/v1",
                action=auth.action,
                observed_sha256=_sha(current),
                observed_size_bytes=len(current),
                observed_device=int(metadata.st_dev),
                observed_inode=int(metadata.st_ino),
                observed_permission_mode=_mode(metadata),
                expected_permission_mode=_desired_permission_mode(auth),
                expected_match=effect == "effect_present",
                scope_match=True,
                directory_fsync=directory_fsync,
            ),
        )
        if directory_fsync == "failed":
            raise ControlledWriteAttention("DIRECTORY_FSYNC_FAILED")
        state: Literal["COMMITTED", "FAILED_NO_EFFECT"] = (
            "COMMITTED" if effect == "effect_present" else "FAILED_NO_EFFECT"
        )
        receipt = _receipt(
            root,
            directory,
            auth,
            backup_path,
            manifest,
            state,
            current,
            authorization_path,
            post,
        )
        if check(authorization_path, ledger_root) != state:
            raise ControlledWriteAttention("POST_RECEIPT_VERIFY_FAILED")
    except (ControlledWriteError, MaterialWorkflowError, OSError) as error:
        if releasing:
            raise
        _handle_operation_failure(
            root=root,
            directory=directory,
            auth=auth,
            target=target,
            fd=fd,
            lock=lock,
            error=error,
            temp=None,
        )
    _release_lock(fd, Path(auth.lock_path), lock, owned=True)
    return receipt


def create_restore_authorization(
    root: Path,
    receipt_path: Path,
    target: Path,
    ledger_root: Path,
    authorization_out: Path,
    operation_id: str,
    nonce: str,
) -> RestoreAuthorization:
    _validate_authorization_keys(operation_id, nonce)
    receipt_data, _ = _record_data(receipt_path, "INVALID_RECEIPT")
    receipt = parse_receipt_bytes(receipt_data)
    if receipt.state != "COMMITTED" or receipt.action != "apply-proposal-v1":
        raise ControlledWriteError("INVALID_RECEIPT")
    original_auth = _load_auth(root, Path(receipt.authorization_path))
    if not isinstance(original_auth, UserAuthorization):
        raise ControlledWriteError("INVALID_RECEIPT")
    original_dir = _operation_dir(
        root, Path(original_auth.ledger_root), original_auth.operation_id, create=False
    )
    original_events = _read_events(root, original_dir, original_auth)
    if (
        not original_events
        or original_events[-1] != receipt
        or receipt_path != Path(original_auth.receipt_path)
        or receipt.target_path != original_auth.target_path
        or receipt.target_relative_path != original_auth.target_relative_path
        or target != Path(receipt.target_path)
        or target != Path(original_auth.target_path)
        or Path(receipt.manifest_path).parent != original_dir
        or check(Path(receipt.authorization_path), Path(original_auth.ledger_root))
        != "COMMITTED"
    ):
        raise ControlledWriteError("INVALID_RECEIPT")
    manifest = parse_event_bytes(
        _record_data(Path(receipt.manifest_path), "INVALID_BACKUP")[0]
    )
    if (
        not isinstance(manifest, BackupManifest)
        or manifest.event_hash != receipt.manifest_event_hash
        or manifest.action != original_auth.action
        or manifest.operation_id != original_auth.operation_id
        or manifest.authorization_hash != original_auth.authorization_hash
        or manifest.source_permission_mode != original_auth.before_permission_mode
        or not manifest.permissions_restricted
        or not manifest.fsync_performed
    ):
        raise ControlledWriteError("INVALID_RECEIPT")
    # The committed receipt is self-authenticating and points to the immutable original backup.
    source_backup = Path(receipt.backup_path)
    source, source_meta = _stable(source_backup, "INVALID_BACKUP")
    if (
        source_backup != Path(manifest.backup_path)
        or (_sha(source), len(source))
        != (manifest.backup_sha256, manifest.backup_size_bytes)
        or (receipt.backup_sha256, receipt.backup_size_bytes)
        != (manifest.backup_sha256, manifest.backup_size_bytes)
        or receipt.backup_permission_mode != manifest.backup_permission_mode
        or receipt.target_before_permission_mode != manifest.source_permission_mode
        or _mode(source_meta) != manifest.backup_permission_mode
        or not _permissions_restricted(source_meta)
    ):
        raise ControlledWriteError("INVALID_RECEIPT")
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError as error:
        raise ControlledWriteError("TARGET_PATH_MISMATCH") from error
    if relative != original_auth.target_relative_path:
        raise ControlledWriteError("TARGET_PATH_MISMATCH")
    current, meta, parent = _target(root, target, relative)
    if (
        _sha(current),
        len(current),
        int(parent.st_dev),
        int(parent.st_ino),
    ) != (
        receipt.target_after_sha256,
        receipt.target_after_size_bytes,
        original_auth.parent_device,
        original_auth.parent_inode,
    ):
        raise ControlledWriteError("TARGET_BINDING_CHANGED")
    if authorization_out.parent == ledger_root / operation_id:
        raise ControlledWriteError("INVALID_AUTHORIZATION_PATH")
    directory = _operation_dir(root, ledger_root, operation_id, create=True)
    value = cast(
        RestoreAuthorization,
        _make(
            RestoreAuthorization,
            schema_version="v3-controlled-write-restore-authorization-v1",
            hash_domain="projecttown/v3/controlled-write-restore-authorization/v1",
            action="restore-backup-v1",
            operation_id=operation_id,
            material_root=str(root),
            target_path=str(target),
            target_relative_path=relative,
            current_sha256=_sha(current),
            current_size_bytes=len(current),
            current_device=int(meta.st_dev),
            current_inode=int(meta.st_ino),
            current_permission_mode=_mode(meta),
            parent_device=int(parent.st_dev),
            parent_inode=int(parent.st_ino),
            original_receipt_path=str(receipt_path),
            original_receipt_bytes_sha256=_sha(receipt_data),
            original_receipt_hash=receipt.receipt_hash,
            source_backup_manifest_path=str(receipt.manifest_path),
            source_backup_manifest_hash=receipt.manifest_event_hash,
            source_backup_path=str(source_backup),
            source_backup_sha256=_sha(source),
            source_backup_size_bytes=len(source),
            desired_sha256=_sha(source),
            desired_size_bytes=len(source),
            desired_permission_mode=manifest.source_permission_mode,
            ledger_root=str(ledger_root),
            lock_path=str(target.with_name(target.name + ".projecttown.lock")),
            backup_path=str(directory / "pre-restore.bin"),
            receipt_path=str(directory / "receipt.json"),
            caller="explicit-local-caller-v1",
            authorization_semantics="single-use-until-first-intent-v1",
            nonce=nonce,
        ),
    )
    publish_record(root, authorization_out, value)
    return value


def restore(
    root: Path,
    authorization_path: Path,
    target: Path,
    ledger_root: Path,
    backup_path: Path,
    receipt_out: Path,
    *,
    fail_at: str | None = None,
) -> WriteReceipt:
    auth = _load_auth(root, authorization_path)
    if not isinstance(auth, RestoreAuthorization):
        raise ControlledWriteError("INVALID_RESTORE_AUTHORIZATION")
    if target != Path(auth.target_path):
        raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH")
    directory = _check_paths(root, auth, ledger_root, backup_path, receipt_out)
    lock = _auth_lock(auth)
    fd = _acquire_lock(Path(auth.lock_path), lock, recover_existing=True)
    temp: tuple[Path, os.stat_result] | None = None
    try:
        events = _read_events(root, directory, auth)
        if any(isinstance(item, ExecutionIntent) for item in events):
            raise ControlledWriteAttention("RECONCILE_REQUIRED")
        desired, source_meta = _stable(Path(auth.source_backup_path), "INVALID_BACKUP")
        receipt_data, _ = _record_data(
            Path(auth.original_receipt_path), "INVALID_RECEIPT"
        )
        original_receipt = parse_receipt_bytes(receipt_data)
        original_auth = _load_auth(root, Path(original_receipt.authorization_path))
        source_manifest_data, _ = _record_data(
            Path(auth.source_backup_manifest_path), "INVALID_BACKUP"
        )
        source_manifest = parse_event_bytes(source_manifest_data)
        if (
            (_sha(desired), len(desired))
            != (auth.source_backup_sha256, auth.source_backup_size_bytes)
            or (_sha(desired), len(desired))
            != (auth.desired_sha256, auth.desired_size_bytes)
            or not _permissions_restricted(source_meta)
            or _sha(receipt_data) != auth.original_receipt_bytes_sha256
            or original_receipt.receipt_hash != auth.original_receipt_hash
            or not isinstance(original_auth, UserAuthorization)
            or not isinstance(source_manifest, BackupManifest)
            or source_manifest.event_hash != auth.source_backup_manifest_hash
            or source_manifest.action != "apply-proposal-v1"
            or source_manifest.operation_id != original_auth.operation_id
            or source_manifest.authorization_hash != original_auth.authorization_hash
            or source_manifest.backup_permission_mode != _mode(source_meta)
            or source_manifest.source_permission_mode != auth.desired_permission_mode
            or source_manifest.backup_path != auth.source_backup_path
            or source_manifest.backup_sha256 != auth.source_backup_sha256
            or source_manifest.backup_size_bytes != auth.source_backup_size_bytes
        ):
            raise ControlledWriteError("RESTORE_SOURCE_CHANGED")
        before, meta, parent = _target(root, target, auth.target_relative_path)
        if (
            _sha(before),
            len(before),
            int(meta.st_dev),
            int(meta.st_ino),
            _mode(meta),
            int(parent.st_dev),
            int(parent.st_ino),
        ) != (
            auth.current_sha256,
            auth.current_size_bytes,
            auth.current_device,
            auth.current_inode,
            auth.current_permission_mode,
            auth.parent_device,
            auth.parent_inode,
        ):
            raise ControlledWriteError("TARGET_BINDING_CHANGED")
        if events:
            _append_pre_intent_recovery(
                root, directory, auth, before, meta, backup_path
            )
        else:
            _append_event(
                root,
                directory,
                auth,
                PreflightObservation,
                "preflight",
                schema_version="v3-controlled-write-preflight-observation-v1",
                hash_domain="projecttown/v3/controlled-write-preflight-observation/v1",
                action=auth.action,
                observed_sha256=_sha(before),
                observed_size_bytes=len(before),
                observed_device=int(meta.st_dev),
                observed_inode=int(meta.st_ino),
                observed_permission_mode=_mode(meta),
            )
        if fail_at == "before_intent":
            raise ControlledWriteError("INTERRUPTED_BEFORE_INTENT")
        if fail_at == "backup":
            raise ControlledWriteAttention("BACKUP_INTERRUPTED")
        backup, backup_meta = _ensure_backup(root, backup_path, before)
        restricted = _permissions_restricted(backup_meta)
        if backup != before or not restricted:
            raise ControlledWriteAttention("BACKUP_VERIFY_FAILED")
        manifests = [
            item
            for item in _read_events(root, directory, auth)
            if isinstance(item, BackupManifest)
        ]
        manifest = (
            manifests[-1]
            if manifests
            else cast(
                BackupManifest,
                _append_event(
                    root,
                    directory,
                    auth,
                    BackupManifest,
                    "backup",
                    schema_version="v3-controlled-write-backup-manifest-v1",
                    hash_domain="projecttown/v3/controlled-write-backup-manifest/v1",
                    action=auth.action,
                    backup_path=str(backup_path),
                    backup_sha256=_sha(backup),
                    backup_size_bytes=len(backup),
                    backup_permission_mode=_mode(backup_meta),
                    source_permission_mode=_current_permission_mode(auth),
                    permissions_restricted=restricted,
                    fsync_performed=True,
                ),
            )
        )
        if fail_at == "after_manifest":
            raise ControlledWriteAttention("INTERRUPTED_AFTER_MANIFEST")
        _append_event(
            root,
            directory,
            auth,
            ExecutionIntent,
            "intent",
            schema_version="v3-controlled-write-execution-intent-v1",
            hash_domain="projecttown/v3/controlled-write-execution-intent/v1",
            action=auth.action,
            nonce_sha256=_sha(auth.nonce.encode()),
            intended_sha256=_sha(desired),
            intended_size_bytes=len(desired),
        )
        if fail_at == "before_replace":
            raise ControlledWriteAttention("INTERRUPTED_BEFORE_DISPATCH")
        temp = _stage(target, desired, _desired_permission_mode(auth))
        live, live_meta, parent = _target(root, target, auth.target_relative_path)
        if (
            live != before
            or (live_meta.st_dev, live_meta.st_ino) != (meta.st_dev, meta.st_ino)
            or (int(parent.st_dev), int(parent.st_ino))
            != (auth.parent_device, auth.parent_inode)
        ):
            raise ControlledWriteAttention("EXTERNAL_DRIFT_BLOCKED")
        staged, smeta = temp
        _append_event(
            root,
            directory,
            auth,
            DispatchStarted,
            "dispatch",
            schema_version="v3-controlled-write-dispatch-started-v1",
            hash_domain="projecttown/v3/controlled-write-dispatch-started/v1",
            action=auth.action,
            dispatch_nonce_sha256=_sha((auth.nonce + "dispatch").encode()),
            temporary_sha256=_sha(desired),
            temporary_size_bytes=len(desired),
            temporary_device=int(smeta.st_dev),
            temporary_inode=int(smeta.st_ino),
            temporary_permission_mode=_mode(smeta),
            target_device=int(live_meta.st_dev),
            target_inode=int(live_meta.st_ino),
            target_permission_mode=_mode(live_meta),
            parent_device=int(parent.st_dev),
            parent_inode=int(parent.st_ino),
        )
        _validate_immediate_replace_state(
            root=root,
            target=target,
            auth=auth,
            before=before,
            before_metadata=meta,
            staged=staged,
            staged_metadata=smeta,
            desired=desired,
        )
        try:
            os.replace(staged, target)
        except OSError as error:
            raise ControlledWriteAttention("DISPATCH_OUTCOME_UNKNOWN") from error
        temp = None
        if fail_at == "after_replace":
            raise ControlledWriteAttention("EFFECT_PRESENT_ATTENTION")
        after, ameta, after_parent = _target(root, target, auth.target_relative_path)
        scope_match = (int(after_parent.st_dev), int(after_parent.st_ino)) == (
            auth.parent_device,
            auth.parent_inode,
        )
        directory_fsync = _fsync_dir(target.parent)
        post = cast(
            PostWriteObservation,
            _append_event(
                root,
                directory,
                auth,
                PostWriteObservation,
                "post",
                schema_version="v3-controlled-write-post-write-observation-v1",
                hash_domain="projecttown/v3/controlled-write-post-write-observation/v1",
                action=auth.action,
                observed_sha256=_sha(after),
                observed_size_bytes=len(after),
                observed_device=int(ameta.st_dev),
                observed_inode=int(ameta.st_ino),
                observed_permission_mode=_mode(ameta),
                expected_permission_mode=_desired_permission_mode(auth),
                expected_match=after == desired,
                scope_match=scope_match,
                directory_fsync=directory_fsync,
            ),
        )
        if (
            not post.expected_match
            or _mode(ameta) != _desired_permission_mode(auth)
            or not scope_match
            or directory_fsync == "failed"
            or fail_at in ("post_observation", "receipt_publication")
        ):
            raise ControlledWriteAttention("EFFECT_PRESENT_ATTENTION")
        receipt = _receipt(
            root,
            directory,
            auth,
            backup_path,
            manifest,
            "COMMITTED",
            after,
            authorization_path,
            post,
        )
        if check(authorization_path, ledger_root) != "COMMITTED":
            raise ControlledWriteAttention("POST_RECEIPT_VERIFY_FAILED")
    except (ControlledWriteError, MaterialWorkflowError, OSError) as error:
        _handle_operation_failure(
            root=root,
            directory=directory,
            auth=auth,
            target=target,
            fd=fd,
            lock=lock,
            error=error,
            temp=temp,
        )
    _release_lock(fd, Path(auth.lock_path), lock, owned=True)
    return receipt


def check(authorization_path: Path, ledger_root: Path) -> str:
    # Check is read-only but verifies the same evidence and final target binding.
    raw, _ = _record_data(authorization_path, "INVALID_AUTHORIZATION")
    auth = parse_record_bytes(raw)
    if (
        not isinstance(auth, (UserAuthorization, RestoreAuthorization))
        or str(ledger_root) != auth.ledger_root
    ):
        raise ControlledWriteError("INVALID_AUTHORIZATION")
    root = Path(auth.material_root)
    _external(root, authorization_path, "INVALID_AUTHORIZATION_PATH")
    _safe_dir(root, "INVALID_ROOT")
    directory = _operation_dir(root, ledger_root, auth.operation_id, create=False)
    events = _read_events(root, directory, auth)
    if not events:
        current, metadata, parent = _target(
            root, Path(auth.target_path), auth.target_relative_path
        )
        if (
            _sha(current),
            len(current),
            int(metadata.st_dev),
            int(metadata.st_ino),
            _mode(metadata),
            int(parent.st_dev),
            int(parent.st_ino),
        ) != (*_authorized_current(auth), auth.parent_device, auth.parent_inode):
            raise ControlledWriteError("TARGET_BINDING_CHANGED")
        return "AUTHORIZED_NOT_DISPATCHED"
    if isinstance(events[-1], WriteReceipt):
        receipt = events[-1]
        manifests = [item for item in events if isinstance(item, BackupManifest)]
        posts = [item for item in events if isinstance(item, PostWriteObservation)]
        if (
            len(manifests) != 1
            or not posts
            or receipt.authorization_path != str(authorization_path)
            or receipt.target_path != auth.target_path
            or receipt.target_relative_path != auth.target_relative_path
        ):
            raise ControlledWriteError("INVALID_RECEIPT")
        manifest = manifests[0]
        final_post = posts[-1]
        manifest_path = _event_path(directory, manifest.sequence, "backup")
        if Path(receipt.manifest_path) != manifest_path:
            raise ControlledWriteError("INVALID_RECEIPT")
        parsed_manifest = parse_event_bytes(
            _record_data(manifest_path, "INVALID_BACKUP")[0]
        )
        backup, backup_meta = _stable(Path(receipt.backup_path), "INVALID_BACKUP")
        if (
            parsed_manifest != manifest
            or manifest.event_hash != receipt.manifest_event_hash
            or manifest.action != auth.action
            or manifest.operation_id != auth.operation_id
            or manifest.authorization_hash != auth.authorization_hash
            or manifest.backup_path != auth.backup_path
            or receipt.backup_path != auth.backup_path
            or not manifest.permissions_restricted
            or not manifest.fsync_performed
            or not _permissions_restricted(backup_meta)
            or _mode(backup_meta) != manifest.backup_permission_mode
            or (
                manifest.backup_path,
                manifest.backup_sha256,
                manifest.backup_size_bytes,
            )
            != (receipt.backup_path, receipt.backup_sha256, receipt.backup_size_bytes)
            or (_sha(backup), len(backup))
            != (receipt.backup_sha256, receipt.backup_size_bytes)
            or receipt.final_observation_event_hash != final_post.event_hash
            or final_post.directory_fsync == "failed"
            or not final_post.scope_match
            or receipt.target_before_permission_mode != _current_permission_mode(auth)
            or receipt.target_after_permission_mode != _desired_permission_mode(auth)
            or receipt.backup_permission_mode != manifest.backup_permission_mode
            or receipt.target_final_permission_mode
            != final_post.observed_permission_mode
            or final_post.expected_permission_mode != _desired_permission_mode(auth)
        ):
            raise ControlledWriteError("INVALID_BACKUP")
        if isinstance(auth, UserAuthorization):
            try:
                proposal = load_executable_proposal(
                    Path(auth.proposal_path), material_root=root
                )
            except ExecutableProposalError as error:
                raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH") from error
            if (
                _sha(_stable(Path(auth.result_path), "INVALID_RESULT")[0])
                != auth.result_bytes_sha256
                or _sha(_stable(Path(auth.proposal_path), "INVALID_PROPOSAL")[0])
                != auth.proposal_bytes_sha256
                or _sha(_stable(Path(auth.plan_path), "INVALID_PLAN")[0])
                != auth.plan_bytes_sha256
                or proposal.proposal_hash != auth.proposal_hash
                or (proposal.post_image_sha256, proposal.post_image_size_bytes)
                != (auth.after_sha256, auth.after_size_bytes)
            ):
                raise ControlledWriteError("AUTHORIZATION_BINDING_MISMATCH")
        else:
            source, source_meta = _stable(
                Path(auth.source_backup_path), "INVALID_BACKUP"
            )
            original_receipt, _ = _record_data(
                Path(auth.original_receipt_path), "INVALID_RECEIPT"
            )
            original_receipt_record = parse_receipt_bytes(original_receipt)
            original_auth = _load_auth(
                root, Path(original_receipt_record.authorization_path)
            )
            source_manifest = parse_event_bytes(
                _record_data(Path(auth.source_backup_manifest_path), "INVALID_BACKUP")[
                    0
                ]
            )
            if (
                (_sha(source), len(source))
                != (auth.source_backup_sha256, auth.source_backup_size_bytes)
                or not _permissions_restricted(source_meta)
                or _sha(original_receipt) != auth.original_receipt_bytes_sha256
                or original_receipt_record.receipt_hash != auth.original_receipt_hash
                or not isinstance(original_auth, UserAuthorization)
                or not isinstance(source_manifest, BackupManifest)
                or _mode(source_meta) != source_manifest.backup_permission_mode
                or source_manifest.event_hash != auth.source_backup_manifest_hash
                or source_manifest.action != "apply-proposal-v1"
                or source_manifest.operation_id != original_auth.operation_id
                or source_manifest.authorization_hash
                != original_auth.authorization_hash
                or source_manifest.backup_path != auth.source_backup_path
                or source_manifest.backup_sha256 != auth.source_backup_sha256
                or source_manifest.backup_size_bytes != auth.source_backup_size_bytes
                or source_manifest.source_permission_mode
                != auth.desired_permission_mode
            ):
                raise ControlledWriteError("RESTORE_SOURCE_CHANGED")
        try:
            current, metadata, parent = _target(
                root,
                Path(auth.target_path),
                auth.target_relative_path,
            )
        except ControlledWriteError:
            return "TARGET_CHANGED_AFTER_RECEIPT"
        if (_sha(current), len(current)) != (
            receipt.final_observed_sha256,
            receipt.target_final_size_bytes,
        ) or (
            int(metadata.st_dev),
            int(metadata.st_ino),
            _mode(metadata),
            int(parent.st_dev),
            int(parent.st_ino),
        ) != (
            final_post.observed_device,
            final_post.observed_inode,
            final_post.observed_permission_mode,
            auth.parent_device,
            auth.parent_inode,
        ):
            return "TARGET_CHANGED_AFTER_RECEIPT"
        return receipt.state
    manifests = [item for item in events if isinstance(item, BackupManifest)]
    if manifests:
        manifest = manifests[-1]
        backup_path = Path(auth.backup_path)
        backup, backup_meta = _stable(backup_path, "INVALID_BACKUP")
        if (
            manifest.backup_path != str(backup_path)
            or manifest.action != auth.action
            or manifest.operation_id != auth.operation_id
            or manifest.authorization_hash != auth.authorization_hash
            or not manifest.permissions_restricted
            or not manifest.fsync_performed
            or not _permissions_restricted(backup_meta)
            or _mode(backup_meta) != manifest.backup_permission_mode
            or manifest.source_permission_mode != _current_permission_mode(auth)
            or (_sha(backup), len(backup))
            != (manifest.backup_sha256, manifest.backup_size_bytes)
        ):
            raise ControlledWriteError("INVALID_BACKUP")
    effect, _current, _metadata = _classify_target_effect(
        root, Path(auth.target_path), auth
    )
    if effect in ("unknown", "external_drift") or (
        effect == "effect_present"
        and not any(isinstance(item, DispatchStarted) for item in events)
    ):
        raise ControlledWriteAttention("EXTERNAL_DRIFT_BLOCKED")
    return "RECONCILE_REQUIRED"


__all__ = [
    "AttentionRecord",
    "BackupManifest",
    "ControlledWriteAttention",
    "ControlledWriteError",
    "DispatchStarted",
    "ExecutionIntent",
    "LockRecord",
    "PostWriteObservation",
    "PreIntentRecovery",
    "PreflightObservation",
    "RestoreAuthorization",
    "UserAuthorization",
    "WriteReceipt",
    "apply",
    "check",
    "create_authorization",
    "create_restore_authorization",
    "load_record",
    "parse_authorization_bytes",
    "parse_event_bytes",
    "parse_receipt_bytes",
    "parse_record_bytes",
    "parse_restore_authorization_bytes",
    "publish_record",
    "reconcile",
    "restore",
    "serialize_authorization",
    "serialize_event",
    "serialize_receipt",
    "serialize_record",
    "serialize_restore_authorization",
]
