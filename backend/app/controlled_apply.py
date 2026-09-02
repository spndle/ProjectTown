"""Phase 3A read-only ApplyPlan preparation and preflight checking.

This module deliberately does not contain an apply operation.  It records a
fresh, conflict-free README suggestion together with the identity of the
existing target, so a later, separately authorised protocol can decide whether
to implement a safe write path.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .material_workflow import (
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    ResultSession,
    load_session,
    publish_new_file,
    revalidate_result_sources,
    serialize_session,
    verify_result_integrity,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

APPLY_PLAN_SCHEMA_VERSION = "v3-material-apply-plan-v1"
APPLY_PLAN_HASH_DOMAIN = "projecttown/v3/material-apply-plan/v1"
_MAX_PLAN_BYTES = 64 * 1024
_DEFERRED_GATES = (
    "explicit_user_authorization",
    "versioned_executable_proposal",
    "backup_and_compare_and_swap",
    "write_receipt_and_recovery",
)


class ControlledApplyError(ValueError):
    """Stable, path-free rejection for the read-only Phase 3A contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("controlled apply rejected")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ApplyPlan(_Model):
    schema_version: Literal["v3-material-apply-plan-v1"]
    hash_domain: Literal["projecttown/v3/material-apply-plan/v1"]
    state: Literal["awaiting_user_confirmation"]
    proposal_semantics: Literal["human_readable_suggestion_not_executable_patch"]
    write_performed: Literal[False]
    result_session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_relative_path: str = Field(min_length=1, max_length=4096)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_size_bytes: int = Field(ge=0)
    target_device: int = Field(ge=0)
    target_inode: int = Field(ge=0)
    selected_scope: tuple[str, ...] = Field(min_length=1)
    suggestion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deferred_gates: tuple[str, ...]
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _plan_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        APPLY_PLAN_HASH_DOMAIN.encode("ascii") + b"\x00" + _canonical_json(payload)
    ).hexdigest()


def _validate_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ControlledApplyError("INVALID_ROOT")
    try:
        metadata = root.lstat()
        canonical = root.resolve(strict=True)
    except OSError as error:
        raise ControlledApplyError("ROOT_UNAVAILABLE") from error
    if canonical != root or not is_safe_directory(metadata):
        raise ControlledApplyError("INVALID_ROOT")
    return root


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise ControlledApplyError("TARGET_OUTSIDE_SCOPE") from error


def _stable_target(
    root: Path, target: Path, expected_relative: str
) -> tuple[str, int, int, int]:
    if not isinstance(target, Path) or not target.is_absolute():
        raise ControlledApplyError("INVALID_TARGET_PATH")
    expected = root.joinpath(*expected_relative.split("/"))
    try:
        expected_canonical = expected.resolve(strict=True)
        target_canonical = target.resolve(strict=True)
        metadata = target.lstat()
    except OSError as error:
        raise ControlledApplyError("TARGET_UNAVAILABLE") from error
    if target != target_canonical or target_canonical != expected_canonical:
        raise ControlledApplyError("TARGET_PATH_MISMATCH")
    relative = _relative_path(root, target_canonical)
    if (
        relative != expected_relative
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
    ):
        raise ControlledApplyError("INVALID_TARGET_PATH")
    current = root
    for component in expected_relative.split("/")[:-1]:
        current = current / component
        try:
            parent_metadata = current.lstat()
        except OSError as error:
            raise ControlledApplyError("TARGET_UNAVAILABLE") from error
        if not is_safe_directory(parent_metadata):
            raise ControlledApplyError("INVALID_TARGET_PATH")
    stable = read_stable_regular_file(
        target_canonical, metadata, capture_bytes=False, require_single_link=True
    )
    if stable is None:
        raise ControlledApplyError("UNSTABLE_TARGET")
    digest, size, _ = stable
    return digest, size, int(metadata.st_dev), int(metadata.st_ino)


def _fresh_readme_result(root: Path, result_path: Path) -> tuple[ResultSession, str]:
    try:
        result = load_session(root, result_path)
    except MaterialWorkflowError as error:
        raise ControlledApplyError(error.code) from error
    if not isinstance(result, ResultSession):
        raise ControlledApplyError("WRONG_SESSION_KIND")
    if (
        result.state != "generated"
        or result.draft.artifact_kind != "readme"
        or result.draft.readme_target is None
        or result.conflicts
        or not verify_result_integrity(result)
        or not revalidate_result_sources(root, result)
    ):
        raise ControlledApplyError("RESULT_NOT_FRESH_OR_CONFLICT_FREE")
    result_bytes_hash = _result_bytes_hash(result_path)
    if hashlib.sha256(serialize_session(result)).hexdigest() != result_bytes_hash:
        raise ControlledApplyError("RESULT_CHANGED_DURING_PREPARE")
    return result, result_bytes_hash


def _result_bytes_hash(result_path: Path) -> str:
    if not isinstance(result_path, Path) or not result_path.is_absolute():
        raise ControlledApplyError("INVALID_SESSION_PATH")
    try:
        metadata = result_path.lstat()
        parent_metadata = result_path.parent.lstat()
        canonical = result_path.resolve(strict=True)
    except OSError as error:
        raise ControlledApplyError("RESULT_UNAVAILABLE") from error
    if (
        canonical != result_path
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or not is_safe_directory(parent_metadata)
    ):
        raise ControlledApplyError("INVALID_SESSION_PATH")
    stable = read_stable_regular_file(
        result_path, metadata, capture_bytes=False, require_single_link=True
    )
    if stable is None:
        raise ControlledApplyError("UNSTABLE_RESULT")
    return stable[0]


def _validate_plan(plan: ApplyPlan) -> bool:
    data = plan.model_dump(mode="json")
    supplied = data.pop("plan_hash")
    return (
        plan.deferred_gates == _DEFERRED_GATES
        and plan.selected_scope == tuple(sorted(plan.selected_scope))
        and plan.target_relative_path in plan.selected_scope
        and supplied == _plan_hash(data)
    )


def prepare_apply_plan(
    root: Path, result_path: Path, target: Path, output: Path
) -> ApplyPlan:
    """Create an external, create-only readonly plan for one README target."""
    root = _validate_root(root)
    result, result_bytes_hash = _fresh_readme_result(root, result_path)
    target_digest, target_size, target_device, target_inode = _stable_target(
        root, target, result.draft.readme_target or ""
    )
    payload: dict[str, object] = {
        "schema_version": APPLY_PLAN_SCHEMA_VERSION,
        "hash_domain": APPLY_PLAN_HASH_DOMAIN,
        "state": "awaiting_user_confirmation",
        "proposal_semantics": "human_readable_suggestion_not_executable_patch",
        "write_performed": False,
        "result_session_hash": result.session_hash,
        "result_bytes_sha256": result_bytes_hash,
        "artifact_hash": result.artifact_hash,
        "preview_hash": result.preview_hash,
        "target_relative_path": result.draft.readme_target,
        "target_sha256": target_digest,
        "target_size_bytes": target_size,
        "target_device": target_device,
        "target_inode": target_inode,
        "selected_scope": result.draft.selections,
        "suggestion_sha256": result.artifact_hash,
        "deferred_gates": _DEFERRED_GATES,
    }
    payload["plan_hash"] = _plan_hash(payload)
    try:
        plan = ApplyPlan.model_validate(payload)
    except ValidationError as error:
        raise ControlledApplyError("INVALID_PLAN") from error
    if not _validate_plan(plan):
        raise ControlledApplyError("INVALID_PLAN")
    final_result, final_result_bytes_hash = _fresh_readme_result(root, result_path)
    final_target = _stable_target(root, target, result.draft.readme_target or "")
    if (
        final_result != result
        or final_result_bytes_hash != result_bytes_hash
        or final_target != (target_digest, target_size, target_device, target_inode)
    ):
        raise ControlledApplyError("BINDING_CHANGED_DURING_PREPARE")
    data = serialize_apply_plan(plan)
    try:
        publish_new_file(root, output, data)
    except (PublicationAttentionError, PublicationRollbackError):
        raise
    except MaterialWorkflowError as error:
        raise ControlledApplyError(error.code) from error
    return plan


def serialize_apply_plan(plan: ApplyPlan) -> bytes:
    if not isinstance(plan, ApplyPlan) or not _validate_plan(plan):
        raise ControlledApplyError("INVALID_PLAN")
    data = _canonical_json(plan.model_dump(mode="json"))
    if len(data) > _MAX_PLAN_BYTES:
        raise ControlledApplyError("PLAN_LIMIT_EXCEEDED")
    return data


def parse_apply_plan_bytes(data: bytes) -> ApplyPlan:
    if not isinstance(data, bytes) or len(data) > _MAX_PLAN_BYTES:
        raise ControlledApplyError("INVALID_PLAN")
    try:
        decoded = json.loads(data.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TypeError("plan must be a JSON object")
        # JSON is the canonical external form for immutable tuples.  Convert
        # only these two schema-defined arrays before strict model validation.
        for field in ("selected_scope", "deferred_gates"):
            if field in decoded:
                if not isinstance(decoded[field], list):
                    raise TypeError("canonical sequence must be a JSON array")
                decoded[field] = tuple(decoded[field])
        plan = ApplyPlan.model_validate(decoded)
    except (UnicodeDecodeError, TypeError, ValueError, ValidationError) as error:
        raise ControlledApplyError("INVALID_PLAN") from error
    if data != _canonical_json(plan.model_dump(mode="json")) or not _validate_plan(
        plan
    ):
        raise ControlledApplyError("INVALID_PLAN")
    return plan


def load_apply_plan(path: Path, *, material_root: Path | None = None) -> ApplyPlan:
    """Load one canonical ApplyPlan, optionally rejecting material-root storage.

    ``material_root`` is intentionally optional so existing callers that only
    need canonical record parsing retain their original contract.  The Phase
    3A CLI supplies it because ApplyPlans are create-only external evidence,
    never material-source files.
    """
    if not isinstance(path, Path) or not path.is_absolute():
        raise ControlledApplyError("INVALID_PLAN_PATH")
    try:
        metadata = path.lstat()
        parent_metadata = path.parent.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise ControlledApplyError("PLAN_UNAVAILABLE") from error
    if (
        canonical != path
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or not is_safe_directory(parent_metadata)
    ):
        raise ControlledApplyError("INVALID_PLAN_PATH")
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise ControlledApplyError("UNSTABLE_PLAN")
    if material_root is not None:
        root = _validate_root(material_root)
        try:
            canonical.relative_to(root)
        except ValueError:
            pass
        else:
            raise ControlledApplyError("INVALID_PLAN_PATH")
    return parse_apply_plan_bytes(stable[2])


def verify_apply_plan(
    root: Path, plan: ApplyPlan, result_path: Path, target: Path
) -> bool:
    """Revalidate every mutable binding.  A false result is a fail-closed BLOCK."""
    try:
        root = _validate_root(root)
        if not _validate_plan(plan):
            return False
        result, result_bytes_hash = _fresh_readme_result(root, result_path)
        if (
            result.session_hash != plan.result_session_hash
            or result_bytes_hash != plan.result_bytes_sha256
            or result.artifact_hash != plan.artifact_hash
            or result.preview_hash != plan.preview_hash
            or hashlib.sha256(result.artifact_markdown.encode("utf-8")).hexdigest()
            != plan.suggestion_sha256
            or result.draft.selections != plan.selected_scope
            or result.draft.readme_target != plan.target_relative_path
        ):
            return False
        digest, size, device, inode = _stable_target(
            root, target, plan.target_relative_path
        )
        return (
            digest == plan.target_sha256
            and size == plan.target_size_bytes
            and device == plan.target_device
            and inode == plan.target_inode
        )
    except ControlledApplyError:
        return False


__all__ = [
    "APPLY_PLAN_HASH_DOMAIN",
    "APPLY_PLAN_SCHEMA_VERSION",
    "ApplyPlan",
    "ControlledApplyError",
    "load_apply_plan",
    "parse_apply_plan_bytes",
    "prepare_apply_plan",
    "serialize_apply_plan",
    "verify_apply_plan",
]
