"""Canonical, create-only records used by the opt-in v3 loopback surface.

These records are intentionally separate from the controlled-write ledger: they
record a browser request's dispatch decision without changing its authority.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .material_workflow import MaterialWorkflowError, publish_new_direct_child
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

_HEX = r"^[0-9a-f]{64}$"
_MAX_RECORD = 16 * 1024


class LoopbackRecordError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OperationBinding(_Model):
    schema_version: Literal["v3-loopback-operation-binding-v1"]
    hash_domain: Literal["projecttown/v3/loopback-operation-binding/v1"]
    web_operation_id: str = Field(pattern=_HEX)
    work_root: str
    work_root_device: int = Field(ge=0)
    work_root_inode: int = Field(ge=0)
    authorization_path: str
    authorization_bytes_sha256: str = Field(pattern=_HEX)
    authorization_hash: str = Field(pattern=_HEX)
    authorization_schema_version: str
    controlled_operation_id: str
    material_root: str
    target_relative_path: str
    target_path_sha256: str = Field(pattern=_HEX)
    target_display: str
    allowed_mutations: tuple[Literal["apply", "reconcile", "restore"], ...]
    binding_hash: str = Field(pattern=_HEX)

    @field_validator("allowed_mutations", mode="before")
    @classmethod
    def _canonical_mutations(cls, value: Any) -> tuple[Any, ...]:
        if isinstance(value, list):
            return tuple(value)
        return value


class IdempotencyIntent(_Model):
    schema_version: Literal["v3-loopback-idempotency-intent-v1"]
    hash_domain: Literal["projecttown/v3/loopback-idempotency-intent/v1"]
    web_operation_id: str = Field(pattern=_HEX)
    key_sha256: str = Field(pattern=_HEX)
    request_digest: str = Field(pattern=_HEX)
    action: Literal["apply", "reconcile", "restore"]
    binding_hash: str = Field(pattern=_HEX)
    authorization_hash: str = Field(pattern=_HEX)
    intent_hash: str = Field(pattern=_HEX)


class IdempotencyResult(_Model):
    schema_version: Literal["v3-loopback-idempotency-result-v1"]
    hash_domain: Literal["projecttown/v3/loopback-idempotency-result/v1"]
    web_operation_id: str = Field(pattern=_HEX)
    key_sha256: str = Field(pattern=_HEX)
    request_digest: str = Field(pattern=_HEX)
    action: Literal["apply", "reconcile", "restore"]
    binding_hash: str = Field(pattern=_HEX)
    authorization_hash: str = Field(pattern=_HEX)
    intent_hash: str = Field(pattern=_HEX)
    outcome: Literal["completed", "attention", "rejected"]
    response_code: str
    write_performed: bool | None
    result_hash: str = Field(pattern=_HEX)


Record: TypeAlias = OperationBinding | IdempotencyIntent | IdempotencyResult
_SCHEMAS: dict[str, type[Record]] = {
    "v3-loopback-operation-binding-v1": OperationBinding,
    "v3-loopback-idempotency-intent-v1": IdempotencyIntent,
    "v3-loopback-idempotency-result-v1": IdempotencyResult,
}


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_hash(domain: str, payload: object) -> str:
    return sha256(domain.encode("ascii") + b"\0" + canonical_json(payload))


def _hash_field(record: Record) -> str:
    if isinstance(record, OperationBinding):
        return "binding_hash"
    if isinstance(record, IdempotencyIntent):
        return "intent_hash"
    return "result_hash"


def serialize_record(record: Record) -> bytes:
    payload = record.model_dump(mode="json")
    field = _hash_field(record)
    actual = payload.pop(field)
    if actual != record_hash(payload["hash_domain"], payload):
        raise LoopbackRecordError("INVALID_RECORD")
    data = canonical_json(record.model_dump(mode="json"))
    if len(data) > _MAX_RECORD:
        raise LoopbackRecordError("RECORD_LIMIT_EXCEEDED")
    return data


def parse_record_bytes(data: bytes) -> Record:
    if not isinstance(data, bytes) or len(data) > _MAX_RECORD:
        raise LoopbackRecordError("INVALID_RECORD")
    try:

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError("duplicate key")
                out[key] = value
            return out

        raw = json.loads(data.decode("ascii"), object_pairs_hook=unique)
        if not isinstance(raw, dict):
            raise TypeError("record must be an object")
        model = _SCHEMAS[raw.get("schema_version")]
        record = model.model_validate(raw)
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        ValidationError,
    ) as error:
        raise LoopbackRecordError("INVALID_RECORD") from error
    if data != serialize_record(record):
        raise LoopbackRecordError("INVALID_RECORD")
    return record


def _safe_parent(path: Path) -> None:
    try:
        if path.parent.resolve(strict=True) != path.parent:
            raise LoopbackRecordError("INVALID_RECORD")
        parent = path.parent.lstat()
    except OSError as error:
        raise LoopbackRecordError("INVALID_RECORD") from error
    if not is_safe_directory(parent):
        raise LoopbackRecordError("INVALID_RECORD")


def load_record(path: Path) -> Record:
    if not isinstance(path, Path) or not path.is_absolute():
        raise LoopbackRecordError("INVALID_RECORD")
    try:
        if path.resolve(strict=True) != path:
            raise LoopbackRecordError("INVALID_RECORD")
        metadata = path.lstat()
    except OSError as error:
        raise LoopbackRecordError("INVALID_RECORD") from error
    _safe_parent(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_RECORD
    ):
        raise LoopbackRecordError("INVALID_RECORD")
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None or stable[1] > _MAX_RECORD:
        raise LoopbackRecordError("UNSTABLE_RECORD")
    return parse_record_bytes(stable[2])


def publish_create_only(root: Path, path: Path, record: Record) -> None:
    data = serialize_record(record)
    try:
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise LoopbackRecordError("PUBLICATION_FAILED")
        root_metadata = root.lstat()
        if not is_safe_directory(root_metadata):
            raise LoopbackRecordError("PUBLICATION_FAILED")
        # Bind publications to a single already-existing safe directory.  The
        # material helper also checks this, but keep the record contract
        # independent from that implementation detail.
        if path.parent != root or not path.name.endswith(".json"):
            raise LoopbackRecordError("PUBLICATION_FAILED")
        publish_new_direct_child(root, path, data)
        published = load_record(path)
        if serialize_record(published) != data:
            raise LoopbackRecordError("PUBLICATION_ATTENTION")
    except MaterialWorkflowError as error:
        if getattr(error, "code", "") == "INVALID_OUTPUT_PATH":
            raise LoopbackRecordError("CREATE_ONLY_CONFLICT") from error
        raise LoopbackRecordError("PUBLICATION_FAILED") from error
    except OSError as error:
        raise LoopbackRecordError("PUBLICATION_FAILED") from error


def make_binding(**fields: Any) -> OperationBinding:
    fields.update(
        schema_version="v3-loopback-operation-binding-v1",
        hash_domain="projecttown/v3/loopback-operation-binding/v1",
    )
    payload = dict(fields)
    payload.pop("binding_hash", None)
    fields["binding_hash"] = record_hash(fields["hash_domain"], payload)
    return OperationBinding.model_validate(fields)


def make_intent(**fields: Any) -> IdempotencyIntent:
    fields.update(
        schema_version="v3-loopback-idempotency-intent-v1",
        hash_domain="projecttown/v3/loopback-idempotency-intent/v1",
    )
    payload = dict(fields)
    payload.pop("intent_hash", None)
    fields["intent_hash"] = record_hash(fields["hash_domain"], payload)
    return IdempotencyIntent.model_validate(fields)


def make_result(**fields: Any) -> IdempotencyResult:
    fields.update(
        schema_version="v3-loopback-idempotency-result-v1",
        hash_domain="projecttown/v3/loopback-idempotency-result/v1",
    )
    payload = dict(fields)
    payload.pop("result_hash", None)
    fields["result_hash"] = record_hash(fields["hash_domain"], payload)
    return IdempotencyResult.model_validate(fields)
