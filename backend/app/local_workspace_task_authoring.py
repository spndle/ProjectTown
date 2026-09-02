"""Bounded, offline Phase 4B authoring records.

This module deliberately has no HTTP, subprocess, Apply, or restore surface.
It turns opaque catalog ids into explicit material-workflow selections and keeps
every mutation in a create-only work root.  The records are independently
versioned so the read-only v1 workspace binding remains byte-for-byte intact.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .material_workflow import (
    DraftSession,
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    ResultSession,
    create_draft,
    generate_result,
    load_external_session,
    publish_new_direct_child,
    render_export,
    render_pdf_export,
    serialize_session,
    verify_result,
)
from .materials import DEFAULT_POLICY, SUPPORTED_SUFFIXES, inspect_material_set
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

_HEX = r"^[0-9a-f]{64}$"
_ID = r"^[a-z][a-z0-9-]{0,63}$"
_MAX_RECORD = 128 * 1024
_MAX_CATALOG_DEPTH = 8
_MAX_VISITED_ENTRIES = 512
_MAX_TASKS = 64
_AUTHORING_BINDINGS = "authoring-bindings"
_DIRECTORIES = (
    "catalogs",
    "requests",
    "intents",
    "receipts",
    "drafts",
    "results",
    "exports",
    # v1 LocalWorkspaceTaskService owns ``bindings`` exclusively.  v2
    # authoring records never share that parser or directory.
    "bindings",
    _AUTHORING_BINDINGS,
)
_LOCK_GUARD = threading.Lock()
_TASK_LOCKS: dict[str, threading.Lock] = {}


class AuthoringError(ValueError):
    """Stable errors; codes intentionally do not contain local paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CatalogEntry(_Record):
    source_id: str = Field(pattern=_HEX)
    relative_path: str = Field(min_length=1, max_length=1024)
    suffix: str
    size_bytes: int = Field(ge=1)

    @field_validator("relative_path")
    @classmethod
    def canonical_relative_path(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or "\\" in value
            or ":" in value
            or value.startswith("/")
            or "\x00" in value
        ):
            raise ValueError("invalid relative path")
        parts = value.split("/")
        if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            raise ValueError("invalid relative path")
        return value


class CatalogV1(_Record):
    schema_version: Literal["v3-local-workspace-task-catalog-v1"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-catalog/v1"]
    catalog_id: str = Field(pattern=_HEX)
    material_root: str
    material_root_device: int = Field(ge=0)
    material_root_inode: int = Field(ge=0)
    root_hash: str = Field(pattern=_HEX)
    entries: tuple[CatalogEntry, ...] = Field(min_length=1)
    catalog_hash: str = Field(pattern=_HEX)


class AuthoringRequestV1(_Record):
    schema_version: Literal["v3-local-workspace-task-request-v1"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-request/v1"]
    request_id: str = Field(pattern=_HEX)
    task_id: str = Field(pattern=_ID)
    operation: Literal["draft", "generate", "export-markdown", "export-pdf"]
    idempotency_key_hash: str = Field(pattern=_HEX)
    payload: dict[str, Any]
    payload_digest: str = Field(pattern=_HEX)
    request_hash: str = Field(pattern=_HEX)


class AuthoringIntentV1(_Record):
    schema_version: Literal["v3-local-workspace-task-intent-v1"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-intent/v1"]
    intent_id: str = Field(pattern=_HEX)
    task_id: str = Field(pattern=_ID)
    operation: Literal["draft", "generate", "export-markdown", "export-pdf"]
    request_hash: str = Field(pattern=_HEX)
    parent_hash: str | None = Field(default=None, pattern=_HEX)
    output_slot: str = Field(pattern=r"^[a-z0-9-]+\.(json|md|pdf)$")
    intent_hash: str = Field(pattern=_HEX)


class AuthoringReceiptV1(_Record):
    schema_version: Literal["v3-local-workspace-task-receipt-v1"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-receipt/v1"]
    receipt_id: str = Field(pattern=_HEX)
    task_id: str = Field(pattern=_ID)
    operation: Literal["draft", "generate", "export-markdown", "export-pdf"]
    intent_hash: str = Field(pattern=_HEX)
    output_slot: str = Field(pattern=r"^[a-z0-9-]+\.(json|md|pdf)$")
    output_sha256: str = Field(pattern=_HEX)
    output_schema: str
    output_size_bytes: int = Field(ge=0)
    outcome: Literal["completed"]
    publication_state: Literal["published"]
    receipt_hash: str = Field(pattern=_HEX)


class AuthoringBindingV2(_Record):
    schema_version: Literal["v3-local-workspace-task-binding-v2"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-binding/v2"]
    task_id: str = Field(pattern=_ID)
    work_root: str
    work_root_device: int = Field(ge=0)
    work_root_inode: int = Field(ge=0)
    material_root: str
    material_root_device: int = Field(ge=0)
    material_root_inode: int = Field(ge=0)
    catalog_hash: str = Field(pattern=_HEX)
    draft_hash: str = Field(pattern=_HEX)
    draft_bytes_sha256: str = Field(pattern=_HEX)
    draft_request_hash: str = Field(pattern=_HEX)
    draft_intent_hash: str = Field(pattern=_HEX)
    draft_receipt_hash: str = Field(pattern=_HEX)
    result_hash: str = Field(pattern=_HEX)
    result_bytes_sha256: str = Field(pattern=_HEX)
    generate_request_hash: str = Field(pattern=_HEX)
    generate_intent_hash: str = Field(pattern=_HEX)
    generate_receipt_hash: str = Field(pattern=_HEX)
    artifact_kind: Literal["plan", "report", "readme"]
    binding_hash: str = Field(pattern=_HEX)


class AuthoringState(_Record):
    task_id: str = Field(pattern=_ID)
    state: Literal[
        "cataloged", "waiting_confirmation", "generated", "exported", "attention"
    ]
    draft_hash: str | None = Field(default=None, pattern=_HEX)
    result_hash: str | None = Field(default=None, pattern=_HEX)
    exports: tuple[str, ...] = ()


def _json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(domain: str, payload: Mapping[str, Any]) -> str:
    return _sha(domain.encode("ascii") + b"\0" + _json(payload))


def _record(
    model: type[_Record], domain: str, hash_name: str, **fields: Any
) -> _Record:
    fields.update(
        schema_version=model.model_fields["schema_version"].annotation.__args__[0],
        hash_domain=domain,
    )
    payload = dict(fields)
    payload.pop(hash_name, None)
    fields[hash_name] = _hash(domain, payload)
    try:
        return model.model_validate(fields)
    except ValidationError as error:
        raise AuthoringError("INVALID_RECORD") from error


def _serialize(record: _Record, hash_name: str) -> bytes:
    data = record.model_dump(mode="json")
    actual = data.pop(hash_name)
    if actual != _hash(record.hash_domain, data):
        raise AuthoringError("RECORD_TAMPERED")
    encoded = _json(record.model_dump(mode="json"))
    if len(encoded) > _MAX_RECORD:
        raise AuthoringError("RECORD_LIMIT_EXCEEDED")
    return encoded


def _parse(data: bytes, model: type[_Record], hash_name: str) -> _Record:
    if not isinstance(data, bytes) or len(data) > _MAX_RECORD:
        raise AuthoringError("INVALID_RECORD")
    try:
        raw = json.loads(data.decode("ascii"), object_pairs_hook=_unique)
        # Canonical JSON represents tuples as arrays; strict record models keep
        # tuple semantics in memory, so restore only the declared tuple field.
        if (
            model is CatalogV1
            and isinstance(raw, dict)
            and isinstance(raw.get("entries"), list)
        ):
            raw["entries"] = tuple(raw["entries"])
        parsed = model.model_validate(raw)
    except (TypeError, ValueError, UnicodeDecodeError, ValidationError) as error:
        raise AuthoringError("INVALID_RECORD") from error
    if data != _serialize(parsed, hash_name):
        raise AuthoringError("NONCANONICAL_RECORD")
    return parsed


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def serialize_catalog(value: CatalogV1) -> bytes:
    return _serialize(value, "catalog_hash")


def parse_catalog_bytes(data: bytes) -> CatalogV1:
    return _parse(data, CatalogV1, "catalog_hash")  # type: ignore[return-value]


def serialize_request(value: AuthoringRequestV1) -> bytes:
    return _serialize(value, "request_hash")


def parse_request_bytes(data: bytes) -> AuthoringRequestV1:
    return _parse(data, AuthoringRequestV1, "request_hash")  # type: ignore[return-value]


def serialize_intent(value: AuthoringIntentV1) -> bytes:
    return _serialize(value, "intent_hash")


def parse_intent_bytes(data: bytes) -> AuthoringIntentV1:
    return _parse(data, AuthoringIntentV1, "intent_hash")  # type: ignore[return-value]


def serialize_receipt(value: AuthoringReceiptV1) -> bytes:
    return _serialize(value, "receipt_hash")


def parse_receipt_bytes(data: bytes) -> AuthoringReceiptV1:
    return _parse(data, AuthoringReceiptV1, "receipt_hash")  # type: ignore[return-value]


def serialize_authoring_binding(value: AuthoringBindingV2) -> bytes:
    return _serialize(value, "binding_hash")


def parse_authoring_binding_bytes(data: bytes) -> AuthoringBindingV2:
    return _parse(data, AuthoringBindingV2, "binding_hash")  # type: ignore[return-value]


def _root(path: Path, code: str) -> tuple[Path, os.stat_result]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise AuthoringError(code)
    try:
        meta = path.lstat()
        if path.resolve(strict=True) != path or not is_safe_directory(meta):
            raise OSError("unsafe")
    except OSError as error:
        raise AuthoringError(code) from error
    return path, meta


def initialize_work_root(work_root: Path, material_root: Path) -> None:
    """Create the fixed record directories once; roots must be disjoint."""
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    material, _ = _root(material_root, "INVALID_MATERIAL_ROOT")
    if (
        work == material
        or work.is_relative_to(material)
        or material.is_relative_to(work)
    ):
        raise AuthoringError("ROOT_SEPARATION_REQUIRED")
    for name in _DIRECTORIES:
        directory = work / name
        if directory.exists():
            _root(directory, "INVALID_WORK_ROOT")
            continue
        try:
            directory.mkdir()
        except OSError as error:
            raise AuthoringError("WORK_ROOT_INITIALIZATION_FAILED") from error
        _root(directory, "WORK_ROOT_INITIALIZATION_FAILED")


def _safe_read(path: Path, code: str) -> bytes:
    try:
        meta, parent = path.lstat(), path.parent.lstat()
        if (
            path.resolve(strict=True) != path
            or not stat.S_ISREG(meta.st_mode)
            or is_reparse(meta)
            or not is_safe_directory(parent)
            or meta.st_nlink != 1
        ):
            raise OSError("unsafe")
        stable = read_stable_regular_file(
            path, meta, capture_bytes=True, require_single_link=True
        )
        if stable is None or stable[2] is None:
            raise OSError("unstable")
        return stable[2]
    except OSError as error:
        raise AuthoringError(code) from error


def _publish(work: Path, directory: str, name: str, data: bytes) -> None:
    try:
        publish_new_direct_child(work / directory, work / directory / name, data)
    except PublicationRollbackError as error:
        raise AuthoringError("PUBLICATION_ROLLED_BACK") from error
    except PublicationAttentionError as error:
        raise AuthoringError("COMMITTED_NEEDS_ATTENTION") from error
    except MaterialWorkflowError as error:
        raise AuthoringError(
            "CREATE_ONLY_CONFLICT"
            if error.code == "INVALID_OUTPUT_PATH"
            else "PUBLICATION_FAILED"
        ) from error


def _safe_relative(relative: str) -> str:
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or ":" in relative
        or relative.startswith("/")
        or "\x00" in relative
    ):
        raise AuthoringError("INVALID_SOURCE_ID")
    parts = relative.split("/")
    if any(
        part in {"", ".", ".."} or part.startswith((".", ".secrets", ".env"))
        for part in parts
    ):
        raise AuthoringError("UNSAFE_SOURCE")
    return relative


def _walk_catalog(root: Path) -> tuple[str, ...]:
    """Bounded deterministic safe scan. It never returns local paths to callers."""
    _root(root, "INVALID_MATERIAL_ROOT")
    entries: list[str] = []
    seen = 0
    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    while stack:
        directory, parts = stack.pop()
        if len(parts) > _MAX_CATALOG_DEPTH:
            raise AuthoringError("CATALOG_DEPTH_LIMIT")
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise AuthoringError("CATALOG_UNAVAILABLE") from error
        for child in children:
            seen += 1
            if seen > _MAX_VISITED_ENTRIES:
                raise AuthoringError("CATALOG_ENTRY_LIMIT")
            try:
                meta = child.lstat()
            except OSError as error:
                raise AuthoringError("CATALOG_UNAVAILABLE") from error
            if child.name.startswith(".") or is_reparse(meta):
                raise AuthoringError("UNSAFE_SOURCE")
            child_parts = parts + (child.name,)
            if stat.S_ISDIR(meta.st_mode):
                stack.append((child, child_parts))
            elif stat.S_ISREG(meta.st_mode):
                if meta.st_nlink != 1 or child.suffix.lower() not in SUPPORTED_SUFFIXES:
                    raise AuthoringError("UNSAFE_SOURCE")
                entries.append("/".join(child_parts))
            else:
                raise AuthoringError("UNSAFE_SOURCE")
    if not entries:
        raise AuthoringError("EMPTY_CATALOG")
    return tuple(sorted(entries))


def build_catalog(material_root: Path) -> CatalogV1:
    root, meta = _root(material_root, "INVALID_MATERIAL_ROOT")
    paths = _walk_catalog(root)
    manifest = inspect_material_set(root, paths, policy=DEFAULT_POLICY)
    if manifest.status != "complete" or manifest.root_hash is None:
        raise AuthoringError("CATALOG_INCOMPLETE")
    entries = tuple(
        CatalogEntry(
            source_id=_sha(
                (manifest.root_hash + "\0" + item.relative_path).encode("utf-8")
            ),
            relative_path=item.relative_path,
            suffix=item.suffix,
            size_bytes=item.size_bytes,
        )
        for item in manifest.entries
    )
    payload = {
        "material_root": str(root),
        "material_root_device": int(meta.st_dev),
        "material_root_inode": int(meta.st_ino),
        "root_hash": manifest.root_hash,
        "entries": tuple(entry.model_dump(mode="json") for entry in entries),
    }
    catalog_id = _hash("projecttown/v3/local-workspace-task-catalog-id/v1", payload)
    return _record(
        CatalogV1,
        "projecttown/v3/local-workspace-task-catalog/v1",
        "catalog_hash",
        catalog_id=catalog_id,
        **payload,
    )  # type: ignore[return-value]


def publish_catalog(work_root: Path, catalog: CatalogV1) -> None:
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    _publish(work, "catalogs", f"{catalog.catalog_id}.json", serialize_catalog(catalog))


def load_catalog(work_root: Path, catalog_id: str) -> CatalogV1:
    """Load one catalog through the stable single-link record path."""
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    if not isinstance(catalog_id, str) or not re.fullmatch(_HEX, catalog_id):
        raise AuthoringError("INVALID_CATALOG")
    value = parse_catalog_bytes(
        _safe_read(work / "catalogs" / f"{catalog_id}.json", "INVALID_CATALOG")
    )
    if value.catalog_id != catalog_id:
        raise AuthoringError("CATALOG_TAMPERED")
    return value


def publish_or_load_catalog(work_root: Path, catalog: CatalogV1) -> CatalogV1:
    """Create-only publish with exact-byte replay, never a path-level TOCTOU."""
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    expected = serialize_catalog(catalog)
    try:
        existing = load_catalog(work, catalog.catalog_id)
    except AuthoringError as error:
        if error.code != "INVALID_CATALOG":
            raise
        try:
            publish_catalog(work, catalog)
        except AuthoringError as publication_error:
            if publication_error.code != "CREATE_ONLY_CONFLICT":
                raise
        existing = load_catalog(work, catalog.catalog_id)
    if serialize_catalog(existing) != expected:
        raise AuthoringError("CATALOG_TAMPERED")
    return existing


def _task_lock(task_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        return _TASK_LOCKS.setdefault(task_id, threading.Lock())


def _validated_task(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(_ID, value):
        raise AuthoringError("INVALID_TASK_ID")
    return value


def _request(
    task_id: str, operation: str, idempotency_key: str, payload: Mapping[str, Any]
) -> AuthoringRequestV1:
    _validated_task(task_id)
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key
        or len(idempotency_key) > 128
    ):
        raise AuthoringError("INVALID_IDEMPOTENCY_KEY")
    payload = json.loads(_json(payload).decode("ascii"))
    digest = _sha(_json(payload))
    key_hash = _sha(idempotency_key.encode("utf-8"))
    return _record(
        AuthoringRequestV1,
        "projecttown/v3/local-workspace-task-request/v1",
        "request_hash",
        request_id=_hash(
            "projecttown/v3/local-workspace-task-request-id/v1",
            {
                "task_id": task_id,
                "operation": operation,
                "idempotency_key_hash": key_hash,
                "payload_digest": digest,
            },
        ),
        task_id=task_id,
        operation=operation,
        idempotency_key_hash=key_hash,
        payload=payload,
        payload_digest=digest,
    )  # type: ignore[return-value]


def _idempotent_request(
    work: Path, request: AuthoringRequestV1
) -> AuthoringRequestV1 | None:
    """Returns same request on exact replay, conflicts on a reused key."""
    directory = work / "requests"
    for path in directory.glob("*.json"):
        try:
            existing = parse_request_bytes(_safe_read(path, "INVALID_REQUEST"))
        except AuthoringError:
            raise AuthoringError("ATTENTION") from None
        if (
            existing.task_id == request.task_id
            and existing.operation == request.operation
            and existing.idempotency_key_hash == request.idempotency_key_hash
        ):
            if (
                existing.payload_digest != request.payload_digest
                or existing.payload != request.payload
            ):
                raise AuthoringError("IDEMPOTENCY_CONFLICT")
            return existing
    return None


def _publish_request(work: Path, request: AuthoringRequestV1) -> None:
    _publish(work, "requests", f"{request.request_id}.json", serialize_request(request))


def _intent(
    work: Path,
    request: AuthoringRequestV1,
    operation: str,
    parent_hash: str | None,
    slot: str,
) -> AuthoringIntentV1:
    value = _record(
        AuthoringIntentV1,
        "projecttown/v3/local-workspace-task-intent/v1",
        "intent_hash",
        intent_id=_hash(
            "projecttown/v3/local-workspace-task-intent-id/v1",
            {"request_hash": request.request_hash, "slot": slot},
        ),
        task_id=request.task_id,
        operation=operation,
        request_hash=request.request_hash,
        parent_hash=parent_hash,
        output_slot=slot,
    )
    _publish(work, "intents", f"{value.intent_id}.json", serialize_intent(value))
    return value  # type: ignore[return-value]


def _receipt(
    work: Path, intent: AuthoringIntentV1, data: bytes, schema: str
) -> AuthoringReceiptV1:
    value = _record(
        AuthoringReceiptV1,
        "projecttown/v3/local-workspace-task-receipt/v1",
        "receipt_hash",
        receipt_id=_hash(
            "projecttown/v3/local-workspace-task-receipt-id/v1",
            {"intent_hash": intent.intent_hash, "sha": _sha(data)},
        ),
        task_id=intent.task_id,
        operation=intent.operation,
        intent_hash=intent.intent_hash,
        output_slot=intent.output_slot,
        output_sha256=_sha(data),
        output_schema=schema,
        output_size_bytes=len(data),
        outcome="completed",
        publication_state="published",
    )
    _publish(work, "receipts", f"{value.receipt_id}.json", serialize_receipt(value))
    return value  # type: ignore[return-value]


def _one_by_hash(
    work: Path, directory: str, parser: Any, field: str, value: str
) -> Any:
    found: Any | None = None
    for path in (work / directory).glob("*.json"):
        record = parser(_safe_read(path, "ATTENTION"))
        if getattr(record, field) == value:
            if found is not None:
                raise AuthoringError("ATTENTION")
            found = record
    if found is None:
        raise AuthoringError("ATTENTION")
    return found


def _verify_receipt_chain(
    work: Path,
    receipt: AuthoringReceiptV1,
    *,
    task_id: str,
    operation: str,
    slot: str,
    data: bytes,
) -> tuple[AuthoringIntentV1, AuthoringRequestV1]:
    if (
        receipt.task_id != task_id
        or receipt.operation != operation
        or receipt.output_slot != slot
        or receipt.outcome != "completed"
        or receipt.publication_state != "published"
        or receipt.output_sha256 != _sha(data)
        or receipt.output_size_bytes != len(data)
    ):
        raise AuthoringError("ATTENTION")
    intent = _one_by_hash(
        work, "intents", parse_intent_bytes, "intent_hash", receipt.intent_hash
    )
    request = _one_by_hash(
        work, "requests", parse_request_bytes, "request_hash", intent.request_hash
    )
    if (
        intent.task_id != task_id
        or intent.operation != operation
        or intent.output_slot != slot
        or request.task_id != task_id
        or request.operation != operation
        or request.request_hash != intent.request_hash
    ):
        raise AuthoringError("ATTENTION")
    return intent, request


def _catalog_for_hash(work: Path, catalog_hash: str) -> CatalogV1:
    return _one_by_hash(
        work, "catalogs", parse_catalog_bytes, "catalog_hash", catalog_hash
    )


def _known_task_ids(work: Path) -> set[str]:
    task_ids: set[str] = set()
    for directory, parser in (
        ("requests", parse_request_bytes),
        ("intents", parse_intent_bytes),
        (_AUTHORING_BINDINGS, parse_authoring_binding_bytes),
    ):
        for path in (work / directory).glob("*.json"):
            try:
                task_ids.add(parser(_safe_read(path, "ATTENTION")).task_id)
            except AuthoringError:
                raise AuthoringError("ATTENTION") from None
    return task_ids


def _receipt_for_intent(work: Path, intent_hash: str) -> AuthoringReceiptV1:
    found: AuthoringReceiptV1 | None = None
    for path in (work / "receipts").glob("*.json"):
        receipt = parse_receipt_bytes(_safe_read(path, "ATTENTION"))
        if receipt.intent_hash == intent_hash:
            if found is not None:
                raise AuthoringError("ATTENTION")
            found = receipt
    if found is None:
        raise AuthoringError("ATTENTION")
    return found


def _catalog_paths(
    material: Path, catalog: CatalogV1, source_ids: Sequence[str]
) -> tuple[str, ...]:
    root, meta = _root(material, "INVALID_MATERIAL_ROOT")
    if str(root) != catalog.material_root or (int(meta.st_dev), int(meta.st_ino)) != (
        catalog.material_root_device,
        catalog.material_root_inode,
    ):
        raise AuthoringError("MATERIAL_ROOT_MISMATCH")
    fresh = build_catalog(root)
    if fresh.catalog_hash != catalog.catalog_hash:
        raise AuthoringError("MATERIAL_STALE_OR_MISMATCH")
    mapping = {
        entry.source_id: path
        for entry, path in zip(catalog.entries, _walk_catalog(root), strict=True)
    }
    if (
        not source_ids
        or len(set(source_ids)) != len(source_ids)
        or any(item not in mapping for item in source_ids)
    ):
        raise AuthoringError("INVALID_SOURCE_ID")
    return tuple(sorted(mapping[item] for item in source_ids))


def create_authoring_draft(
    work_root: Path,
    material_root: Path,
    catalog: CatalogV1,
    *,
    task_id: str,
    task: str,
    artifact_kind: Literal["plan", "report", "readme"],
    source_ids: Sequence[str],
    idempotency_key: str,
    readme_target_id: str | None = None,
    constraints: Mapping[str, str] | None = None,
) -> DraftSession:
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    _validated_task(task_id)
    with _task_lock(task_id):
        payload = {
            "catalog_hash": catalog.catalog_hash,
            "task": task,
            "artifact_kind": artifact_kind,
            "source_ids": tuple(sorted(source_ids)),
            "readme_target_id": readme_target_id,
            "constraints": dict(constraints or {}),
        }
        request = _request(task_id, "draft", idempotency_key, payload)
        replay = _idempotent_request(work, request)
        target_paths = _catalog_paths(material_root, catalog, source_ids)
        if replay is not None:
            try:
                item = load_external_session(work / "drafts" / f"{task_id}-draft.json")
            except MaterialWorkflowError as error:
                raise AuthoringError("ATTENTION") from error
            intent = _matching_intent(work, task_id, "draft", f"{task_id}-draft.json")
            if intent is not None and isinstance(item, DraftSession):
                receipt = _receipt_for_intent(work, intent.intent_hash)
                _verify_receipt_chain(
                    work,
                    receipt,
                    task_id=task_id,
                    operation="draft",
                    slot=intent.output_slot,
                    data=serialize_session(item),
                )
                if intent.parent_hash != catalog.catalog_hash or not _draft_is_fresh(
                    material_root, item
                ):
                    raise AuthoringError("ATTENTION")
                return item
            raise AuthoringError("ATTENTION")
        if (
            task_id not in _known_task_ids(work)
            and len(_known_task_ids(work)) >= _MAX_TASKS
        ):
            raise AuthoringError("TASK_CAPACITY_EXCEEDED")
        _publish_request(work, request)
        readme_target = None
        if artifact_kind == "readme":
            if readme_target_id is None:
                raise AuthoringError("INVALID_SOURCE_ID")
            paths = _catalog_paths(material_root, catalog, [readme_target_id])
            readme_target = paths[0]
            if not readme_target.endswith(".md") or readme_target not in target_paths:
                raise AuthoringError("INVALID_README_TARGET")
        intent = _intent(
            work, request, "draft", catalog.catalog_hash, f"{task_id}-draft.json"
        )
        try:
            draft = create_draft(
                material_root,
                target_paths,
                task=task,
                artifact_kind=artifact_kind,
                readme_target=readme_target,
                constraints=constraints,
                generator_version="deterministic-grounded-plan-v2",
            )
        except MaterialWorkflowError as error:
            raise AuthoringError(error.code) from error
        data = serialize_session(draft)
        _publish(work, "drafts", intent.output_slot, data)
        _receipt(work, intent, data, draft.schema_version)
        return draft


def _confirmation_phrase(contract_hash: str) -> str:
    return f"CONFIRM {contract_hash}"


def confirm_and_generate(
    work_root: Path,
    material_root: Path,
    *,
    task_id: str,
    confirmation_phrase: str,
    idempotency_key: str,
) -> ResultSession:
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    _validated_task(task_id)
    with _task_lock(task_id):
        try:
            draft = load_external_session(work / "drafts" / f"{task_id}-draft.json")
        except MaterialWorkflowError as error:
            raise AuthoringError("DRAFT_UNAVAILABLE") from error
        if not isinstance(
            draft, DraftSession
        ) or confirmation_phrase != _confirmation_phrase(draft.contract_hash):
            raise AuthoringError("INVALID_CONFIRMATION")
        payload = {
            "draft_hash": draft.session_hash,
            "confirmation_hash": draft.contract_hash,
        }
        request = _request(task_id, "generate", idempotency_key, payload)
        replay = _idempotent_request(work, request)
        if replay is not None:
            item = load_external_session(work / "results" / f"{task_id}-result.json")
            intent = _matching_intent(
                work, task_id, "generate", f"{task_id}-result.json"
            )
            if intent is not None and isinstance(item, ResultSession):
                receipt = _receipt_for_intent(work, intent.intent_hash)
                _verify_receipt_chain(
                    work,
                    receipt,
                    task_id=task_id,
                    operation="generate",
                    slot=intent.output_slot,
                    data=serialize_session(item),
                )
                if intent.parent_hash != draft.session_hash or not verify_result(
                    material_root, item
                ):
                    raise AuthoringError("ATTENTION")
                binding = parse_authoring_binding_bytes(
                    _safe_read(
                        work / _AUTHORING_BINDINGS / f"{task_id}.json", "ATTENTION"
                    )
                )
                verify_authoring_binding(binding, work, material_root)
                return item
            raise AuthoringError("ATTENTION")
        _publish_request(work, request)
        intent = _intent(
            work, request, "generate", draft.session_hash, f"{task_id}-result.json"
        )
        try:
            result = generate_result(material_root, draft, draft.contract_hash)
        except MaterialWorkflowError as error:
            raise AuthoringError(error.code) from error
        data = serialize_session(result)
        _publish(work, "results", intent.output_slot, data)
        result_receipt = _receipt(work, intent, data, result.schema_version)
        draft_intent = _matching_intent(work, task_id, "draft", f"{task_id}-draft.json")
        if draft_intent is None:
            raise AuthoringError("ATTENTION")
        draft_bytes = _safe_read(work / "drafts" / f"{task_id}-draft.json", "ATTENTION")
        draft_receipt = _receipt_for_intent(work, draft_intent.intent_hash)
        _, draft_request = _verify_receipt_chain(
            work,
            draft_receipt,
            task_id=task_id,
            operation="draft",
            slot=draft_intent.output_slot,
            data=draft_bytes,
        )
        _, generate_request = _verify_receipt_chain(
            work,
            result_receipt,
            task_id=task_id,
            operation="generate",
            slot=intent.output_slot,
            data=data,
        )
        catalog = _catalog_for_hash(work, draft_intent.parent_hash or "")
        binding = _record(
            AuthoringBindingV2,
            "projecttown/v3/local-workspace-task-binding/v2",
            "binding_hash",
            task_id=task_id,
            work_root=str(work),
            work_root_device=int(work.lstat().st_dev),
            work_root_inode=int(work.lstat().st_ino),
            material_root=str(material_root),
            material_root_device=int(material_root.lstat().st_dev),
            material_root_inode=int(material_root.lstat().st_ino),
            catalog_hash=catalog.catalog_hash,
            draft_hash=draft.session_hash,
            draft_bytes_sha256=_sha(draft_bytes),
            draft_request_hash=draft_request.request_hash,
            draft_intent_hash=draft_intent.intent_hash,
            draft_receipt_hash=draft_receipt.receipt_hash,
            result_hash=result.session_hash,
            result_bytes_sha256=_sha(data),
            generate_request_hash=generate_request.request_hash,
            generate_intent_hash=intent.intent_hash,
            generate_receipt_hash=result_receipt.receipt_hash,
            artifact_kind=draft.artifact_kind,
        )
        _publish(
            work,
            _AUTHORING_BINDINGS,
            f"{task_id}.json",
            serialize_authoring_binding(binding),
        )
        return result


def export_result(
    work_root: Path,
    material_root: Path,
    *,
    task_id: str,
    format: Literal["markdown", "pdf"],
    idempotency_key: str,
) -> Path:
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    _validated_task(task_id)
    with _task_lock(task_id):
        try:
            result = load_external_session(work / "results" / f"{task_id}-result.json")
        except MaterialWorkflowError as error:
            raise AuthoringError("RESULT_UNAVAILABLE") from error
        if not isinstance(result, ResultSession) or not verify_result(
            material_root, result
        ):
            raise AuthoringError("MATERIAL_STALE_OR_MISMATCH")
        operation = "export-markdown" if format == "markdown" else "export-pdf"
        suffix = "md" if format == "markdown" else "pdf"
        payload = {"result_hash": result.session_hash, "format": format}
        request = _request(task_id, operation, idempotency_key, payload)
        replay = _idempotent_request(work, request)
        path = work / "exports" / f"{task_id}.{suffix}"
        if replay is not None:
            intent = _matching_intent(work, task_id, operation, path.name)
            if intent is not None and path.exists():
                data = _safe_read(path, "ATTENTION")
                receipt = _receipt_for_intent(work, intent.intent_hash)
                _verify_receipt_chain(
                    work,
                    receipt,
                    task_id=task_id,
                    operation=operation,
                    slot=path.name,
                    data=data,
                )
                if intent.parent_hash != result.session_hash:
                    raise AuthoringError("ATTENTION")
                return path
            raise AuthoringError("ATTENTION")
        _publish_request(work, request)
        intent = _intent(work, request, operation, result.session_hash, path.name)
        try:
            data = (
                render_export(material_root, result)
                if format == "markdown"
                else render_pdf_export(
                    material_root, result, export_version="v3-material-pdf-export-v1"
                )
            )
        except MaterialWorkflowError as error:
            raise AuthoringError(error.code) from error
        _publish(work, "exports", intent.output_slot, data)
        _receipt(
            work,
            intent,
            data,
            "text/markdown" if format == "markdown" else "application/pdf",
        )
        return path


def verify_authoring_binding(
    binding: AuthoringBindingV2, work_root: Path, material_root: Path
) -> ResultSession:
    """Authoritative v2 chain verifier; no v1 binding is involved."""
    work, work_meta = _root(work_root, "INVALID_WORK_ROOT")
    material, material_meta = _root(material_root, "INVALID_MATERIAL_ROOT")
    if (
        binding.work_root != str(work)
        or (binding.work_root_device, binding.work_root_inode)
        != (int(work_meta.st_dev), int(work_meta.st_ino))
        or binding.material_root != str(material)
        or (binding.material_root_device, binding.material_root_inode)
        != (int(material_meta.st_dev), int(material_meta.st_ino))
    ):
        raise AuthoringError("BINDING_ROOT_MISMATCH")
    catalog = _catalog_for_hash(work, binding.catalog_hash)
    if catalog.material_root != str(material):
        raise AuthoringError("BINDING_TAMPERED")
    draft_data = _safe_read(
        work / "drafts" / f"{binding.task_id}-draft.json", "BINDING_TAMPERED"
    )
    result_data = _safe_read(
        work / "results" / f"{binding.task_id}-result.json", "BINDING_TAMPERED"
    )
    if (
        _sha(draft_data) != binding.draft_bytes_sha256
        or _sha(result_data) != binding.result_bytes_sha256
    ):
        raise AuthoringError("BINDING_TAMPERED")
    try:
        draft = load_external_session(work / "drafts" / f"{binding.task_id}-draft.json")
        result = load_external_session(
            work / "results" / f"{binding.task_id}-result.json"
        )
    except MaterialWorkflowError as error:
        raise AuthoringError("BINDING_TAMPERED") from error
    if (
        not isinstance(draft, DraftSession)
        or not isinstance(result, ResultSession)
        or draft.session_hash != binding.draft_hash
        or result.session_hash != binding.result_hash
        or result.parent_session_hash != draft.session_hash
        or draft.artifact_kind != binding.artifact_kind
        or not _draft_is_fresh(material, draft)
        or not verify_result(material, result)
    ):
        raise AuthoringError("MATERIAL_STALE_OR_MISMATCH")
    draft_request = _one_by_hash(
        work,
        "requests",
        parse_request_bytes,
        "request_hash",
        binding.draft_request_hash,
    )
    draft_intent = _one_by_hash(
        work, "intents", parse_intent_bytes, "intent_hash", binding.draft_intent_hash
    )
    draft_receipt = _one_by_hash(
        work,
        "receipts",
        parse_receipt_bytes,
        "receipt_hash",
        binding.draft_receipt_hash,
    )
    generate_request = _one_by_hash(
        work,
        "requests",
        parse_request_bytes,
        "request_hash",
        binding.generate_request_hash,
    )
    generate_intent = _one_by_hash(
        work, "intents", parse_intent_bytes, "intent_hash", binding.generate_intent_hash
    )
    generate_receipt = _one_by_hash(
        work,
        "receipts",
        parse_receipt_bytes,
        "receipt_hash",
        binding.generate_receipt_hash,
    )
    _verify_receipt_chain(
        work,
        draft_receipt,
        task_id=binding.task_id,
        operation="draft",
        slot=f"{binding.task_id}-draft.json",
        data=draft_data,
    )
    _verify_receipt_chain(
        work,
        generate_receipt,
        task_id=binding.task_id,
        operation="generate",
        slot=f"{binding.task_id}-result.json",
        data=result_data,
    )
    if (
        draft_intent.request_hash != draft_request.request_hash
        or generate_intent.request_hash != generate_request.request_hash
        or draft_intent.parent_hash != catalog.catalog_hash
        or generate_intent.parent_hash != draft.session_hash
        or draft_request.payload.get("catalog_hash") != catalog.catalog_hash
        or tuple(draft_request.payload.get("source_ids", ())) == ()
        or generate_request.payload.get("draft_hash") != draft.session_hash
        or generate_request.payload.get("confirmation_hash") != draft.contract_hash
    ):
        raise AuthoringError("BINDING_TAMPERED")
    return result


def _repair_binding_if_complete(work: Path, material_root: Path, task_id: str) -> None:
    """Create a missing v2 binding only from an already verified full chain."""
    draft_data = _safe_read(work / "drafts" / f"{task_id}-draft.json", "ATTENTION")
    result_data = _safe_read(work / "results" / f"{task_id}-result.json", "ATTENTION")
    try:
        draft = load_external_session(work / "drafts" / f"{task_id}-draft.json")
        result = load_external_session(work / "results" / f"{task_id}-result.json")
    except MaterialWorkflowError as error:
        raise AuthoringError("ATTENTION") from error
    if (
        not isinstance(draft, DraftSession)
        or not isinstance(result, ResultSession)
        or result.parent_session_hash != draft.session_hash
        or not _draft_is_fresh(material_root, draft)
        or not verify_result(material_root, result)
    ):
        raise AuthoringError("ATTENTION")
    draft_intent = _matching_intent(work, task_id, "draft", f"{task_id}-draft.json")
    generate_intent = _matching_intent(
        work, task_id, "generate", f"{task_id}-result.json"
    )
    if draft_intent is None or generate_intent is None:
        raise AuthoringError("ATTENTION")
    draft_receipt = _receipt_for_intent(work, draft_intent.intent_hash)
    generate_receipt = _receipt_for_intent(work, generate_intent.intent_hash)
    _, draft_request = _verify_receipt_chain(
        work,
        draft_receipt,
        task_id=task_id,
        operation="draft",
        slot=draft_intent.output_slot,
        data=draft_data,
    )
    _, generate_request = _verify_receipt_chain(
        work,
        generate_receipt,
        task_id=task_id,
        operation="generate",
        slot=generate_intent.output_slot,
        data=result_data,
    )
    catalog = _catalog_for_hash(work, draft_intent.parent_hash or "")
    if (
        catalog.material_root != str(material_root)
        or generate_intent.parent_hash != draft.session_hash
        or draft_request.payload.get("catalog_hash") != catalog.catalog_hash
        or generate_request.payload.get("draft_hash") != draft.session_hash
    ):
        raise AuthoringError("ATTENTION")
    material_meta = material_root.lstat()
    binding = _record(
        AuthoringBindingV2,
        "projecttown/v3/local-workspace-task-binding/v2",
        "binding_hash",
        task_id=task_id,
        work_root=str(work),
        work_root_device=int(work.lstat().st_dev),
        work_root_inode=int(work.lstat().st_ino),
        material_root=str(material_root),
        material_root_device=int(material_meta.st_dev),
        material_root_inode=int(material_meta.st_ino),
        catalog_hash=catalog.catalog_hash,
        draft_hash=draft.session_hash,
        draft_bytes_sha256=_sha(draft_data),
        draft_request_hash=draft_request.request_hash,
        draft_intent_hash=draft_intent.intent_hash,
        draft_receipt_hash=draft_receipt.receipt_hash,
        result_hash=result.session_hash,
        result_bytes_sha256=_sha(result_data),
        generate_request_hash=generate_request.request_hash,
        generate_intent_hash=generate_intent.intent_hash,
        generate_receipt_hash=generate_receipt.receipt_hash,
        artifact_kind=draft.artifact_kind,
    )
    _publish(
        work,
        _AUTHORING_BINDINGS,
        f"{task_id}.json",
        serialize_authoring_binding(binding),
    )


def _matching_intent(
    work: Path, task_id: str, operation: str, output_slot: str
) -> AuthoringIntentV1 | None:
    matched: AuthoringIntentV1 | None = None
    for path in (work / "intents").glob("*.json"):
        intent = parse_intent_bytes(_safe_read(path, "ATTENTION"))
        if (
            intent.task_id == task_id
            and intent.operation == operation
            and intent.output_slot == output_slot
        ):
            if matched is not None:
                raise AuthoringError("ATTENTION")
            matched = intent
    return matched


def _draft_is_fresh(material_root: Path, draft: DraftSession) -> bool:
    manifest = inspect_material_set(
        material_root, draft.selections, policy=DEFAULT_POLICY
    )
    return (
        manifest.status == "complete"
        and manifest.root_hash == draft.material_manifest.root_hash
    )


def recover_authoring_state(
    work_root: Path, *, task_id: str, material_root: Path | None = None
) -> AuthoringState:
    """Rebuild state; restore a receipt only when source freshness is proved."""
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    _validated_task(task_id)
    outputs: list[tuple[str, Path, str]] = [
        (
            "draft",
            work / "drafts" / f"{task_id}-draft.json",
            "v3-material-draft-session-v1",
        ),
        (
            "generate",
            work / "results" / f"{task_id}-result.json",
            "v3-material-result-session-v1",
        ),
        ("export-markdown", work / "exports" / f"{task_id}.md", "text/markdown"),
        ("export-pdf", work / "exports" / f"{task_id}.pdf", "application/pdf"),
    ]
    receipts: dict[tuple[str, str], AuthoringReceiptV1] = {}
    for path in (work / "receipts").glob("*.json"):
        try:
            receipt = parse_receipt_bytes(_safe_read(path, "ATTENTION"))
        except AuthoringError:
            return AuthoringState(task_id=task_id, state="attention")
        if receipt.task_id == task_id:
            receipts[(receipt.operation, receipt.output_slot)] = receipt
    intents = [
        parse_intent_bytes(_safe_read(path, "ATTENTION"))
        for path in (work / "intents").glob("*.json")
    ]
    for intent in intents:
        if (
            intent.task_id == task_id
            and not (
                work
                / {
                    "draft": "drafts",
                    "generate": "results",
                    "export-markdown": "exports",
                    "export-pdf": "exports",
                }[intent.operation]
                / intent.output_slot
            ).exists()
        ):
            return AuthoringState(task_id=task_id, state="attention")
    draft_hash = result_hash = None
    exports: list[str] = []
    for operation, path, schema in outputs:
        if not path.exists():
            continue
        data = _safe_read(path, "ATTENTION")
        receipt = receipts.get((operation, path.name))
        if receipt is None:
            intent = _matching_intent(work, task_id, operation, path.name)
            if intent is None or material_root is None:
                return AuthoringState(task_id=task_id, state="attention")
            try:
                material, _ = _root(material_root, "INVALID_MATERIAL_ROOT")
                if operation == "draft":
                    candidate = load_external_session(path)
                    valid = isinstance(candidate, DraftSession) and _draft_is_fresh(
                        material, candidate
                    )
                elif operation == "generate":
                    candidate = load_external_session(path)
                    valid = (
                        isinstance(candidate, ResultSession)
                        and candidate.parent_session_hash == draft_hash
                        and verify_result(material, candidate)
                    )
                else:
                    candidate = load_external_session(
                        work / "results" / f"{task_id}-result.json"
                    )
                    valid = isinstance(candidate, ResultSession) and verify_result(
                        material, candidate
                    )
                if not valid:
                    return AuthoringState(task_id=task_id, state="attention")
                receipt = _receipt(work, intent, data, schema)
            except (AuthoringError, MaterialWorkflowError):
                return AuthoringState(task_id=task_id, state="attention")
        if receipt.output_sha256 != _sha(data) or receipt.output_schema != schema:
            return AuthoringState(task_id=task_id, state="attention")
        try:
            intent, _request_value = _verify_receipt_chain(
                work,
                receipt,
                task_id=task_id,
                operation=operation,
                slot=path.name,
                data=data,
            )
            if operation == "draft" and intent.parent_hash is None:
                return AuthoringState(task_id=task_id, state="attention")
            if operation == "generate" and intent.parent_hash != draft_hash:
                return AuthoringState(task_id=task_id, state="attention")
        except AuthoringError:
            return AuthoringState(task_id=task_id, state="attention")
        if operation == "draft":
            item = load_external_session(path)
            if not isinstance(item, DraftSession):
                return AuthoringState(task_id=task_id, state="attention")
            draft_hash = item.session_hash
        elif operation == "generate":
            item = load_external_session(path)
            if (
                not isinstance(item, ResultSession)
                or item.parent_session_hash != draft_hash
            ):
                return AuthoringState(task_id=task_id, state="attention")
            result_hash = item.session_hash
        else:
            exports.append(path.name)
    if result_hash is not None:
        state: Literal[
            "cataloged", "waiting_confirmation", "generated", "exported", "attention"
        ] = "exported" if exports else "generated"
        binding_path = work / _AUTHORING_BINDINGS / f"{task_id}.json"
        if binding_path.exists():
            try:
                binding = parse_authoring_binding_bytes(
                    _safe_read(binding_path, "ATTENTION")
                )
                verify_authoring_binding(
                    binding,
                    work,
                    material_root
                    if material_root is not None
                    else Path(binding.material_root),
                )
            except AuthoringError:
                return AuthoringState(task_id=task_id, state="attention")
        else:
            try:
                catalog_intent = _matching_intent(
                    work, task_id, "draft", f"{task_id}-draft.json"
                )
                if catalog_intent is None:
                    return AuthoringState(task_id=task_id, state="attention")
                catalog = _catalog_for_hash(work, catalog_intent.parent_hash or "")
                repair_material = (
                    material_root
                    if material_root is not None
                    else Path(catalog.material_root)
                )
                _repair_binding_if_complete(work, repair_material, task_id)
            except AuthoringError:
                return AuthoringState(task_id=task_id, state="attention")
    elif draft_hash is not None:
        state = "waiting_confirmation"
    else:
        state = "cataloged"
    return AuthoringState(
        task_id=task_id,
        state=state,
        draft_hash=draft_hash,
        result_hash=result_hash,
        exports=tuple(sorted(exports)),
    )


def authoring_projection(
    state: AuthoringState,
) -> dict[str, str | tuple[str, ...] | None]:
    """Safe API projection: opaque ids and state only; deliberately no paths."""
    return {
        "task_id": state.task_id,
        "state": state.state,
        "draft_hash": state.draft_hash,
        "result_hash": state.result_hash,
        "exports": state.exports,
    }


def load_authoring_result(
    work_root: Path, material_root: Path, *, task_id: str
) -> tuple[AuthoringState, ResultSession]:
    """Return a verified current result, rebuilding only verified core state."""
    state = recover_authoring_state(
        work_root, task_id=task_id, material_root=material_root
    )
    if state.state == "attention" or state.result_hash is None:
        raise AuthoringError("ATTENTION")
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    try:
        result = load_external_session(work / "results" / f"{task_id}-result.json")
    except MaterialWorkflowError as error:
        raise AuthoringError("ATTENTION") from error
    if (
        not isinstance(result, ResultSession)
        or result.session_hash != state.result_hash
        or not verify_result(material_root, result)
    ):
        raise AuthoringError("MATERIAL_STALE_OR_MISMATCH")
    return state, result


def read_authoring_export(
    work_root: Path,
    material_root: Path,
    *,
    task_id: str,
    format: Literal["markdown", "pdf"],
) -> bytes:
    """Read a completed export only after a full verified core-chain replay."""
    state, result = load_authoring_result(work_root, material_root, task_id=task_id)
    suffix = "md" if format == "markdown" else "pdf"
    operation = "export-markdown" if format == "markdown" else "export-pdf"
    slot = f"{task_id}.{suffix}"
    if state.state != "exported" or slot not in state.exports:
        raise AuthoringError("EXPORT_UNAVAILABLE")
    work, _ = _root(work_root, "INVALID_WORK_ROOT")
    data = _safe_read(work / "exports" / slot, "EXPORT_UNAVAILABLE")
    intent = _matching_intent(work, task_id, operation, slot)
    if intent is None or intent.parent_hash != result.session_hash:
        raise AuthoringError("ATTENTION")
    receipt = _receipt_for_intent(work, intent.intent_hash)
    _verify_receipt_chain(
        work,
        receipt,
        task_id=task_id,
        operation=operation,
        slot=slot,
        data=data,
    )
    return data
