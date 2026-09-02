"""Phase 4D bind-only controlled-write handoff records.

This adapter is deliberately a verifier and publisher of one immutable binding.
It neither prepares an ApplyPlan/proposal nor carries an authorization or writes
the README target.  Phase 3C remains the sole owner of those operations.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .controlled_apply import (
    ApplyPlan,
    ControlledApplyError,
    load_apply_plan,
    verify_apply_plan,
)
from .executable_proposal import (
    ExecutableProposal,
    ExecutableProposalError,
    load_executable_proposal,
    verify_executable_proposal,
)
from .local_workspace_task_authoring import (
    AuthoringBindingV2,
    AuthoringError,
    parse_authoring_binding_bytes,
    verify_authoring_binding,
)
from .material_workflow import (
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    ResultSession,
    publish_new_file,
    serialize_session,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

HANDOFF_SCHEMA_VERSION = "v3-local-workspace-task-controlled-handoff-v1"
HANDOFF_HASH_DOMAIN = "projecttown/v3/local-workspace-task-controlled-handoff/v1"
HANDOFF_PRODUCER_VERSION = "projecttown-local-workspace-task-controlled-handoff-v1"
HANDOFF_STATE = "proposal_bound_awaiting_separate_apply_authorization"
HANDOFF_SEMANTICS = "verified-binding-only-no-authorization-no-target-write"
_MAX_BYTES = 128 * 1024
_HEX = r"^[0-9a-f]{64}$"
_ID = r"^[a-z][a-z0-9-]{0,63}$"
_DIRECTORY = "controlled-handoffs"


class ControlledHandoffError(ValueError):
    """Stable, path-free error codes for the bind-only contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ControlledHandoff(_Record):
    schema_version: Literal["v3-local-workspace-task-controlled-handoff-v1"]
    hash_domain: Literal["projecttown/v3/local-workspace-task-controlled-handoff/v1"]
    producer_version: Literal["projecttown-local-workspace-task-controlled-handoff-v1"]
    state: Literal["proposal_bound_awaiting_separate_apply_authorization"]
    handoff_semantics: Literal["verified-binding-only-no-authorization-no-target-write"]
    write_performed: Literal[False]
    authorization_included: Literal[False]
    task_id: str = Field(pattern=_ID)
    work_root: str
    work_root_device: int = Field(ge=0)
    work_root_inode: int = Field(ge=0)
    material_root: str
    material_root_device: int = Field(ge=0)
    material_root_inode: int = Field(ge=0)
    evidence_root: str
    evidence_root_device: int = Field(ge=0)
    evidence_root_inode: int = Field(ge=0)
    authoring_binding_path: str
    authoring_binding_hash: str = Field(pattern=_HEX)
    authoring_binding_bytes_sha256: str = Field(pattern=_HEX)
    catalog_hash: str = Field(pattern=_HEX)
    result_path: str
    result_schema_version: Literal["v3-material-result-session-v1"]
    result_session_hash: str = Field(pattern=_HEX)
    result_bytes_sha256: str = Field(pattern=_HEX)
    result_artifact_kind: Literal["readme"]
    target_relative_path: str = Field(min_length=1, max_length=4096)
    target_path: str
    target_device: int = Field(ge=0)
    target_inode: int = Field(ge=0)
    apply_plan_path: str
    apply_plan_schema_version: Literal["v3-material-apply-plan-v1"]
    apply_plan_hash: str = Field(pattern=_HEX)
    apply_plan_bytes_sha256: str = Field(pattern=_HEX)
    proposal_path: str
    proposal_schema_version: Literal["v3-material-executable-proposal-v1"]
    proposal_hash: str = Field(pattern=_HEX)
    proposal_bytes_sha256: str = Field(pattern=_HEX)
    proposal_post_image_sha256: str = Field(pattern=_HEX)
    proposal_post_image_size_bytes: int = Field(ge=0)
    handoff_hash: str = Field(pattern=_HEX)

    @field_validator(
        "work_root",
        "material_root",
        "evidence_root",
        "authoring_binding_path",
        "result_path",
        "target_path",
        "apply_plan_path",
        "proposal_path",
    )
    @classmethod
    def absolute_path_syntax(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or "\x00" in value
            or not Path(value).is_absolute()
        ):
            raise ValueError("absolute path required")
        return value

    @field_validator("target_relative_path")
    @classmethod
    def canonical_relative_path(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or "\\" in value
            or ":" in value
            or value.startswith("/")
            or "\x00" in value
        ):
            raise ValueError("invalid relative path")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("invalid relative path")
        return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(payload: dict[str, Any]) -> str:
    return _sha(HANDOFF_HASH_DOMAIN.encode("ascii") + b"\0" + _canonical_json(payload))


def _root(path: Path, code: str) -> tuple[Path, os.stat_result]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ControlledHandoffError(code)
    try:
        meta = path.lstat()
        if path.resolve(strict=True) != path or not is_safe_directory(meta):
            raise OSError("unsafe")
    except OSError as error:
        raise ControlledHandoffError(code) from error
    return path, meta


def _distinct_roots(work: Path, material: Path, evidence: Path) -> None:
    roots = (work, material, evidence)
    if any(
        left == right or left.is_relative_to(right) or right.is_relative_to(left)
        for position, left in enumerate(roots)
        for right in roots[position + 1 :]
    ):
        raise ControlledHandoffError("ROOT_SEPARATION_REQUIRED")


def _safe_file(path: Path, code: str) -> tuple[bytes, os.stat_result]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ControlledHandoffError(code)
    try:
        meta, parent = path.lstat(), path.parent.lstat()
        if (
            path.resolve(strict=True) != path
            or not stat.S_ISREG(meta.st_mode)
            or is_reparse(meta)
            or meta.st_nlink != 1
            or not is_safe_directory(parent)
        ):
            raise OSError("unsafe")
        stable = read_stable_regular_file(
            path, meta, capture_bytes=True, require_single_link=True
        )
        if stable is None or stable[2] is None:
            raise OSError("unstable")
        return stable[2], meta
    except OSError as error:
        raise ControlledHandoffError(code) from error


def _inside(root: Path, path: Path, code: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ControlledHandoffError(code) from error


def _binding_inputs(
    work: Path,
    material: Path,
    evidence: Path,
    task_id: str,
    binding_path: Path,
    plan_path: Path,
    proposal_path: Path,
) -> tuple[
    AuthoringBindingV2,
    ResultSession,
    ApplyPlan,
    ExecutableProposal,
    Path,
    dict[str, tuple[bytes, os.stat_result]],
]:
    expected_binding = work / "authoring-bindings" / f"{task_id}.json"
    expected_result = work / "results" / f"{task_id}-result.json"
    if binding_path != expected_binding:
        raise ControlledHandoffError("BINDING_PATH_MISMATCH")
    _inside(evidence, plan_path, "EVIDENCE_PATH_REQUIRED")
    _inside(evidence, proposal_path, "EVIDENCE_PATH_REQUIRED")
    values = {
        "binding": _safe_file(binding_path, "INVALID_BINDING_PATH"),
        "result": _safe_file(expected_result, "INVALID_RESULT_PATH"),
        "plan": _safe_file(plan_path, "INVALID_PLAN_PATH"),
        "proposal": _safe_file(proposal_path, "INVALID_PROPOSAL_PATH"),
    }
    try:
        binding = parse_authoring_binding_bytes(values["binding"][0])
        result = verify_authoring_binding(binding, work, material)
        plan = load_apply_plan(plan_path, material_root=material)
        proposal = load_executable_proposal(proposal_path, material_root=material)
    except (AuthoringError, ControlledApplyError, ExecutableProposalError) as error:
        raise ControlledHandoffError(
            getattr(error, "code", "BINDING_BLOCKED")
        ) from error
    if (
        binding.task_id != task_id
        or binding.artifact_kind != "readme"
        or not isinstance(result, ResultSession)
        or result.state != "generated"
        or result.draft.artifact_kind != "readme"
        or not result.draft.readme_target
        or result.conflicts
        or serialize_session(result) != values["result"][0]
    ):
        raise ControlledHandoffError("RESULT_NOT_FRESH_OR_GROUNDED")
    target = material.joinpath(*result.draft.readme_target.split("/"))
    _inside(material, target, "TARGET_OUTSIDE_MATERIAL_ROOT")
    _safe_file(target, "INVALID_TARGET_PATH")
    if not verify_apply_plan(
        material, plan, expected_result, target
    ) or not verify_executable_proposal(
        material, proposal, expected_result, target, plan_path
    ):
        raise ControlledHandoffError("UPSTREAM_PREFLIGHT_BLOCKED")
    return binding, result, plan, proposal, target, values


def _make_record(
    work_root: Path,
    material_root: Path,
    evidence_root: Path,
    *,
    task_id: str,
    binding_path: Path,
    plan_path: Path,
    proposal_path: Path,
) -> ControlledHandoff:
    work, work_meta = _root(work_root, "INVALID_WORK_ROOT")
    material, material_meta = _root(material_root, "INVALID_MATERIAL_ROOT")
    evidence, evidence_meta = _root(evidence_root, "INVALID_EVIDENCE_ROOT")
    _distinct_roots(work, material, evidence)
    binding, result, plan, proposal, target, values = _binding_inputs(
        work, material, evidence, task_id, binding_path, plan_path, proposal_path
    )
    _target_data, target_meta = _safe_file(target, "INVALID_TARGET_PATH")
    payload: dict[str, Any] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "hash_domain": HANDOFF_HASH_DOMAIN,
        "producer_version": HANDOFF_PRODUCER_VERSION,
        "state": HANDOFF_STATE,
        "handoff_semantics": HANDOFF_SEMANTICS,
        "write_performed": False,
        "authorization_included": False,
        "task_id": task_id,
        "work_root": str(work),
        "work_root_device": int(work_meta.st_dev),
        "work_root_inode": int(work_meta.st_ino),
        "material_root": str(material),
        "material_root_device": int(material_meta.st_dev),
        "material_root_inode": int(material_meta.st_ino),
        "evidence_root": str(evidence),
        "evidence_root_device": int(evidence_meta.st_dev),
        "evidence_root_inode": int(evidence_meta.st_ino),
        "authoring_binding_path": str(binding_path),
        "authoring_binding_hash": binding.binding_hash,
        "authoring_binding_bytes_sha256": _sha(values["binding"][0]),
        "catalog_hash": binding.catalog_hash,
        "result_path": str(work / "results" / f"{task_id}-result.json"),
        "result_schema_version": result.schema_version,
        "result_session_hash": result.session_hash,
        "result_bytes_sha256": _sha(values["result"][0]),
        "result_artifact_kind": "readme",
        "target_relative_path": result.draft.readme_target,
        "target_path": str(target),
        "target_device": int(target_meta.st_dev),
        "target_inode": int(target_meta.st_ino),
        "apply_plan_path": str(plan_path),
        "apply_plan_schema_version": plan.schema_version,
        "apply_plan_hash": plan.plan_hash,
        "apply_plan_bytes_sha256": _sha(values["plan"][0]),
        "proposal_path": str(proposal_path),
        "proposal_schema_version": proposal.schema_version,
        "proposal_hash": proposal.proposal_hash,
        "proposal_bytes_sha256": _sha(values["proposal"][0]),
        "proposal_post_image_sha256": proposal.post_image_sha256,
        "proposal_post_image_size_bytes": proposal.post_image_size_bytes,
    }
    payload["handoff_hash"] = _hash(payload)
    try:
        return ControlledHandoff.model_validate(payload)
    except ValidationError as error:
        raise ControlledHandoffError("INVALID_HANDOFF") from error


def serialize_controlled_handoff(value: ControlledHandoff) -> bytes:
    if not isinstance(value, ControlledHandoff):
        raise ControlledHandoffError("INVALID_HANDOFF")
    payload = value.model_dump(mode="json")
    supplied = payload.pop("handoff_hash")
    if supplied != _hash(payload):
        raise ControlledHandoffError("HANDOFF_TAMPERED")
    data = _canonical_json(value.model_dump(mode="json"))
    if len(data) > _MAX_BYTES:
        raise ControlledHandoffError("HANDOFF_LIMIT_EXCEEDED")
    return data


def parse_controlled_handoff_bytes(data: bytes) -> ControlledHandoff:
    if not isinstance(data, bytes) or len(data) > _MAX_BYTES:
        raise ControlledHandoffError("INVALID_HANDOFF")
    try:

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            item: dict[str, Any] = {}
            for key, value in pairs:
                if key in item:
                    raise ValueError("duplicate")
                item[key] = value
            return item

        value = ControlledHandoff.model_validate(
            json.loads(data.decode("ascii"), object_pairs_hook=unique)
        )
    except (UnicodeDecodeError, TypeError, ValueError, ValidationError) as error:
        raise ControlledHandoffError("INVALID_HANDOFF") from error
    if data != serialize_controlled_handoff(value):
        raise ControlledHandoffError("NONCANONICAL_HANDOFF")
    return value


def create_controlled_handoff(
    work_root: Path,
    material_root: Path,
    evidence_root: Path,
    *,
    task_id: str,
    binding_path: Path,
    plan_path: Path,
    proposal_path: Path,
    output: Path,
) -> ControlledHandoff:
    evidence, _ = _root(evidence_root, "INVALID_EVIDENCE_ROOT")
    if output != evidence / _DIRECTORY / f"{task_id}.json":
        raise ControlledHandoffError("INVALID_OUTPUT_PATH")
    value = _make_record(
        work_root,
        material_root,
        evidence,
        task_id=task_id,
        binding_path=binding_path,
        plan_path=plan_path,
        proposal_path=proposal_path,
    )
    # Re-read all mutable inputs immediately before publication.
    final = _make_record(
        work_root,
        material_root,
        evidence,
        task_id=task_id,
        binding_path=binding_path,
        plan_path=plan_path,
        proposal_path=proposal_path,
    )
    if final != value:
        raise ControlledHandoffError("BINDING_CHANGED_DURING_CREATE")
    parent = output.parent
    if output.exists():
        raise ControlledHandoffError("PUBLICATION_CONFLICT")
    if not parent.exists():
        try:
            parent.mkdir()
        except OSError as error:
            raise ControlledHandoffError("OUTPUT_DIRECTORY_UNAVAILABLE") from error
    _root(parent, "OUTPUT_DIRECTORY_UNAVAILABLE")
    try:
        publish_new_file(material_root, output, serialize_controlled_handoff(value))
    except PublicationRollbackError as error:
        raise ControlledHandoffError("PUBLICATION_ROLLED_BACK") from error
    except PublicationAttentionError as error:
        raise ControlledHandoffError("COMMITTED_NEEDS_ATTENTION") from error
    except MaterialWorkflowError as error:
        raise ControlledHandoffError(error.code) from error
    if not verify_controlled_handoff(work_root, material_root, evidence, output):
        raise ControlledHandoffError("COMMITTED_NEEDS_ATTENTION")
    return value


def verify_controlled_handoff(
    work_root: Path, material_root: Path, evidence_root: Path, path: Path
) -> bool:
    try:
        work, _ = _root(work_root, "INVALID_WORK_ROOT")
        material, _ = _root(material_root, "INVALID_MATERIAL_ROOT")
        evidence, _ = _root(evidence_root, "INVALID_EVIDENCE_ROOT")
        _distinct_roots(work, material, evidence)
        raw, _ = _safe_file(path, "INVALID_HANDOFF_PATH")
        value = parse_controlled_handoff_bytes(raw)
        if path != evidence / _DIRECTORY / f"{value.task_id}.json":
            return False
        expected = _make_record(
            work,
            material,
            evidence,
            task_id=value.task_id,
            binding_path=Path(value.authoring_binding_path),
            plan_path=Path(value.apply_plan_path),
            proposal_path=Path(value.proposal_path),
        )
        return expected == value
    except ControlledHandoffError:
        return False


def load_controlled_handoff(path: Path) -> ControlledHandoff:
    return parse_controlled_handoff_bytes(_safe_file(path, "INVALID_HANDOFF_PATH")[0])


__all__ = [
    "HANDOFF_HASH_DOMAIN",
    "HANDOFF_PRODUCER_VERSION",
    "HANDOFF_SCHEMA_VERSION",
    "ControlledHandoff",
    "ControlledHandoffError",
    "create_controlled_handoff",
    "load_controlled_handoff",
    "parse_controlled_handoff_bytes",
    "serialize_controlled_handoff",
    "verify_controlled_handoff",
]
