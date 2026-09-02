"""Create-only bindings for the opt-in read-only local task workbench."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .material_workflow import (
    DraftSession,
    MaterialWorkflowError,
    ResultSession,
    load_external_session,
    publish_new_direct_child,
    serialize_session,
    verify_result,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

_HEX = r"^[0-9a-f]{64}$"
_MAX_RECORD = 16 * 1024


class LocalWorkspaceTaskError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TaskBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["v3-local-workspace-task-binding-v1"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-binding/v1"]
    task_id: str = Field(pattern=_HEX)
    ui_work_root: str
    ui_work_root_device: int = Field(ge=0)
    ui_work_root_inode: int = Field(ge=0)
    material_root: str
    draft_path: str
    draft_sha256: str = Field(pattern=_HEX)
    result_path: str
    result_sha256: str = Field(pattern=_HEX)
    artifact_kind: Literal["plan", "report", "readme"]
    task_label: str = Field(min_length=1, max_length=160)
    binding_hash: str = Field(pattern=_HEX)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_hash(domain: str, payload: object) -> str:
    return sha256(domain.encode("ascii") + b"\0" + canonical_json(payload))


def make_binding(**fields: Any) -> TaskBinding:
    fields.update(
        schema_version="v3-local-workspace-task-binding-v1",
        hash_domain="projecttown/v3/local-workspace-task-binding/v1",
    )
    payload = dict(fields)
    payload.pop("binding_hash", None)
    fields["binding_hash"] = record_hash(fields["hash_domain"], payload)
    return TaskBinding.model_validate(fields)


def serialize_binding(binding: TaskBinding) -> bytes:
    payload = binding.model_dump(mode="json")
    actual = payload.pop("binding_hash")
    if actual != record_hash(binding.hash_domain, payload):
        raise LocalWorkspaceTaskError("INVALID_BINDING")
    data = canonical_json(binding.model_dump(mode="json"))
    if len(data) > _MAX_RECORD:
        raise LocalWorkspaceTaskError("RECORD_LIMIT_EXCEEDED")
    return data


def parse_binding_bytes(data: bytes) -> TaskBinding:
    if not isinstance(data, bytes) or len(data) > _MAX_RECORD:
        raise LocalWorkspaceTaskError("INVALID_BINDING")
    try:

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate")
                value[key] = item
            return value

        raw = json.loads(data.decode("ascii"), object_pairs_hook=unique)
        if not isinstance(raw, dict):
            raise TypeError("object required")
        binding = TaskBinding.model_validate(raw)
    except (TypeError, ValueError, UnicodeDecodeError, ValidationError) as error:
        raise LocalWorkspaceTaskError("INVALID_BINDING") from error
    if data != serialize_binding(binding):
        raise LocalWorkspaceTaskError("INVALID_BINDING")
    return binding


def _safe_file(path: Path, code: str) -> bytes:
    try:
        metadata = path.lstat()
        parent = path.parent.lstat()
        if (
            path.resolve(strict=True) != path
            or path.parent.resolve(strict=True) != path.parent
            or not is_safe_directory(parent)
            or not stat.S_ISREG(metadata.st_mode)
            or is_reparse(metadata)
        ):
            raise OSError("unsafe")
        stable = read_stable_regular_file(
            path, metadata, capture_bytes=True, require_single_link=True
        )
        if stable is None or stable[2] is None:
            raise OSError("unstable")
        return stable[2]
    except OSError as error:
        raise LocalWorkspaceTaskError(code) from error


def _safe_root(path: Path, code: str) -> Path:
    try:
        canonical = path.resolve(strict=True)
        if canonical != path or not is_safe_directory(path.lstat()):
            raise OSError("unsafe")
        return canonical
    except OSError as error:
        raise LocalWorkspaceTaskError(code) from error


def load_binding(path: Path) -> TaskBinding:
    return parse_binding_bytes(_safe_file(path, "INVALID_BINDING"))


def publish_binding(ui_work_root: Path, binding: TaskBinding) -> None:
    root = _safe_root(ui_work_root, "PUBLICATION_FAILED")
    bindings = root / "bindings"
    _safe_root(bindings, "PUBLICATION_FAILED")
    destination = bindings / f"{binding.task_id}.json"
    try:
        publish_new_direct_child(bindings, destination, serialize_binding(binding))
    except MaterialWorkflowError as error:
        code = (
            "CREATE_ONLY_CONFLICT"
            if error.code == "INVALID_OUTPUT_PATH"
            else "PUBLICATION_FAILED"
        )
        raise LocalWorkspaceTaskError(code) from error
    if serialize_binding(load_binding(destination)) != serialize_binding(binding):
        raise LocalWorkspaceTaskError("PUBLICATION_ATTENTION")


def verify_binding(binding: TaskBinding, ui_work_root: Path) -> ResultSession:
    root = _safe_root(ui_work_root, "INVALID_WORK_ROOT")
    metadata = root.lstat()
    if binding.ui_work_root != str(root) or (
        binding.ui_work_root_device,
        binding.ui_work_root_inode,
    ) != (int(metadata.st_dev), int(metadata.st_ino)):
        raise LocalWorkspaceTaskError("WORK_ROOT_MISMATCH")
    material_root = _safe_root(Path(binding.material_root), "MATERIAL_ROOT_UNAVAILABLE")
    if (
        material_root == root
        or material_root.is_relative_to(root)
        or root.is_relative_to(material_root)
    ):
        raise LocalWorkspaceTaskError("ROOT_SEPARATION_REQUIRED")
    draft_raw = _safe_file(Path(binding.draft_path), "DRAFT_UNAVAILABLE")
    result_raw = _safe_file(Path(binding.result_path), "RESULT_UNAVAILABLE")
    if (
        sha256(draft_raw) != binding.draft_sha256
        or sha256(result_raw) != binding.result_sha256
    ):
        raise LocalWorkspaceTaskError("BINDING_TAMPERED")
    try:
        draft, result = (
            load_external_session(Path(binding.draft_path)),
            load_external_session(Path(binding.result_path)),
        )
    except MaterialWorkflowError as error:
        raise LocalWorkspaceTaskError("SESSION_INVALID") from error
    if (
        not isinstance(draft, DraftSession)
        or not isinstance(result, ResultSession)
        or serialize_session(draft) != serialize_session(result.draft)
        or result.draft.artifact_kind != binding.artifact_kind
        or not verify_result(material_root, result)
    ):
        raise LocalWorkspaceTaskError("MATERIAL_STALE_OR_MISMATCH")
    return result
