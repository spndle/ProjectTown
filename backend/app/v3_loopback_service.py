"""Feature-gated local bridge from a loopback UI to the 3C kernel."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Literal

from . import controlled_write
from .controlled_write import RestoreAuthorization, UserAuthorization
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file
from .v3_loopback_records import (
    IdempotencyIntent,
    IdempotencyResult,
    LoopbackRecordError,
    OperationBinding,
    canonical_json,
    load_record,
    make_intent,
    make_result,
    publish_create_only,
    sha256,
)

Action = Literal["apply", "reconcile", "restore"]
_MAX_RECORDS = 512
_MAX_SESSIONS = 512
_SESSION_IDLE_SECONDS = 15 * 60
_SESSION_ABSOLUTE_SECONDS = 60 * 60


class LoopbackServiceError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code, self.status_code = code, status_code
        super().__init__(code)


class LoopbackService:
    """State that must not survive a process restart (sessions and locks)."""

    def __init__(self, work_root: Path, *, allow_test_client: bool = False) -> None:
        self.work_root = Path(work_root).resolve(strict=True)
        self.bindings_dir = self.work_root / "bindings"
        self.idempotency_dir = self.work_root / "idempotency"
        try:
            directories = (
                (self.work_root, self.work_root.lstat()),
                (self.bindings_dir, self.bindings_dir.lstat()),
                (self.idempotency_dir, self.idempotency_dir.lstat()),
            )
        except OSError as error:
            raise LoopbackServiceError("INVALID_WORK_ROOT", 503) from error
        if any(
            path.resolve(strict=True) != path or not is_safe_directory(metadata)
            for path, metadata in directories
        ):
            raise LoopbackServiceError("INVALID_WORK_ROOT", 503)
        self.allow_test_client = allow_test_client
        self._sessions: dict[str, tuple[str, float, float]] = {}
        self._sessions_lock = threading.RLock()
        # A process-local reservation is necessary because per-operation locks
        # do not serialize two different operations racing for the same
        # physical idempotency-directory capacity.
        self._idempotency_lock = threading.RLock()
        self._operation_locks: dict[str, threading.Lock] = {}
        self._operation_locks_guard = threading.Lock()

    def client_allowed(self, host: str | None) -> bool:
        return host == "127.0.0.1" or (self.allow_test_client and host == "testclient")

    def bootstrap(self) -> tuple[str, str]:
        now = time.monotonic()
        with self._sessions_lock:
            self._purge_expired_sessions(now)
            if len(self._sessions) >= _MAX_SESSIONS:
                raise LoopbackServiceError("SESSION_CAPACITY_REACHED", 503)
            # A collision is extraordinarily unlikely, but overwriting an
            # existing valid session would be an authority violation.
            while True:
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                if token not in self._sessions:
                    self._sessions[token] = (csrf, now, now)
                    return token, csrf

    def _purge_expired_sessions(self, now: float) -> None:
        for token, (_, created, last_seen) in tuple(self._sessions.items()):
            if (
                now - created > _SESSION_ABSOLUTE_SECONDS
                or now - last_seen > _SESSION_IDLE_SECONDS
            ):
                self._sessions.pop(token, None)

    def verify_session(
        self, token: str | None, csrf: str | None = None, *, mutation: bool = False
    ) -> None:
        if not isinstance(token, str):
            raise LoopbackServiceError("SESSION_REQUIRED", 401)
        now = time.monotonic()
        with self._sessions_lock:
            value = self._sessions.get(token)
            if value is None:
                raise LoopbackServiceError("SESSION_REQUIRED", 401)
            expected, created, last_seen = value
            if (
                now - created > _SESSION_ABSOLUTE_SECONDS
                or now - last_seen > _SESSION_IDLE_SECONDS
            ):
                self._sessions.pop(token, None)
                raise LoopbackServiceError("SESSION_EXPIRED", 401)
            if mutation and (
                not isinstance(csrf, str) or not hmac.compare_digest(expected, csrf)
            ):
                raise LoopbackServiceError("CSRF_REJECTED", 403)
            self._sessions[token] = (expected, created, now)

    def bindings(self) -> list[dict[str, str]]:
        values: list[dict[str, str]] = []
        try:
            paths = sorted(self.bindings_dir.iterdir())
        except OSError as error:
            raise LoopbackServiceError("INVALID_BINDING", 409) from error
        for path in paths:
            # A work root is an allowlist, not an opportunistic directory scan.
            # Stray entries would otherwise make an operator miss an invalid
            # binding while the UI appears healthy.
            if not path.is_file() or path.suffix != ".json":
                raise LoopbackServiceError("INVALID_BINDING", 409)
            binding = self._binding(path)
            self._verified_auth(binding)
            values.append(
                {
                    "operation_id": binding.web_operation_id,
                    "target": binding.target_display,
                    "target_path_sha256": binding.target_path_sha256,
                }
            )
        return values

    def inspect(self, operation_id: str) -> dict[str, Any]:
        binding = self._binding_for(operation_id)
        return {
            "operation_id": binding.web_operation_id,
            "target": binding.target_display,
            "target_path_sha256": binding.target_path_sha256,
            "allowed_mutations": binding.allowed_mutations,
            "binding_hash": binding.binding_hash,
            "confirmations": {
                action: self._confirmation(action, binding)
                for action in binding.allowed_mutations
            },
        }

    def check(self, operation_id: str) -> dict[str, Any]:
        binding = self._binding_for(operation_id)
        try:
            state = controlled_write.check(
                Path(binding.authorization_path), self._ledger(binding)
            )
            return {"code": "CHECKED", "state": state, "write_performed": False}
        except (
            controlled_write.ControlledWriteError,
            controlled_write.ControlledWriteAttention,
        ) as error:
            return {
                "code": getattr(error, "code", "CHECK_FAILED"),
                "state": "ATTENTION",
                "write_performed": None,
            }

    def mutate(
        self, operation_id: str, action: Action, confirmation: str, key: str
    ) -> tuple[int, dict[str, Any]]:
        binding = self._binding_for(operation_id)
        if (
            action not in binding.allowed_mutations
            or confirmation != self._confirmation(action, binding)
        ):
            raise LoopbackServiceError("CONFIRMATION_REJECTED", 400)
        if not 16 <= len(key) <= 200 or any(
            ord(char) < 33 or ord(char) > 126 for char in key
        ):
            raise LoopbackServiceError("IDEMPOTENCY_KEY_REJECTED", 400)
        digest = sha256(
            canonical_json(
                {
                    "api": "v3-loopback-api-v1",
                    "action": action,
                    "binding_hash": binding.binding_hash,
                    "authorization_hash": binding.authorization_hash,
                    "web_operation_id": operation_id,
                    "confirmation": confirmation,
                }
            )
        )
        key_hash = sha256(key.encode("ascii"))
        with self._operation_locks_guard:
            lock = self._operation_locks.setdefault(operation_id, threading.Lock())
        with lock, self._idempotency_lock:
            existing = self._idempotency(operation_id, key_hash)
            if existing[0] is not None:
                intent = existing[0]
                if intent.request_digest != digest:
                    raise LoopbackServiceError("IDEMPOTENCY_CONFLICT", 409)
                if existing[1] is None:
                    return 409, {
                        "code": "IDEMPOTENCY_ATTENTION",
                        "outcome": "attention",
                        "write_performed": None,
                    }
                result = existing[1]
                return 200, self._result_projection(result)
            # A request creates both an immutable intent and an immutable
            # result.  Reserve both slots before dispatch so concurrent
            # operations cannot exceed the physical 512-file limit.
            if self._record_count() + 2 > _MAX_RECORDS:
                raise LoopbackServiceError("IDEMPOTENCY_CAPACITY_REACHED", 503)
            intent = make_intent(
                web_operation_id=operation_id,
                key_sha256=key_hash,
                request_digest=digest,
                action=action,
                binding_hash=binding.binding_hash,
                authorization_hash=binding.authorization_hash,
            )
            try:
                publish_create_only(
                    self.idempotency_dir,
                    self._intent_path(operation_id, key_hash),
                    intent,
                )
            except LoopbackRecordError as error:
                if error.code == "CREATE_ONLY_CONFLICT":
                    return 409, {
                        "code": "IDEMPOTENCY_ATTENTION",
                        "outcome": "attention",
                        "write_performed": None,
                    }
                raise LoopbackServiceError(
                    "IDEMPOTENCY_PUBLICATION_ATTENTION", 409
                ) from error
            result = self._dispatch(
                binding, action, key_hash, digest, intent.intent_hash
            )
            try:
                publish_create_only(
                    self.idempotency_dir,
                    self._result_path(operation_id, key_hash),
                    result,
                )
            except LoopbackRecordError:
                return 409, {
                    "code": "IDEMPOTENCY_ATTENTION",
                    "outcome": "attention",
                    "write_performed": None,
                }
            return 200, self._result_projection(result)

    def _dispatch(
        self,
        binding: OperationBinding,
        action: Action,
        key_hash: str,
        digest: str,
        intent_hash: str,
    ) -> IdempotencyResult:
        auth = self._verified_auth(binding)
        try:
            root, auth_path, target, ledger, backup, receipt = (
                Path(auth.material_root),
                Path(binding.authorization_path),
                Path(auth.target_path),
                Path(auth.ledger_root),
                Path(auth.backup_path),
                Path(auth.receipt_path),
            )
            if action == "check":
                raise AssertionError("check is not mutable")
            if action == "reconcile":
                receipt_value = controlled_write.reconcile(
                    root, auth_path, target, ledger, backup, receipt
                )
            elif action == "apply" and isinstance(auth, UserAuthorization):
                receipt_value = controlled_write.apply(
                    root,
                    auth_path,
                    Path(auth.result_path),
                    Path(auth.proposal_path),
                    target,
                    Path(auth.plan_path),
                    ledger,
                    backup,
                    receipt,
                )
            elif action == "restore" and isinstance(auth, RestoreAuthorization):
                receipt_value = controlled_write.restore(
                    root, auth_path, target, ledger, backup, receipt
                )
            else:
                raise LoopbackServiceError("ACTION_NOT_AUTHORIZED", 403)
            code, outcome, wrote = (
                receipt_value.state,
                "completed",
                action in {"apply", "restore"},
            )
        except controlled_write.ControlledWriteAttention as error:
            code, outcome, wrote = error.code, "attention", None
        except controlled_write.ControlledWriteError as error:
            code, outcome, wrote = error.code, "rejected", False
        return make_result(
            web_operation_id=binding.web_operation_id,
            key_sha256=key_hash,
            request_digest=digest,
            action=action,
            binding_hash=binding.binding_hash,
            authorization_hash=binding.authorization_hash,
            intent_hash=intent_hash,
            outcome=outcome,
            response_code=code,
            write_performed=wrote,
        )

    def _result_projection(self, result: IdempotencyResult) -> dict[str, Any]:
        return {
            "code": result.response_code,
            "outcome": result.outcome,
            "write_performed": result.write_performed,
        }

    def _binding(self, path: Path) -> OperationBinding:
        try:
            record = load_record(path)
        except LoopbackRecordError as error:
            raise LoopbackServiceError(error.code, 409) from error
        if not isinstance(record, OperationBinding):
            raise LoopbackServiceError("INVALID_BINDING", 409)
        if path.name != f"{record.web_operation_id}.json":
            raise LoopbackServiceError("INVALID_BINDING", 409)
        metadata = self.work_root.lstat()
        if record.work_root != str(self.work_root) or (
            record.work_root_device,
            record.work_root_inode,
        ) != (int(metadata.st_dev), int(metadata.st_ino)):
            raise LoopbackServiceError("INVALID_BINDING", 409)
        return record

    def _binding_for(self, operation_id: str) -> OperationBinding:
        if (
            not isinstance(operation_id, str)
            or len(operation_id) != 64
            or any(char not in "0123456789abcdef" for char in operation_id)
        ):
            raise LoopbackServiceError("OPERATION_ID_REJECTED", 400)
        binding = self._binding(self.bindings_dir / f"{operation_id}.json")
        # Inspection is part of the authority boundary: never display or
        # enable actions from a binding whose duplicated projection has drifted
        # from its immutable authorization.
        self._verified_auth(binding)
        return binding

    def _verified_auth(
        self, binding: OperationBinding
    ) -> UserAuthorization | RestoreAuthorization:
        try:
            path = Path(binding.authorization_path)
            if (
                path.resolve(strict=True) != path
                or path.parent.resolve(strict=True) != path.parent
                or not path.is_relative_to(self.work_root)
            ):
                raise OSError("invalid binding path")
            metadata = path.lstat()
            if not is_safe_directory(path.parent.lstat()) or is_reparse(metadata):
                raise OSError("invalid auth")
            stable = read_stable_regular_file(
                path, metadata, capture_bytes=True, require_single_link=True
            )
            if stable is None or stable[2] is None:
                raise OSError("unstable auth")
            data = stable[2]
            auth = controlled_write.parse_record_bytes(data)
        except (OSError, controlled_write.ControlledWriteError) as error:
            raise LoopbackServiceError("AUTHORIZATION_INVALID", 409) from error
        if not isinstance(auth, (UserAuthorization, RestoreAuthorization)) or (
            sha256(data),
            auth.authorization_hash,
            auth.schema_version,
            auth.operation_id,
        ) != (
            binding.authorization_bytes_sha256,
            binding.authorization_hash,
            binding.authorization_schema_version,
            binding.controlled_operation_id,
        ):
            raise LoopbackServiceError("AUTHORIZATION_BINDING_MISMATCH", 409)
        expected_mutations = (
            ("apply", "reconcile")
            if isinstance(auth, UserAuthorization)
            else ("restore", "reconcile")
        )
        if (
            binding.material_root != auth.material_root
            or binding.target_relative_path != auth.target_relative_path
            or binding.target_display != auth.target_relative_path
            or binding.target_path_sha256 != sha256(auth.target_path.encode("utf-8"))
            or binding.allowed_mutations != expected_mutations
        ):
            raise LoopbackServiceError("AUTHORIZATION_BINDING_MISMATCH", 409)
        material_root = Path(auth.material_root)
        if (
            material_root == self.work_root
            or material_root.is_relative_to(self.work_root)
            or self.work_root.is_relative_to(material_root)
        ):
            raise LoopbackServiceError("AUTHORIZATION_BINDING_MISMATCH", 409)
        return auth

    def _ledger(self, binding: OperationBinding) -> Path:
        return Path(self._verified_auth(binding).ledger_root)

    def _confirmation(self, action: Action, binding: OperationBinding) -> str:
        return f"{action.upper()} {binding.web_operation_id}"

    def _intent_path(self, operation_id: str, key_hash: str) -> Path:
        return (
            self.idempotency_dir / f"{self._storage_key(operation_id, key_hash)}.i.json"
        )

    def _result_path(self, operation_id: str, key_hash: str) -> Path:
        return (
            self.idempotency_dir / f"{self._storage_key(operation_id, key_hash)}.r.json"
        )

    @staticmethod
    def _storage_key(operation_id: str, key_hash: str) -> str:
        # Keep direct-child evidence paths comfortably below the legacy Win32
        # path limit.  The full identifiers remain hash-bound inside each
        # canonical record and are revalidated on load.
        return sha256(f"{operation_id}:{key_hash}".encode("ascii"))

    def _idempotency(
        self, operation_id: str, key_hash: str
    ) -> tuple[IdempotencyIntent | None, IdempotencyResult | None]:
        intent_path = self._intent_path(operation_id, key_hash)
        try:
            intent_path.lstat()
        except FileNotFoundError:
            return None, None
        except OSError as error:
            raise LoopbackServiceError("IDEMPOTENCY_RECORD_INVALID", 409) from error
        try:
            first = load_record(intent_path)
        except LoopbackRecordError as error:
            raise LoopbackServiceError(error.code, 409) from error
        second_path = self._result_path(operation_id, key_hash)
        try:
            second_path.lstat()
        except FileNotFoundError:
            second = None
        except OSError as error:
            raise LoopbackServiceError("IDEMPOTENCY_RECORD_INVALID", 409) from error
        else:
            try:
                second = load_record(second_path)
            except LoopbackRecordError as error:
                raise LoopbackServiceError(error.code, 409) from error
        if (
            not isinstance(first, IdempotencyIntent)
            or second is not None
            and not isinstance(second, IdempotencyResult)
        ):
            raise LoopbackServiceError("IDEMPOTENCY_RECORD_INVALID", 409)
        if second is not None and (
            second.key_sha256 != first.key_sha256
            or second.action != first.action
            or second.request_digest != first.request_digest
            or second.web_operation_id != first.web_operation_id
            or second.binding_hash != first.binding_hash
            or second.authorization_hash != first.authorization_hash
            or second.intent_hash != first.intent_hash
        ):
            raise LoopbackServiceError("IDEMPOTENCY_RECORD_INVALID", 409)
        return first, second

    def _record_count(self) -> int:
        return sum(1 for _ in self.idempotency_dir.glob("*.json"))
