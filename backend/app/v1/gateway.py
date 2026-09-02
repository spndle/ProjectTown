"""Persistent, idempotent Tool Gateway for the v1 runtime."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from typing import Any, Protocol

from ..errors import AppError, ToolError
from ..runtime import stable_hash
from ..tools import Sandbox, ToolRegistry
from .models import ReceiptStatus

READ_ONLY_TOOLS = {
    "list_directory",
    "read_file",
    "check_markdown",
    "check_python_syntax",
}
KNOWN_NO_EFFECT_ERRORS = {
    "FILE_ALREADY_EXISTS",
    "FILE_TOO_LARGE",
    "FILE_TYPE_NOT_ALLOWED",
    "INVALID_PATH",
    "INVALID_TOOL_ARGUMENTS",
    "PATH_OUTSIDE_SANDBOX",
    "PATH_OUTSIDE_WORKSPACE",
}


class GatewayStore(Protocol):
    def prepare_action(
        self,
        action_id: str,
        quest_id: str,
        milestone_id: str,
        idempotency_key: str,
        tool_name: str,
        arguments_hash: str,
        arguments: Mapping[str, Any],
        expected_state_version: int,
        pre_effect_hash: str | None = None,
    ) -> dict[str, Any]: ...

    def mark_action_dispatched(self, action_id: str) -> dict[str, Any]: ...

    def commit_action(
        self, action_id: str, result: Mapping[str, Any]
    ) -> dict[str, Any]: ...

    def commit_action_with_event(
        self,
        action_id: str,
        result: Mapping[str, Any],
        *,
        file_observation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def fail_action(
        self, action_id: str, error: Mapping[str, Any]
    ) -> dict[str, Any]: ...

    def mark_action_unknown(
        self, action_id: str, error: Mapping[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def get_action(self, action_id: str) -> dict[str, Any] | None: ...


class InjectedFault(RuntimeError):
    def __init__(self, point: str) -> None:
        super().__init__(f"fault injected: {point}")
        self.point = point


class FaultInjector:
    """One-shot deterministic fault hook; disabled by default."""

    def __init__(self, points: set[str] | None = None) -> None:
        self.points = set(points or ())

    def hit(self, point: str) -> None:
        if point in self.points:
            self.points.remove(point)
            raise InjectedFault(point)


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        store: GatewayStore,
        *,
        sandbox: Sandbox | None = None,
        allowlist: set[str] | None = None,
        high_risk_tools: set[str] | None = None,
        read_only_tools: set[str] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.sandbox = sandbox or registry.sandbox
        self.allowlist = set(allowlist if allowlist is not None else registry.names)
        self.high_risk_tools = set(high_risk_tools or ())
        self.read_only_tools = set(read_only_tools or READ_ONLY_TOOLS)
        self.faults = fault_injector or FaultInjector()

    def execute(
        self,
        *,
        action_id: str,
        quest_id: str,
        milestone_id: str = "",
        idempotency_key: str,
        expected_state_version: int,
        workspace: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        approved: bool = False,
    ) -> dict[str, Any]:
        self._authorize(tool_name, approved)
        if not isinstance(arguments, Mapping):
            raise AppError(
                "INVALID_TOOL_ARGUMENTS",
                "Tool arguments must be an object",
                status_code=400,
            )
        args = dict(arguments)
        arguments_hash = stable_hash(args)
        pre_effect_hash = self._pre_effect_hash(workspace, tool_name, args)
        try:
            record = self.store.prepare_action(
                action_id,
                quest_id,
                milestone_id,
                idempotency_key,
                tool_name,
                arguments_hash,
                args,
                expected_state_version,
                pre_effect_hash,
            )
        except ValueError as exc:
            message = str(exc)
            code = (
                "IDEMPOTENCY_CONFLICT"
                if "idempotency" in message
                else "STATE_VERSION_CONFLICT"
            )
            raise AppError(code, message, status_code=409) from exc

        record = dict(record)
        record.setdefault("action_id", action_id)
        record.setdefault("tool_name", tool_name)
        record.setdefault("arguments_hash", arguments_hash)
        record.setdefault("pre_effect_hash", pre_effect_hash)
        if (
            record.get("arguments_hash") != arguments_hash
            or record.get("tool_name") != tool_name
        ):
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "Idempotency key was used with a different request",
                status_code=409,
                details={"action_id": record.get("action_id")},
            )

        status = self._status(record)
        if status in {
            ReceiptStatus.COMMITTED.value,
            ReceiptStatus.FAILED.value,
        }:
            return self._receipt(record)

        if status in {
            ReceiptStatus.DISPATCHED.value,
            ReceiptStatus.UNKNOWN_EFFECT.value,
        }:
            reconciled, safe_retry = self._reconcile(record, workspace, args)
            if reconciled is not None:
                return reconciled
            if not safe_retry:
                if status == ReceiptStatus.DISPATCHED.value:
                    record = self.store.mark_action_unknown(
                        str(record["action_id"]),
                        {
                            "code": "UNKNOWN_EFFECT",
                            "message": "Tool effect could not be reconciled",
                        },
                    )
                return self._receipt(record)

        return self._dispatch(record, workspace, tool_name, args)

    def _authorize(self, tool_name: str, approved: bool) -> None:
        if tool_name not in self.allowlist:
            raise AppError(
                "TOOL_NOT_ALLOWED",
                f"Tool '{tool_name}' is not allowlisted",
                status_code=403,
            )
        if tool_name in self.high_risk_tools and approved is not True:
            raise AppError(
                "APPROVAL_REQUIRED",
                f"Tool '{tool_name}' requires explicit approval",
                status_code=403,
            )

    def _dispatch(
        self,
        record: Mapping[str, Any],
        workspace: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        action_id = str(record["action_id"])
        status = self._status(record)
        try:
            self.faults.hit("before_dispatch")
        except InjectedFault as exc:
            return self._receipt(
                self.store.fail_action(
                    action_id,
                    {"code": "DISPATCH_FAILED", "message": str(exc)},
                )
            )

        if status == ReceiptStatus.PREPARED.value:
            self.store.mark_action_dispatched(action_id)
        try:
            result = self.registry.execute(tool_name, workspace, arguments)
            self.faults.hit("after_effect_before_receipt")
            if "malformed_result" in self.faults.points:
                self.faults.points.remove("malformed_result")
                result = None
            if not isinstance(result, Mapping):
                raise ToolError("MALFORMED_RESULT", "Tool returned a non-object result")
            observation = self._write_file_observation(record, workspace, arguments)
            return self._receipt(self._commit(record, dict(result), observation))
        except InjectedFault as exc:
            return self._receipt(
                self.store.mark_action_unknown(
                    action_id,
                    {"code": "UNKNOWN_EFFECT", "message": str(exc)},
                )
            )
        except ToolError as exc:
            if tool_name in self.read_only_tools or exc.code in KNOWN_NO_EFFECT_ERRORS:
                return self._receipt(self.store.fail_action(action_id, exc.as_dict()))
            return self._receipt(
                self.store.mark_action_unknown(action_id, exc.as_dict())
            )
        except Exception as exc:  # noqa: BLE001 - external tool boundary
            error = {
                "code": "TOOL_EXECUTION_INTERRUPTED",
                "message": "Tool outcome is ambiguous",
                "details": {"exception_type": type(exc).__name__},
            }
            if tool_name in self.read_only_tools:
                return self._receipt(self.store.fail_action(action_id, error))
            return self._receipt(self.store.mark_action_unknown(action_id, error))

    def _pre_effect_hash(
        self,
        workspace: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> str | None:
        if tool_name != "write_file":
            return None
        relative_path = arguments.get("path")
        if not isinstance(relative_path, str):
            return None
        path = self.sandbox.resolve(workspace, relative_path, must_exist=False)
        if not path.exists():
            return "absent"
        if not path.is_file():
            return "not_file"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _reconcile(
        self,
        record: Mapping[str, Any],
        workspace: str,
        arguments: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        tool_name = str(record.get("tool_name", ""))
        if tool_name in self.read_only_tools:
            return None, True
        if tool_name != "write_file" or not isinstance(arguments.get("content"), str):
            return None, False

        relative_path = arguments.get("path")
        if not isinstance(relative_path, str):
            return None, False
        path = self.sandbox.resolve(workspace, relative_path, must_exist=False)
        pre_effect_hash = record.get("pre_effect_hash")
        if not path.exists():
            return None, pre_effect_hash == "absent"
        if not path.is_file():
            return None, False
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        content = str(arguments["content"])
        expected_bytes = content.encode("utf-8")
        expected_hash = hashlib.sha256(expected_bytes).hexdigest()
        if current_hash != expected_hash:
            return None, False
        if arguments.get("overwrite", True) is False and pre_effect_hash != "absent":
            return None, False
        try:
            observation = self._write_file_observation(record, workspace, arguments)
        except ToolError:
            return None, False
        committed = self._commit(
            record,
            {
                "path": self.sandbox.display_path(workspace, path),
                "size_bytes": len(content.encode("utf-8")),
                "created": pre_effect_hash == "absent",
                "reconciled": True,
            },
            observation,
        )
        return self._receipt(committed), False

    def _commit(
        self,
        record: Mapping[str, Any],
        result: Mapping[str, Any],
        file_observation: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Commit through the store's required atomic receipt interface."""

        action_id = str(record["action_id"])
        if file_observation is None:
            return self.store.commit_action_with_event(action_id, result)
        return self.store.commit_action_with_event(
            action_id,
            result,
            file_observation=file_observation,
        )

    @staticmethod
    def _same_file(before: object, after: object) -> bool:
        return (
            before.st_mode == after.st_mode
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
            and before.st_ino == after.st_ino
            and before.st_dev == after.st_dev
        )

    @staticmethod
    def _same_opened_regular_file(
        path_metadata: object, opened_metadata: object
    ) -> bool:
        """Compare path and descriptor views without Windows open-time ctime drift."""
        return (
            path_metadata.st_mode == opened_metadata.st_mode
            and path_metadata.st_size == opened_metadata.st_size
            and path_metadata.st_mtime_ns == opened_metadata.st_mtime_ns
            and path_metadata.st_ino == opened_metadata.st_ino
            and path_metadata.st_dev == opened_metadata.st_dev
        )

    @staticmethod
    def _safe_regular_metadata(metadata: object) -> bool:
        return (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and not bool(getattr(metadata, "st_file_attributes", 0) & 0x0400)
        )

    def _write_file_observation(
        self,
        record: Mapping[str, Any],
        workspace: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if str(record.get("tool_name", "")) != "write_file":
            return None
        relative_path = arguments.get("path")
        if not isinstance(relative_path, str):
            raise ToolError("UNSAFE_FILE_OBSERVATION", "Write path cannot be observed")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolError(
                "UNSAFE_FILE_OBSERVATION", "Write content cannot be observed"
            )
        expected_bytes = content.encode("utf-8")
        expected_hash = hashlib.sha256(expected_bytes).hexdigest()
        path = self.sandbox.resolve(
            workspace,
            relative_path,
            must_exist=True,
            create_workspace=False,
        )
        for _attempt in range(2):
            descriptor: int | None = None
            close_failed = False
            try:
                before = path.lstat()
                if not self._safe_regular_metadata(before):
                    break
                open_flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    open_flags |= os.O_NOFOLLOW
                if hasattr(os, "O_BINARY"):
                    open_flags |= os.O_BINARY
                descriptor = os.open(path, open_flags)
                opened_before = os.fstat(descriptor)
                if not self._safe_regular_metadata(
                    opened_before
                ) or not self._same_opened_regular_file(before, opened_before):
                    continue

                payload_parts: list[bytes] = []
                remaining = len(expected_bytes) + 1
                while remaining:
                    chunk = os.read(descriptor, remaining)
                    if not chunk:
                        break
                    payload_parts.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(payload_parts)
                opened_after = os.fstat(descriptor)
                after = path.lstat()
            except OSError:
                continue
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        close_failed = True
            if close_failed:
                continue
            if (
                self._safe_regular_metadata(opened_after)
                and self._safe_regular_metadata(after)
                and self._same_file(opened_before, opened_after)
                and self._same_file(before, after)
                and self._same_opened_regular_file(after, opened_after)
            ):
                after_hash = hashlib.sha256(payload).hexdigest()
                if after_hash != expected_hash:
                    raise ToolError(
                        "POST_WRITE_HASH_MISMATCH",
                        "Written file bytes do not match the requested content",
                        details={"path": relative_path},
                    )
                before_hash = record.get("pre_effect_hash")
                before_digest = (
                    before_hash
                    if isinstance(before_hash, str)
                    and len(before_hash) == 64
                    and all(char in "0123456789abcdef" for char in before_hash)
                    else None
                )
                if before_hash == "absent":
                    change_kind = "created"
                elif before_digest == after_hash:
                    change_kind = "unchanged"
                else:
                    change_kind = "modified"
                return {
                    "observation_id": stable_hash(
                        {"action_id": str(record["action_id"]), "schema": 1}
                    ),
                    "relative_path": self.sandbox.display_path(workspace, path),
                    "before_sha256": before_digest,
                    "after_sha256": after_hash,
                    "after_size_bytes": len(payload),
                    "change_kind": change_kind,
                    "status": "observed",
                }
        raise ToolError(
            "UNSAFE_FILE_OBSERVATION",
            "Written file is missing, unsafe, or unstable for observation",
            details={"path": relative_path},
        )

    @staticmethod
    def _status(record: Mapping[str, Any]) -> str:
        raw_status = record.get("status", ReceiptStatus.PREPARED.value)
        return (
            raw_status.value
            if isinstance(raw_status, ReceiptStatus)
            else str(raw_status)
        )

    @classmethod
    def _receipt(cls, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "action_id": record.get("action_id"),
            "idempotency_key": record.get("idempotency_key"),
            "status": cls._status(record),
            "result": record.get("result"),
            "error": record.get("error"),
        }


Gateway = ToolGateway
