"""Phase 3E additive, fail-closed release-candidate records.

This module deliberately does not dispatch controlled writes, create releases, or
change the Phase 2 usability schemas.  It records the evidence required before a
human may make a release-candidate decision.
"""

from __future__ import annotations

import hashlib
import json
import stat
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .material_workflow import (
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    inspect_material_set,
    publish_new_direct_child,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

STUDY_SCHEMA = "v3-phase3e-study-v1"
ROUND_SCHEMA = "v3-phase3e-round-v1"
SUMMARY_SCHEMA = "v3-phase3e-summary-v1"
USER_DECISION_SCHEMA = "v3-phase3e-user-rc-decision-v1"
STUDY_HASH_DOMAIN = "projecttown/v3/phase3e-study/v1"
ROUND_HASH_DOMAIN = "projecttown/v3/phase3e-round/v1"
SUMMARY_HASH_DOMAIN = "projecttown/v3/phase3e-summary/v1"
USER_DECISION_HASH_DOMAIN = "projecttown/v3/phase3e-user-rc-decision/v1"
CANDIDATE_PROFILE = "projecttown-phase3e-rc-v1"
STUDY_SCHEMA_V2 = "v3-phase3e-study-v2"
ROUND_SCHEMA_V2 = "v3-phase3e-round-v2"
SUMMARY_SCHEMA_V2 = "v3-phase3e-summary-v2"
USER_DECISION_SCHEMA_V2 = "v3-phase3e-user-rc-decision-v2"
SOURCE_SET_SCHEMA_V2 = "v3-phase3e-source-set-v1"
STUDY_HASH_DOMAIN_V2 = "projecttown/v3/phase3e-study/v2"
ROUND_HASH_DOMAIN_V2 = "projecttown/v3/phase3e-round/v2"
SUMMARY_HASH_DOMAIN_V2 = "projecttown/v3/phase3e-summary/v2"
USER_DECISION_HASH_DOMAIN_V2 = "projecttown/v3/phase3e-user-rc-decision/v2"
SOURCE_SET_HASH_DOMAIN_V2 = "projecttown/v3/phase3e-source-set/v1"
CANDIDATE_PROFILE_V2 = "projecttown-phase3e-rc-v2"
STUDY_SCHEMA_V3 = "v3-phase3e-study-v3"
ROUND_SCHEMA_V3 = "v3-phase3e-round-v3"
SUMMARY_SCHEMA_V3 = "v3-phase3e-summary-v3"
USER_DECISION_SCHEMA_V3 = "v3-phase3e-user-rc-decision-v3"
STUDY_HASH_DOMAIN_V3 = "projecttown/v3/phase3e-study/v3"
ROUND_HASH_DOMAIN_V3 = "projecttown/v3/phase3e-round/v3"
SUMMARY_HASH_DOMAIN_V3 = "projecttown/v3/phase3e-summary/v3"
USER_DECISION_HASH_DOMAIN_V3 = "projecttown/v3/phase3e-user-rc-decision/v3"
CANDIDATE_PROFILE_V3 = "projecttown-phase3e-rc-v3"
STUDY_SCHEMA_V4 = "v3-phase3e-study-v4"
ROUND_SCHEMA_V4 = "v3-phase3e-round-v4"
SUMMARY_SCHEMA_V4 = "v3-phase3e-summary-v4"
USER_DECISION_SCHEMA_V4 = "v3-phase3e-user-rc-decision-v4"
STUDY_HASH_DOMAIN_V4 = "projecttown/v3/phase3e-study/v4"
ROUND_HASH_DOMAIN_V4 = "projecttown/v3/phase3e-round/v4"
SUMMARY_HASH_DOMAIN_V4 = "projecttown/v3/phase3e-summary/v4"
USER_DECISION_HASH_DOMAIN_V4 = "projecttown/v3/phase3e-user-rc-decision/v4"
ENGINEERING_ACCEPTANCE_SCHEMA_V4 = "v3-phase3e-engineering-acceptance-v4"
ENGINEERING_ACCEPTANCE_HASH_DOMAIN_V4 = (
    "projecttown/v3/phase3e-engineering-acceptance/v4"
)
CANDIDATE_PROFILE_V4 = "projecttown-phase3e-rc-v4"
GATE_MODEL_V4 = "participant_instance_plus_engineering_acceptance_plus_user_v1"
_V2_FIXED_TASK = (
    "生成一份 release-candidate 状态报告，说明已完成能力、当前阻断、工程证据、"
    "用户门禁和回滚步骤；关键结论必须有引用，不执行 Apply。"
)
_V2_SOURCE_PATHS = (
    "docs/v3-current-code-audit-2026-08-31.md",
    "docs/v3-phase-3.md",
    "docs/v3-phase-3e.md",
    "docs/v3-product-direction.md",
)
ROUND_IDS = ("R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT")
MAX_RECORD_BYTES = 256 * 1024
MAX_NOTES = 4_000
_HEX = r"^[0-9a-f]{64}$"


class Phase3EError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )


def _hash(domain: str, payload: object) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json(payload)
    ).hexdigest()


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _canonical_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value or " " in value or value.endswith("Z"):
        raise ValueError("timestamp must use an explicit canonical offset")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise ValueError("timestamp must include an offset and no fractional seconds")
    if parsed.isoformat() != value:
        raise ValueError("timestamp is not canonical")
    return value


def _notes(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != unicodedata.normalize("NFC", value)
        or len(value) > MAX_NOTES
    ):
        raise ValueError("invalid notes")
    if "\x00" in value or any(ord(char) < 32 for char in value):
        raise ValueError("invalid notes")
    return value


def _human_text(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != unicodedata.normalize("NFC", value)
        or "\x00" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("invalid text")
    return value


def _round1_constraints(
    value: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple) or len(value) > 32:
        raise ValueError("invalid constraints")
    canonical: list[tuple[str, str]] = []
    folded: set[str] = set()
    for pair in value:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("invalid constraints")
        key, item = pair
        if (
            not isinstance(key, str)
            or not isinstance(item, str)
            or key != unicodedata.normalize("NFC", key.replace("\r\n", "\n")).strip()
            or item != unicodedata.normalize("NFC", item.replace("\r\n", "\n")).strip()
            or not key
            or not item
            or len(key.encode("utf-8")) > 80
            or len(item.encode("utf-8")) > 500
            or "\x00" in key
            or "\x00" in item
        ):
            raise ValueError("invalid constraints")
        folded_key = key.casefold()
        if folded_key in folded:
            raise ValueError("invalid constraints")
        folded.add(folded_key)
        canonical.append((key, item))
    result = tuple(sorted(canonical))
    if result != value:
        raise ValueError("constraints must be canonical")
    return result


def _absolute_path_syntax(value: str) -> str:
    path = Path(value)
    if (
        not isinstance(value, str)
        or not path.is_absolute()
        or str(path) != value
        or ".." in path.parts
    ):
        raise ValueError("path must be canonical and absolute")
    return value


def _safe_directory(path: Path, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise Phase3EError(code)
    try:
        metadata = path.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise Phase3EError(code) from error
    if canonical != path or is_reparse(metadata) or not is_safe_directory(metadata):
        raise Phase3EError(code)
    return path


def _safe_file(path: Path, code: str) -> tuple[bytes, str]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise Phase3EError(code)
    try:
        metadata = path.lstat()
        canonical = path.resolve(strict=True)
        parent = path.parent.lstat()
    except OSError as error:
        raise Phase3EError(code) from error
    if (
        canonical != path
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or not is_safe_directory(parent)
        or is_reparse(parent)
    ):
        raise Phase3EError(code)
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise Phase3EError(code)
    return stable[2], stable[0]


def _safe_file_permission_mode(path: Path, code: str) -> int:
    _safe_file(path, code)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise Phase3EError(code) from error
    if not stat.S_ISREG(metadata.st_mode) or is_reparse(metadata):
        raise Phase3EError(code)
    return stat.S_IMODE(metadata.st_mode)


def _verify_round1_contract_files(
    contract: Round1ContractV3,
    *,
    study_root: Path,
    work_root: Path,
    repository: Path,
    round2_material_source_root: Path,
    expected_post_image_path: Path | None = None,
) -> None:
    material_root = _safe_directory(
        Path(contract.material_root), "INVALID_R1_MATERIAL_ROOT"
    )
    if any(
        material_root == root
        or material_root.is_relative_to(root)
        or root.is_relative_to(material_root)
        for root in (study_root, work_root, repository)
    ):
        raise Phase3EError("INVALID_R1_MATERIAL_ROOT")
    if (
        material_root == round2_material_source_root
        or material_root.is_relative_to(round2_material_source_root)
        or round2_material_source_root.is_relative_to(material_root)
    ):
        raise Phase3EError("R1_R2_MATERIAL_ROOT_OVERLAP")
    target = Path(contract.target_path)
    try:
        relative_target = target.relative_to(material_root)
    except ValueError as error:
        raise Phase3EError("R1_TARGET_OUTSIDE_MATERIAL_ROOT") from error
    target_data, target_digest = _safe_file(target, "INVALID_R1_TARGET_PATH")
    if (
        relative_target.as_posix() != contract.target_relative_path
        or target_digest != contract.initial_sha256
        or len(target_data) != contract.initial_size_bytes
        or _safe_file_permission_mode(target, "INVALID_R1_TARGET_PATH")
        != contract.initial_permission_mode
    ):
        raise Phase3EError("R1_TARGET_BINDING_MISMATCH")
    for entry in contract.source_entries:
        source_path = material_root / entry.relative_path
        try:
            source_path.relative_to(material_root)
        except ValueError as error:
            raise Phase3EError("R1_SOURCE_OUTSIDE_MATERIAL_ROOT") from error
        data, digest = _safe_file(source_path, "INVALID_R1_SOURCE_PATH")
        if digest != entry.bytes_sha256 or len(data) != entry.size_bytes:
            raise Phase3EError("R1_SOURCE_BINDING_MISMATCH")
    if expected_post_image_path is not None:
        post_path = Path(expected_post_image_path)
        post_data, post_digest = _safe_file(post_path, "INVALID_R1_EXPECTED_POST_IMAGE")
        if (
            not post_path.is_relative_to(work_root)
            or post_path == target
            or any(
                post_path == material_root / entry.relative_path
                for entry in contract.source_entries
            )
        ):
            raise Phase3EError("INVALID_R1_EXPECTED_POST_IMAGE")
        if (
            post_digest != contract.expected_post_sha256
            or len(post_data) != contract.expected_post_size_bytes
        ):
            raise Phase3EError("R1_EXPECTED_POST_IMAGE_MISMATCH")


class P0Values(_Model):
    control_rating_threshold: int = Field(ge=1, le=5)
    participant_arrangement: str = Field(min_length=3, max_length=160)
    participant_count: int = Field(ge=1, le=100)
    backup_retention: str = Field(min_length=3, max_length=160)
    release_evidence_format: str = Field(min_length=3, max_length=160)

    _texts = field_validator(
        "participant_arrangement", "backup_retention", "release_evidence_format"
    )(_human_text)


class CandidateLineage(_Model):
    candidate_profile: Literal["projecttown-phase3e-rc-v1"]
    procedure_version: Literal["phase3e-release-candidate-v1"]
    material_result_schema: Literal["v3-material-result-session-v1"]
    controlled_write_authorization_schema: Literal[
        "v3-controlled-write-authorization-v1"
    ]
    manifest_path: str
    manifest_sha256: str = Field(pattern=_HEX)

    _manifest_path = field_validator("manifest_path")(_absolute_path_syntax)


class Phase3EStudy(_Model):
    schema_version: Literal["v3-phase3e-study-v1"]
    hash_domain: Literal["projecttown/v3/phase3e-study/v1"]
    study_id: str = Field(min_length=3, max_length=160)
    study_root: str
    work_root: str
    lineage: CandidateLineage
    round_ids: tuple[
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
    ]
    p0: P0Values
    study_hash: str = Field(pattern=_HEX)

    _study_paths = field_validator("study_root", "work_root")(_absolute_path_syntax)

    @model_validator(mode="after")
    def _ordered_rounds(self) -> Phase3EStudy:
        if self.round_ids != ROUND_IDS:
            raise ValueError("round IDs must be ordered and unique")
        return self


class CandidateLineageV2(_Model):
    candidate_profile: Literal["projecttown-phase3e-rc-v2"]
    procedure_version: Literal["phase3e-release-candidate-v2"]
    material_result_schema: Literal["v3-material-result-session-v1"]
    controlled_write_authorization_schema: Literal[
        "v3-controlled-write-authorization-v1"
    ]
    manifest_path: str
    manifest_sha256: str = Field(pattern=_HEX)

    _manifest_path = field_validator("manifest_path")(_absolute_path_syntax)


class SourceSetEntryV2(_Model):
    relative_path: str
    bytes_sha256: str = Field(pattern=_HEX)

    @field_validator("relative_path")
    @classmethod
    def _fixed_path(cls, value: str) -> str:
        if value not in _V2_SOURCE_PATHS:
            raise ValueError("unexpected source path")
        return value


class SourceSetManifestV2(_Model):
    schema_version: Literal["v3-phase3e-source-set-v1"]
    hash_domain: Literal["projecttown/v3/phase3e-source-set/v1"]
    material_source_root: str
    fixed_task: Literal[
        "生成一份 release-candidate 状态报告，说明已完成能力、当前阻断、工程证据、用户门禁和回滚步骤；关键结论必须有引用，不执行 Apply。"
    ]
    entries: tuple[
        SourceSetEntryV2, SourceSetEntryV2, SourceSetEntryV2, SourceSetEntryV2
    ]
    source_set_root_hash: str = Field(pattern=_HEX)
    source_set_hash: str = Field(pattern=_HEX)

    _root = field_validator("material_source_root")(_absolute_path_syntax)

    @model_validator(mode="after")
    def _ordered_entries(self) -> SourceSetManifestV2:
        if tuple(item.relative_path for item in self.entries) != _V2_SOURCE_PATHS:
            raise ValueError("source entries must be complete and ordered")
        return self


class Round2SourceContractV2(_Model):
    material_source_root: str
    fixed_task: Literal[
        "生成一份 release-candidate 状态报告，说明已完成能力、当前阻断、工程证据、用户门禁和回滚步骤；关键结论必须有引用，不执行 Apply。"
    ]
    source_set_manifest_path: str
    source_set_manifest_sha256: str = Field(pattern=_HEX)
    source_set_hash: str = Field(pattern=_HEX)
    source_set_root_hash: str = Field(pattern=_HEX)
    expected_participant_identity: str = Field(min_length=3, max_length=160)
    expected_reviewer_identity: str = Field(min_length=3, max_length=160)

    _paths = field_validator("material_source_root", "source_set_manifest_path")(
        _absolute_path_syntax
    )
    _labels = field_validator(
        "expected_participant_identity", "expected_reviewer_identity"
    )(_human_text)

    @model_validator(mode="after")
    def _labels_differ(self) -> Round2SourceContractV2:
        if self.expected_participant_identity == self.expected_reviewer_identity:
            raise ValueError("participant and reviewer labels must differ")
        return self


class Phase3EStudyV2(_Model):
    schema_version: Literal["v3-phase3e-study-v2"]
    hash_domain: Literal["projecttown/v3/phase3e-study/v2"]
    study_id: str = Field(min_length=3, max_length=160)
    study_root: str
    work_root: str
    lineage: CandidateLineageV2
    round_ids: tuple[
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
    ]
    p0: P0Values
    round2_source: Round2SourceContractV2
    study_hash: str = Field(pattern=_HEX)

    _study_paths = field_validator("study_root", "work_root")(_absolute_path_syntax)

    @model_validator(mode="after")
    def _ordered_rounds(self) -> Phase3EStudyV2:
        if self.round_ids != ROUND_IDS:
            raise ValueError("round IDs must be ordered and unique")
        return self


class CandidateLineageV3(_Model):
    candidate_profile: Literal["projecttown-phase3e-rc-v3"]
    procedure_version: Literal["phase3e-release-candidate-v3"]
    material_result_schema: Literal["v3-material-result-session-v1"]
    controlled_write_authorization_schema: Literal[
        "v3-controlled-write-authorization-v1"
    ]
    manifest_path: str
    manifest_sha256: str = Field(pattern=_HEX)

    _manifest_path = field_validator("manifest_path")(_absolute_path_syntax)


class Round1SourceEntryV3(_Model):
    relative_path: str
    bytes_sha256: str = Field(pattern=_HEX)
    size_bytes: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or str(path) != value:
            raise ValueError("invalid source entry")
        return value


class Round1ContractV3(_Model):
    material_root: str
    source_entries: tuple[Round1SourceEntryV3, ...] = ()
    no_external_sources: bool
    exact_task: str = Field(min_length=1, max_length=MAX_NOTES)
    constraints: tuple[tuple[str, str], ...] = Field(max_length=32)
    target_path: str
    target_relative_path: str
    initial_sha256: str = Field(pattern=_HEX)
    initial_size_bytes: int = Field(ge=0)
    initial_permission_mode: int = Field(ge=0, le=0o7777)
    expected_post_sha256: str = Field(pattern=_HEX)
    expected_post_size_bytes: int = Field(gt=0)
    restore_executor_label: str = Field(min_length=3, max_length=160)
    identity_attestation_mode: Literal["self_attested_privacy_label_v1"]
    expected_participant_identity: str = Field(min_length=3, max_length=160)
    expected_reviewer_identity: str = Field(min_length=3, max_length=160)

    _paths = field_validator("material_root", "target_path")(_absolute_path_syntax)
    _texts = field_validator(
        "exact_task",
        "restore_executor_label",
        "expected_participant_identity",
        "expected_reviewer_identity",
    )(_human_text)
    _constraints = field_validator("constraints")(_round1_constraints)

    @model_validator(mode="after")
    def _contract(self) -> Round1ContractV3:
        if self.expected_participant_identity == self.expected_reviewer_identity:
            raise ValueError("participant and reviewer labels must differ")
        if self.no_external_sources != (not self.source_entries):
            raise ValueError("source entries must match no-external-sources")
        if len({item.relative_path for item in self.source_entries}) != len(
            self.source_entries
        ):
            raise ValueError("duplicate source entry")
        if tuple(item.relative_path for item in self.source_entries) != tuple(
            sorted(item.relative_path for item in self.source_entries)
        ):
            raise ValueError("source entries must be sorted")
        if any(
            item.relative_path == self.target_relative_path
            for item in self.source_entries
        ):
            raise ValueError("target cannot be a source entry")
        if (
            self.initial_sha256 == self.expected_post_sha256
            and self.initial_size_bytes == self.expected_post_size_bytes
        ):
            raise ValueError("expected post image cannot be a no-op")
        return self


class Phase3EStudyV3(_Model):
    schema_version: Literal["v3-phase3e-study-v3"]
    hash_domain: Literal["projecttown/v3/phase3e-study/v3"]
    study_id: str = Field(min_length=3, max_length=160)
    study_root: str
    work_root: str
    lineage: CandidateLineageV3
    round_ids: tuple[
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
    ]
    p0: P0Values
    round1_contract: Round1ContractV3
    round2_source: Round2SourceContractV2
    study_hash: str = Field(pattern=_HEX)

    _study_paths = field_validator("study_root", "work_root")(_absolute_path_syntax)

    @model_validator(mode="after")
    def _ordered_rounds(self) -> Phase3EStudyV3:
        if self.round_ids != ROUND_IDS:
            raise ValueError("round IDs must be ordered and unique")
        return self


class CandidateLineageV4(_Model):
    """A procedure lineage, deliberately separate from frozen v3 records."""

    candidate_profile: Literal["projecttown-phase3e-rc-v4"]
    procedure_version: Literal["phase3e-release-candidate-v4"]
    material_result_schema: Literal["v3-material-result-session-v1"]
    controlled_write_authorization_schema: Literal[
        "v3-controlled-write-authorization-v1"
    ]
    manifest_path: str
    manifest_sha256: str = Field(pattern=_HEX)

    _manifest_path = field_validator("manifest_path")(_absolute_path_syntax)


class Round1ContractV4(_Model):
    """Participant-only R1 contract; predecessor evidence is read-only history."""

    material_root: str
    exact_task: str = Field(min_length=1, max_length=MAX_NOTES)
    target_path: str
    expected_participant_identity: str = Field(min_length=3, max_length=160)
    predecessor_study_hash: str | None = Field(default=None, pattern=_HEX)
    predecessor_evidence: tuple[PredecessorEvidenceV4, ...] = Field(max_length=16)

    _paths = field_validator("material_root", "target_path")(_absolute_path_syntax)
    _texts = field_validator("exact_task", "expected_participant_identity")(_human_text)

    @model_validator(mode="after")
    def _predecessor_is_explicit(self) -> Round1ContractV4:
        if (self.predecessor_study_hash is None) != (not self.predecessor_evidence):
            raise ValueError("predecessor binding must be complete or absent")
        if len({item.path for item in self.predecessor_evidence}) != len(
            self.predecessor_evidence
        ):
            raise ValueError("duplicate predecessor evidence")
        required_roles = {
            "v3_study",
            "result",
            "apply_plan",
            "executable_proposal",
            "apply_authorization",
            "restore_authorization",
            "apply_receipt",
            "restore_receipt",
            "apply_backup",
            "restore_backup",
        }
        if (
            self.predecessor_evidence
            and {item.role for item in self.predecessor_evidence} != required_roles
        ):
            raise ValueError("incomplete predecessor evidence roles")
        return self


class PredecessorEvidenceV4(_Model):
    """Immutable historical evidence; this object is never an execution grant."""

    path: str
    bytes_sha256: str = Field(pattern=_HEX)
    role: Literal[
        "v3_study",
        "result",
        "apply_plan",
        "executable_proposal",
        "apply_authorization",
        "restore_authorization",
        "apply_receipt",
        "restore_receipt",
        "apply_backup",
        "restore_backup",
    ]
    inherited_historical_evidence: Literal[True]

    _path = field_validator("path")(_absolute_path_syntax)


class Round2SourceContractV4(_Model):
    material_source_root: str
    fixed_task: Literal[
        "生成一份 release-candidate 状态报告，说明已完成能力、当前阻断、工程证据、用户门禁和回滚步骤；关键结论必须有引用，不执行 Apply。"
    ]
    source_set_manifest_path: str
    source_set_manifest_sha256: str = Field(pattern=_HEX)
    source_set_hash: str = Field(pattern=_HEX)
    source_set_root_hash: str = Field(pattern=_HEX)
    expected_participant_identity: str = Field(min_length=3, max_length=160)

    _paths = field_validator("material_source_root", "source_set_manifest_path")(
        _absolute_path_syntax
    )
    _identity = field_validator("expected_participant_identity")(_human_text)


class Phase3EStudyV4(_Model):
    schema_version: Literal["v3-phase3e-study-v4"]
    hash_domain: Literal["projecttown/v3/phase3e-study/v4"]
    study_id: str = Field(min_length=3, max_length=160)
    study_root: str
    work_root: str
    lineage: CandidateLineageV4
    gate_model: Literal["participant_instance_plus_engineering_acceptance_plus_user_v1"]
    expected_participant_identity: str = Field(min_length=3, max_length=160)
    round_ids: tuple[
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
        Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
    ]
    p0: P0Values
    round1_contract: Round1ContractV4
    round2_source: Round2SourceContractV4
    study_hash: str = Field(pattern=_HEX)

    _paths = field_validator("study_root", "work_root")(_absolute_path_syntax)
    _identity = field_validator("expected_participant_identity")(_human_text)

    @model_validator(mode="after")
    def _contract(self) -> Phase3EStudyV4:
        if self.round_ids != ROUND_IDS:
            raise ValueError("round IDs must be ordered and unique")
        if (
            self.round1_contract.expected_participant_identity
            != self.expected_participant_identity
            or self.round2_source.expected_participant_identity
            != self.expected_participant_identity
            or self.p0.participant_count != 1
        ):
            raise ValueError("v4 requires one consistent participant")
        return self


class EvidenceBinding(_Model):
    kind: Literal[
        "result",
        "apply_plan",
        "executable_proposal",
        "user_authorization",
        "restore_authorization",
        "loopback_binding",
        "ledger",
        "backup",
        "restore_backup",
        "apply_receipt",
        "restore_receipt",
        "reconcile_observation",
        "restore_observation",
        "material_manifest",
        "preview",
        "citation",
        "pdf_export",
    ]
    path: str
    bytes_sha256: str = Field(pattern=_HEX)
    schema_version: str | None = Field(default=None, max_length=160)
    record_hash: str | None = Field(default=None, pattern=_HEX)

    _evidence_path = field_validator("path")(_absolute_path_syntax)


class EvidenceBindingV2(_Model):
    kind: Literal[
        "result",
        "apply_plan",
        "executable_proposal",
        "user_authorization",
        "restore_authorization",
        "loopback_binding",
        "ledger",
        "backup",
        "restore_backup",
        "apply_receipt",
        "restore_receipt",
        "reconcile_observation",
        "restore_observation",
        "material_manifest",
        "source_set_manifest",
        "preview",
        "citation",
        "pdf_export",
    ]
    path: str
    bytes_sha256: str = Field(pattern=_HEX)
    schema_version: str | None = Field(default=None, max_length=160)
    record_hash: str | None = Field(default=None, pattern=_HEX)

    _evidence_path = field_validator("path")(_absolute_path_syntax)


class EvidenceBindingV3(_Model):
    kind: Literal[
        "result",
        "apply_plan",
        "executable_proposal",
        "user_authorization",
        "restore_authorization",
        "backup",
        "restore_backup",
        "apply_receipt",
        "restore_receipt",
        "material_manifest",
        "source_set_manifest",
        "preview",
        "citation",
        "pdf_export",
    ]
    path: str
    bytes_sha256: str = Field(pattern=_HEX)
    schema_version: str | None = Field(default=None, max_length=160)
    record_hash: str | None = Field(default=None, pattern=_HEX)

    _evidence_path = field_validator("path")(_absolute_path_syntax)


class ControlledOperationBindingV3(_Model):
    authorization_path: str
    ledger_root: str
    operation_id: str = Field(pattern="^[a-z0-9][a-z0-9-]{2,79}$")
    backup_manifest_path: str
    post_observation_path: str
    receipt_path: str

    _paths = field_validator(
        "authorization_path",
        "ledger_root",
        "backup_manifest_path",
        "post_observation_path",
        "receipt_path",
    )(_absolute_path_syntax)


class ParticipantEvidence(_Model):
    participant_identity: str = Field(min_length=1, max_length=160)
    disposition: Literal["retained", "not_kept"]
    elapsed_seconds: int = Field(ge=1, le=86_400)
    actions: tuple[Literal["open_task"], ...] = Field(min_length=1, max_length=5)
    notes: str = Field(min_length=1, max_length=MAX_NOTES)
    timestamp: str
    evidence_path: str
    evidence_sha256: str | None = Field(default=None, pattern=_HEX)

    _evidence_path = field_validator("evidence_path")(_absolute_path_syntax)
    _identity = field_validator("participant_identity")(_human_text)

    @model_validator(mode="after")
    def _canonical(self) -> ParticipantEvidence:
        _notes(self.notes)
        _canonical_timestamp(self.timestamp)
        return self


class ReviewerEvidence(_Model):
    reviewer_identity: str = Field(min_length=1, max_length=160)
    disposition: Literal["PASS", "REVISE", "FAIL"]
    executability_rating: int = Field(ge=1, le=5)
    readability_rating: int = Field(ge=1, le=5)
    control_rating: int = Field(ge=1, le=5)
    citation_traceability_rating: int = Field(ge=1, le=5)
    fixed_question_answers: tuple[str, str, str, str]
    notes: str = Field(min_length=1, max_length=MAX_NOTES)
    actions: tuple[str, ...] = Field(max_length=5)
    timestamp: str
    evidence_path: str
    evidence_sha256: str | None = Field(default=None, pattern=_HEX)

    _evidence_path = field_validator("evidence_path")(_absolute_path_syntax)
    _identity = field_validator("reviewer_identity")(_human_text)

    @model_validator(mode="after")
    def _canonical(self) -> ReviewerEvidence:
        if len(self.fixed_question_answers) != 4:
            raise ValueError("exactly four fixed question answers are required")
        for answer in self.fixed_question_answers:
            _notes(answer)
        _notes(self.notes)
        _canonical_timestamp(self.timestamp)
        return self


class ParticipantEvidenceV4(ParticipantEvidence):
    """Participant supplied instance-test outcome; no reviewer fields exist here."""

    control_rating: int = Field(ge=1, le=5)
    citation_usable: bool
    structural_rewrite: bool


class EngineeringAcceptanceV4(_Model):
    """Non-human engineering evidence, separately hashed from participant judgment."""

    schema_version: Literal["v3-phase3e-engineering-acceptance-v4"]
    hash_domain: Literal["projecttown/v3/phase3e-engineering-acceptance/v4"]
    outcome: Literal["PASS", "FAIL"]
    verifier_identity: str = Field(min_length=3, max_length=160)
    checks: tuple[str, ...] = Field(min_length=1, max_length=32)
    notes: str = Field(min_length=1, max_length=MAX_NOTES)
    actions: tuple[str, ...] = Field(max_length=16)
    timestamp: str
    evidence_path: str
    evidence_sha256: str | None = Field(default=None, pattern=_HEX)
    citation_traceable: bool
    citation_usable: bool
    blocking_defect: bool
    acceptance_hash: str = Field(pattern=_HEX)

    _path = field_validator("evidence_path")(_absolute_path_syntax)
    _identity = field_validator("verifier_identity")(_human_text)

    @model_validator(mode="after")
    def _canonical(self) -> EngineeringAcceptanceV4:
        _notes(self.notes)
        _canonical_timestamp(self.timestamp)
        if any(not isinstance(item, str) or not item for item in self.checks):
            raise ValueError("invalid engineering checks")
        if self.outcome == "PASS" and (
            self.blocking_defect
            or not self.citation_traceable
            or not self.citation_usable
        ):
            raise ValueError("PASS engineering acceptance cannot have blockers")
        payload = self.model_dump(mode="json")
        payload.pop("acceptance_hash")
        if self.acceptance_hash != _hash(
            ENGINEERING_ACCEPTANCE_HASH_DOMAIN_V4, payload
        ):
            raise ValueError("engineering acceptance hash mismatch")
        return self


def create_engineering_acceptance_v4(
    *,
    outcome: Literal["PASS", "FAIL"],
    verifier_identity: str,
    checks: tuple[str, ...],
    notes: str,
    actions: tuple[str, ...],
    timestamp: str,
    evidence_path: str,
    evidence_sha256: str | None = None,
    citation_traceable: bool,
    citation_usable: bool,
    blocking_defect: bool,
) -> EngineeringAcceptanceV4:
    values: dict[str, object] = {
        "schema_version": ENGINEERING_ACCEPTANCE_SCHEMA_V4,
        "hash_domain": ENGINEERING_ACCEPTANCE_HASH_DOMAIN_V4,
        "outcome": outcome,
        "verifier_identity": verifier_identity,
        "checks": checks,
        "notes": notes,
        "actions": actions,
        "timestamp": timestamp,
        "evidence_path": evidence_path,
        "evidence_sha256": evidence_sha256,
        "citation_traceable": citation_traceable,
        "citation_usable": citation_usable,
        "blocking_defect": blocking_defect,
    }
    values["acceptance_hash"] = _hash(ENGINEERING_ACCEPTANCE_HASH_DOMAIN_V4, values)
    return EngineeringAcceptanceV4.model_validate(values)


class Phase3ERound(_Model):
    schema_version: Literal["v3-phase3e-round-v1"]
    hash_domain: Literal["projecttown/v3/phase3e-round/v1"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"]
    round_kind: Literal["controlled_apply", "report_export"]
    binding_status: Literal["verified", "stale", "conflict", "missing"]
    target_is_disposable_external_fixture: bool
    evidence: tuple[EvidenceBinding, ...] = Field(min_length=1, max_length=16)
    participant: ParticipantEvidence | None = None
    reviewer: ReviewerEvidence | None = None
    citation_usable: bool | None = None
    structural_rewrite: bool | None = None
    blocking_defect: bool = False
    round_hash: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def _round_contract(self) -> Phase3ERound:
        kinds = tuple(item.kind for item in self.evidence)
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate evidence kind")
        if self.round_id == "R1-CONTROLLED-APPLY":
            required = {
                "result",
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "restore_authorization",
                "ledger",
                "backup",
                "restore_backup",
                "apply_receipt",
                "restore_receipt",
                "reconcile_observation",
            }
            if (
                self.round_kind != "controlled_apply"
                or not self.target_is_disposable_external_fixture
                or not required.issubset(kinds)
            ):
                raise ValueError("invalid controlled-apply binding")
        else:
            required = {
                "material_manifest",
                "result",
                "preview",
                "citation",
                "pdf_export",
            }
            forbidden = {
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "loopback_binding",
                "ledger",
                "backup",
                "restore_backup",
                "apply_receipt",
                "restore_receipt",
                "reconcile_observation",
                "restore_observation",
            }
            if (
                self.round_kind != "report_export"
                or self.target_is_disposable_external_fixture
                or not required.issubset(kinds)
                or forbidden.intersection(kinds)
            ):
                raise ValueError("invalid report-export binding")
        if self.reviewer is not None and (
            self.participant is None
            or self.citation_usable is None
            or self.structural_rewrite is None
        ):
            raise ValueError("reviewer evidence requires complete participant outcome")
        if (
            self.participant is not None and self.participant.evidence_sha256 is None
        ) or (self.reviewer is not None and self.reviewer.evidence_sha256 is None):
            raise ValueError("human evidence must bind bytes")
        return self


class Phase3ERoundV2(_Model):
    schema_version: Literal["v3-phase3e-round-v2"]
    hash_domain: Literal["projecttown/v3/phase3e-round/v2"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"]
    round_kind: Literal["controlled_apply", "report_export"]
    binding_status: Literal["verified", "stale", "conflict", "missing"]
    target_is_disposable_external_fixture: bool
    evidence: tuple[EvidenceBindingV2, ...] = Field(min_length=1, max_length=17)
    participant: ParticipantEvidence | None = None
    reviewer: ReviewerEvidence | None = None
    citation_usable: bool | None = None
    structural_rewrite: bool | None = None
    blocking_defect: bool = False
    round_hash: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def _round_contract(self) -> Phase3ERoundV2:
        kinds = tuple(item.kind for item in self.evidence)
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate evidence kind")
        if self.round_id == "R1-CONTROLLED-APPLY":
            required = {
                "result",
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "restore_authorization",
                "ledger",
                "backup",
                "restore_backup",
                "apply_receipt",
                "restore_receipt",
                "reconcile_observation",
            }
            if (
                self.round_kind != "controlled_apply"
                or not self.target_is_disposable_external_fixture
                or not required.issubset(kinds)
                or "source_set_manifest" in kinds
            ):
                raise ValueError("invalid controlled-apply binding")
        else:
            required = {
                "material_manifest",
                "source_set_manifest",
                "result",
                "preview",
                "citation",
                "pdf_export",
            }
            forbidden = {
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "loopback_binding",
                "ledger",
                "backup",
                "restore_backup",
                "apply_receipt",
                "restore_receipt",
                "reconcile_observation",
                "restore_observation",
            }
            if (
                self.round_kind != "report_export"
                or self.target_is_disposable_external_fixture
                or not required.issubset(kinds)
                or forbidden.intersection(kinds)
            ):
                raise ValueError("invalid report-export binding")
        if self.reviewer is not None and (
            self.participant is None
            or self.citation_usable is None
            or self.structural_rewrite is None
        ):
            raise ValueError("reviewer evidence requires complete participant outcome")
        if (
            self.participant is not None and self.participant.evidence_sha256 is None
        ) or (self.reviewer is not None and self.reviewer.evidence_sha256 is None):
            raise ValueError("human evidence must bind bytes")
        return self


class Phase3ERoundV3(_Model):
    schema_version: Literal["v3-phase3e-round-v3"]
    hash_domain: Literal["projecttown/v3/phase3e-round/v3"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"]
    round_kind: Literal["controlled_apply", "report_export"]
    binding_status: Literal["verified", "stale", "conflict", "missing"]
    target_is_disposable_external_fixture: bool
    evidence: tuple[EvidenceBindingV3, ...] = Field(min_length=1, max_length=12)
    apply_operation: ControlledOperationBindingV3 | None = None
    restore_operation: ControlledOperationBindingV3 | None = None
    participant: ParticipantEvidence | None = None
    reviewer: ReviewerEvidence | None = None
    citation_usable: bool | None = None
    structural_rewrite: bool | None = None
    blocking_defect: bool = False
    round_hash: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def _round_contract(self) -> Phase3ERoundV3:
        kinds = tuple(item.kind for item in self.evidence)
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate evidence kind")
        if self.round_id == ROUND_IDS[0]:
            required = {
                "result",
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "restore_authorization",
                "backup",
                "restore_backup",
                "apply_receipt",
                "restore_receipt",
            }
            if (
                self.round_kind != "controlled_apply"
                or not self.target_is_disposable_external_fixture
                or not required.issubset(kinds)
                or self.apply_operation is None
                or self.restore_operation is None
            ):
                raise ValueError("invalid controlled-apply v3 binding")
        else:
            required = {
                "material_manifest",
                "source_set_manifest",
                "result",
                "preview",
                "citation",
                "pdf_export",
            }
            if (
                self.round_kind != "report_export"
                or self.target_is_disposable_external_fixture
                or not required.issubset(kinds)
                or self.apply_operation is not None
                or self.restore_operation is not None
            ):
                raise ValueError("invalid report-export v3 binding")
        if self.reviewer is not None and (
            self.participant is None
            or self.citation_usable is None
            or self.structural_rewrite is None
        ):
            raise ValueError("reviewer evidence requires complete participant outcome")
        if (
            self.participant is not None and self.participant.evidence_sha256 is None
        ) or (self.reviewer is not None and self.reviewer.evidence_sha256 is None):
            raise ValueError("human evidence must bind bytes")
        return self


class EvidenceBindingV4(EvidenceBindingV3):
    kind: Literal[
        "result",
        "apply_plan",
        "executable_proposal",
        "user_authorization",
        "restore_authorization",
        "apply_receipt",
        "restore_receipt",
        "backup",
        "restore_backup",
        "material_manifest",
        "source_set_manifest",
        "preview",
        "citation",
        "pdf_export",
        "predecessor_evidence",
    ]


class Phase3ERoundV4(_Model):
    schema_version: Literal["v3-phase3e-round-v4"]
    hash_domain: Literal["projecttown/v3/phase3e-round/v4"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"]
    round_kind: Literal["controlled_apply", "report_export"]
    binding_status: Literal["verified", "stale", "conflict", "missing"]
    target_is_disposable_external_fixture: bool
    evidence: tuple[EvidenceBindingV4, ...] = Field(min_length=1, max_length=16)
    participant: ParticipantEvidenceV4 | None = None
    engineering_acceptance: EngineeringAcceptanceV4 | None = None
    round_hash: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def _round_contract(self) -> Phase3ERoundV4:
        kinds = tuple(item.kind for item in self.evidence)
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate evidence kind")
        required = (
            {
                "result",
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "restore_authorization",
                "apply_receipt",
                "restore_receipt",
                "backup",
                "restore_backup",
            }
            if self.round_id == ROUND_IDS[0]
            else {
                "material_manifest",
                "source_set_manifest",
                "result",
                "preview",
                "citation",
                "pdf_export",
            }
        )
        if not required.issubset(kinds):
            raise ValueError("incomplete v4 evidence")
        if self.round_id == ROUND_IDS[0] and (
            self.round_kind != "controlled_apply"
            or not self.target_is_disposable_external_fixture
        ):
            raise ValueError("invalid v4 controlled-apply binding")
        if self.round_id == ROUND_IDS[1] and (
            self.round_kind != "report_export"
            or self.target_is_disposable_external_fixture
        ):
            raise ValueError("invalid v4 report-export binding")
        if (self.participant is None) != (self.engineering_acceptance is None):
            raise ValueError("participant and engineering acceptance must be paired")
        if self.participant is not None and self.participant.evidence_sha256 is None:
            raise ValueError("participant evidence must bind bytes")
        if (
            self.engineering_acceptance is not None
            and self.engineering_acceptance.evidence_sha256 is None
        ):
            raise ValueError("engineering evidence must bind bytes")
        return self


class RoundProjection(_Model):
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"]
    round_hash: str = Field(pattern=_HEX)
    reviewer_disposition: Literal["PASS", "REVISE", "FAIL"] | None
    citation_usable: bool | None
    structural_rewrite: bool | None
    binding_status: Literal["verified", "stale", "conflict", "missing"]
    blocking_defect: bool


class Phase3ESummary(_Model):
    schema_version: Literal["v3-phase3e-summary-v1"]
    hash_domain: Literal["projecttown/v3/phase3e-summary/v1"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_projections: tuple[RoundProjection, RoundProjection]
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_awaiting_user_rc_acceptance",
    ]
    blockers: tuple[str, ...]
    summary_hash: str = Field(pattern=_HEX)


class Phase3ESummaryV2(_Model):
    schema_version: Literal["v3-phase3e-summary-v2"]
    hash_domain: Literal["projecttown/v3/phase3e-summary/v2"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_projections: tuple[RoundProjection, RoundProjection]
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_awaiting_user_rc_acceptance",
    ]
    blockers: tuple[str, ...]
    summary_hash: str = Field(pattern=_HEX)


class Phase3ESummaryV3(_Model):
    schema_version: Literal["v3-phase3e-summary-v3"]
    hash_domain: Literal["projecttown/v3/phase3e-summary/v3"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    round_projections: tuple[RoundProjection, RoundProjection]
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_awaiting_user_rc_acceptance",
    ]
    blockers: tuple[str, ...]
    summary_hash: str = Field(pattern=_HEX)


class RoundProjectionV4(_Model):
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"]
    round_hash: str = Field(pattern=_HEX)
    participant_disposition: Literal["retained", "not_kept"] | None
    participant_control_rating: int | None = Field(default=None, ge=1, le=5)
    citation_usable: bool | None
    structural_rewrite: bool | None
    engineering_outcome: Literal["PASS", "FAIL"] | None
    binding_status: Literal["verified", "stale", "conflict", "missing"]
    blocking_defect: bool


class Phase3ESummaryV4(_Model):
    schema_version: Literal["v3-phase3e-summary-v4"]
    hash_domain: Literal["projecttown/v3/phase3e-summary/v4"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    gate_model: Literal["participant_instance_plus_engineering_acceptance_plus_user_v1"]
    round_projections: tuple[RoundProjectionV4, RoundProjectionV4]
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_awaiting_user_rc_acceptance",
    ]
    blockers: tuple[str, ...]
    summary_hash: str = Field(pattern=_HEX)


class UserRCDecision(_Model):
    schema_version: Literal["v3-phase3e-user-rc-decision-v1"]
    hash_domain: Literal["projecttown/v3/phase3e-user-rc-decision/v1"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    summary_hash: str = Field(pattern=_HEX)
    decision: Literal["ACCEPT", "RETAIN", "REVISE", "DISCARD", "STOP"]
    user_timestamp: str
    evidence_path: str
    evidence_sha256: str = Field(pattern=_HEX)
    notes: str = Field(min_length=1, max_length=MAX_NOTES)
    outcome: Literal[
        "rc_accepted_pending_version_gate",
        "retained_no_release_authority",
        "revise_new_candidate_required",
        "discarded_no_release_authority",
        "stopped_no_release_authority",
    ]
    decision_hash: str = Field(pattern=_HEX)

    _evidence_path = field_validator("evidence_path")(_absolute_path_syntax)

    @model_validator(mode="after")
    def _canonical(self) -> UserRCDecision:
        _canonical_timestamp(self.user_timestamp)
        _notes(self.notes)
        expected = {
            "ACCEPT": "rc_accepted_pending_version_gate",
            "RETAIN": "retained_no_release_authority",
            "REVISE": "revise_new_candidate_required",
            "DISCARD": "discarded_no_release_authority",
            "STOP": "stopped_no_release_authority",
        }[self.decision]
        if self.outcome != expected:
            raise ValueError("decision outcome mismatch")
        return self


class UserRCDecisionV2(_Model):
    schema_version: Literal["v3-phase3e-user-rc-decision-v2"]
    hash_domain: Literal["projecttown/v3/phase3e-user-rc-decision/v2"]
    study_id: str
    study_hash: str = Field(pattern=_HEX)
    summary_hash: str = Field(pattern=_HEX)
    decision: Literal["ACCEPT", "RETAIN", "REVISE", "DISCARD", "STOP"]
    user_timestamp: str
    evidence_path: str
    evidence_sha256: str = Field(pattern=_HEX)
    notes: str = Field(min_length=1, max_length=MAX_NOTES)
    outcome: Literal[
        "rc_accepted_pending_version_gate",
        "retained_no_release_authority",
        "revise_new_candidate_required",
        "discarded_no_release_authority",
        "stopped_no_release_authority",
    ]
    decision_hash: str = Field(pattern=_HEX)

    _evidence_path = field_validator("evidence_path")(_absolute_path_syntax)

    @model_validator(mode="after")
    def _canonical(self) -> UserRCDecisionV2:
        _canonical_timestamp(self.user_timestamp)
        _notes(self.notes)
        expected = {
            "ACCEPT": "rc_accepted_pending_version_gate",
            "RETAIN": "retained_no_release_authority",
            "REVISE": "revise_new_candidate_required",
            "DISCARD": "discarded_no_release_authority",
            "STOP": "stopped_no_release_authority",
        }[self.decision]
        if self.outcome != expected:
            raise ValueError("decision outcome mismatch")
        return self


class UserRCDecisionV3(UserRCDecisionV2):
    schema_version: Literal["v3-phase3e-user-rc-decision-v3"]
    hash_domain: Literal["projecttown/v3/phase3e-user-rc-decision/v3"]


class UserRCDecisionV4(UserRCDecisionV2):
    schema_version: Literal["v3-phase3e-user-rc-decision-v4"]
    hash_domain: Literal["projecttown/v3/phase3e-user-rc-decision/v4"]


Study: TypeAlias = Phase3EStudy | Phase3EStudyV2 | Phase3EStudyV3 | Phase3EStudyV4
Round: TypeAlias = Phase3ERound | Phase3ERoundV2 | Phase3ERoundV3 | Phase3ERoundV4
Summary: TypeAlias = (
    Phase3ESummary | Phase3ESummaryV2 | Phase3ESummaryV3 | Phase3ESummaryV4
)
Decision: TypeAlias = (
    UserRCDecision | UserRCDecisionV2 | UserRCDecisionV3 | UserRCDecisionV4
)
Record: TypeAlias = Study | Round | Summary | Decision | SourceSetManifestV2
_SCHEMAS: dict[str, tuple[type[Record], str, str]] = {
    STUDY_SCHEMA: (Phase3EStudy, STUDY_HASH_DOMAIN, "study_hash"),
    ROUND_SCHEMA: (Phase3ERound, ROUND_HASH_DOMAIN, "round_hash"),
    SUMMARY_SCHEMA: (Phase3ESummary, SUMMARY_HASH_DOMAIN, "summary_hash"),
    USER_DECISION_SCHEMA: (UserRCDecision, USER_DECISION_HASH_DOMAIN, "decision_hash"),
    STUDY_SCHEMA_V2: (Phase3EStudyV2, STUDY_HASH_DOMAIN_V2, "study_hash"),
    ROUND_SCHEMA_V2: (Phase3ERoundV2, ROUND_HASH_DOMAIN_V2, "round_hash"),
    SUMMARY_SCHEMA_V2: (Phase3ESummaryV2, SUMMARY_HASH_DOMAIN_V2, "summary_hash"),
    USER_DECISION_SCHEMA_V2: (
        UserRCDecisionV2,
        USER_DECISION_HASH_DOMAIN_V2,
        "decision_hash",
    ),
    SOURCE_SET_SCHEMA_V2: (
        SourceSetManifestV2,
        SOURCE_SET_HASH_DOMAIN_V2,
        "source_set_hash",
    ),
    STUDY_SCHEMA_V3: (Phase3EStudyV3, STUDY_HASH_DOMAIN_V3, "study_hash"),
    ROUND_SCHEMA_V3: (Phase3ERoundV3, ROUND_HASH_DOMAIN_V3, "round_hash"),
    SUMMARY_SCHEMA_V3: (Phase3ESummaryV3, SUMMARY_HASH_DOMAIN_V3, "summary_hash"),
    USER_DECISION_SCHEMA_V3: (
        UserRCDecisionV3,
        USER_DECISION_HASH_DOMAIN_V3,
        "decision_hash",
    ),
    STUDY_SCHEMA_V4: (Phase3EStudyV4, STUDY_HASH_DOMAIN_V4, "study_hash"),
    ROUND_SCHEMA_V4: (Phase3ERoundV4, ROUND_HASH_DOMAIN_V4, "round_hash"),
    SUMMARY_SCHEMA_V4: (Phase3ESummaryV4, SUMMARY_HASH_DOMAIN_V4, "summary_hash"),
    USER_DECISION_SCHEMA_V4: (
        UserRCDecisionV4,
        USER_DECISION_HASH_DOMAIN_V4,
        "decision_hash",
    ),
}


def _record_version(record: object) -> int:
    schema = getattr(record, "schema_version", "")
    if schema.endswith("-v3"):
        return 3
    if schema.endswith("-v4"):
        return 4
    if schema.endswith("-v2"):
        return 2
    if schema.endswith("-v1"):
        return 1
    return 0


def _payload(record: Record, hash_field: str) -> dict[str, object]:
    payload = record.model_dump(mode="json")
    payload.pop(hash_field)
    return payload


def serialize_record(record: Record) -> bytes:
    try:
        schema, domain, field = _SCHEMAS[record.schema_version]
        checked = schema.model_validate_json(
            canonical_json(record.model_dump(mode="json"))
        )
    except (AttributeError, KeyError, ValidationError) as error:
        raise Phase3EError("INVALID_RECORD") from error
    if checked.hash_domain != domain or getattr(checked, field) != _hash(
        domain, _payload(checked, field)
    ):
        raise Phase3EError("INVALID_RECORD_HASH")
    return canonical_json(checked.model_dump(mode="json"))


def parse_record_bytes(data: bytes) -> Record:
    if not isinstance(data, bytes) or len(data) > MAX_RECORD_BYTES:
        raise Phase3EError("INVALID_RECORD")
    try:
        raw = json.loads(
            data.decode("utf-8", "strict"), object_pairs_hook=_reject_duplicates
        )
        schema_name = raw["schema_version"]
        schema, domain, field = _SCHEMAS[schema_name]
        record = schema.model_validate_json(data)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        ValidationError,
        ValueError,
    ) as error:
        raise Phase3EError("INVALID_RECORD") from error
    if record.hash_domain != domain or data != canonical_json(
        record.model_dump(mode="json")
    ):
        raise Phase3EError("NONCANONICAL_RECORD")
    if getattr(record, field) != _hash(domain, _payload(record, field)):
        raise Phase3EError("INVALID_RECORD_HASH")
    return record


def _make(
    model: type[Record], domain: str, hash_field: str, values: dict[str, object]
) -> Record:
    try:
        provisional = model.model_validate({**values, hash_field: "0" * 64})
    except ValidationError as error:
        raise Phase3EError("INVALID_RECORD") from error
    return provisional.model_copy(
        update={hash_field: _hash(domain, _payload(provisional, hash_field))}
    )


def _binding(
    path: Path,
    kind: str,
    schema_version: str | None = None,
    record_hash: str | None = None,
) -> EvidenceBinding | EvidenceBindingV2:
    data, digest = _safe_file(path, "INVALID_EVIDENCE_PATH")
    if schema_version is not None:
        try:
            if kind == "result":
                from .material_workflow import parse_session_bytes

                parsed = parse_session_bytes(data)
            elif kind == "apply_plan":
                from .controlled_apply import parse_apply_plan_bytes

                parsed = parse_apply_plan_bytes(data)
            elif kind == "executable_proposal":
                from .executable_proposal import parse_executable_proposal_bytes

                parsed = parse_executable_proposal_bytes(data)
            elif kind == "user_authorization":
                from .controlled_write import parse_authorization_bytes

                parsed = parse_authorization_bytes(data)
            elif kind == "restore_authorization":
                from .controlled_write import parse_restore_authorization_bytes

                parsed = parse_restore_authorization_bytes(data)
            elif kind in {"apply_receipt", "restore_receipt"}:
                from .controlled_write import parse_receipt_bytes

                parsed = parse_receipt_bytes(data)
            elif kind == "loopback_binding":
                from .v3_loopback_records import parse_record_bytes

                parsed = parse_record_bytes(data)
            else:
                parsed = None
        except (MaterialWorkflowError, ValueError) as error:
            raise Phase3EError("INVALID_CANONICAL_EVIDENCE") from error
        if parsed is not None:
            actual_schema = getattr(parsed, "schema_version", None)
            actual_hash = getattr(
                parsed,
                {
                    "result": "session_hash",
                    "apply_plan": "plan_hash",
                    "executable_proposal": "proposal_hash",
                    "user_authorization": "authorization_hash",
                    "restore_authorization": "authorization_hash",
                    "apply_receipt": "event_hash",
                    "restore_receipt": "event_hash",
                    "loopback_binding": "binding_hash",
                }[kind],
            )
            if actual_schema != schema_version or (
                record_hash is not None and actual_hash != record_hash
            ):
                raise Phase3EError("CANONICAL_EVIDENCE_MISMATCH")
    binding_values = {
        "kind": kind,
        "path": str(path),
        "bytes_sha256": digest,
        "schema_version": schema_version,
        "record_hash": record_hash,
    }
    if kind == "source_set_manifest":
        return EvidenceBindingV2.model_validate(binding_values)
    return EvidenceBinding(
        **binding_values,
    )


def _record_evidence(round_: Round, kind: str) -> EvidenceBinding | EvidenceBindingV2:
    try:
        return next(item for item in round_.evidence if item.kind == kind)
    except StopIteration as error:
        raise Phase3EError("MISSING_REQUIRED_EVIDENCE") from error


def _canonical_record(binding: EvidenceBinding, kind: str):
    if binding.schema_version is None or binding.record_hash is None:
        raise Phase3EError("MISSING_CANONICAL_EVIDENCE_METADATA")
    data, digest = _safe_file(Path(binding.path), "INVALID_EVIDENCE_PATH")
    if digest != binding.bytes_sha256:
        raise Phase3EError("EVIDENCE_BYTES_DRIFT")
    try:
        if kind == "result":
            from .material_workflow import parse_session_bytes, verify_result_integrity

            parsed = parse_session_bytes(data)
            if not verify_result_integrity(parsed):
                raise Phase3EError("INVALID_RESULT_INTEGRITY")
            actual_hash = parsed.session_hash
        elif kind == "apply_plan":
            from .controlled_apply import parse_apply_plan_bytes

            parsed = parse_apply_plan_bytes(data)
            actual_hash = parsed.plan_hash
        elif kind == "executable_proposal":
            from .executable_proposal import parse_executable_proposal_bytes

            parsed = parse_executable_proposal_bytes(data)
            actual_hash = parsed.proposal_hash
        elif kind == "user_authorization":
            from .controlled_write import parse_authorization_bytes

            parsed = parse_authorization_bytes(data)
            actual_hash = parsed.authorization_hash
        elif kind == "restore_authorization":
            from .controlled_write import parse_restore_authorization_bytes

            parsed = parse_restore_authorization_bytes(data)
            actual_hash = parsed.authorization_hash
        elif kind in {"apply_receipt", "restore_receipt"}:
            from .controlled_write import parse_receipt_bytes

            parsed = parse_receipt_bytes(data)
            actual_hash = parsed.event_hash
        elif kind == "loopback_binding":
            from .v3_loopback_records import parse_record_bytes

            parsed = parse_record_bytes(data)
            actual_hash = parsed.binding_hash
        else:
            raise Phase3EError("UNSUPPORTED_CANONICAL_EVIDENCE")
    except (MaterialWorkflowError, ValueError) as error:
        raise Phase3EError("INVALID_CANONICAL_EVIDENCE") from error
    if (
        parsed.schema_version != binding.schema_version
        or actual_hash != binding.record_hash
    ):
        raise Phase3EError("CANONICAL_EVIDENCE_MISMATCH")
    return parsed


def _verify_round2_source_contract(
    study: Phase3EStudyV2 | Phase3EStudyV3,
    round_: Phase3ERoundV2 | Phase3ERoundV3,
    result: object,
) -> bool:
    try:
        source = study.round2_source
        source_binding = _record_evidence(round_, "source_set_manifest")
        if (
            source_binding.path != source.source_set_manifest_path
            or source_binding.bytes_sha256 != source.source_set_manifest_sha256
        ):
            return False
        source_set = load_record(Path(source_binding.path))
        if not isinstance(source_set, SourceSetManifestV2) or (
            source_set.source_set_hash != source.source_set_hash
            or source_set.material_source_root != source.material_source_root
            or source_set.fixed_task != source.fixed_task
        ):
            return False
        refreshed = create_source_set_manifest(Path(source.material_source_root))
        if refreshed != source_set:
            return False
        draft = getattr(result, "draft", None)
        manifest = getattr(draft, "material_manifest", None)
        if (
            getattr(draft, "task", None) != source.fixed_task
            or manifest is None
            or getattr(manifest, "root_hash", None) != source.source_set_root_hash
            or tuple(
                (entry.relative_path, entry.sha256)
                for entry in getattr(manifest, "entries", ())
            )
            != tuple(
                (entry.relative_path, entry.bytes_sha256)
                for entry in source_set.entries
            )
        ):
            return False
        if round_.participant is not None and (
            round_.participant.participant_identity
            != source.expected_participant_identity
        ):
            return False
        return not (
            round_.reviewer is not None
            and round_.reviewer.reviewer_identity != source.expected_reviewer_identity
        )
    except (Phase3EError, ValueError, OSError):
        return False


def _verify_v3_operation(
    operation: ControlledOperationBindingV3,
    authorization: object,
    receipt: object,
    *,
    expected_action: str,
    expected_check_status: Literal["COMMITTED", "TARGET_CHANGED_AFTER_RECEIPT"],
) -> bool:
    """Check the operation's canonical receipt, ledger events and path bindings."""
    try:
        from .controlled_write import (
            BackupManifest,
            ControlledWriteAttention,
            ControlledWriteError,
            PostWriteObservation,
            check,
            parse_event_bytes,
        )

        try:
            if (
                check(Path(operation.authorization_path), Path(operation.ledger_root))
                != expected_check_status
            ):
                return False
        except (ControlledWriteError, ControlledWriteAttention):
            return False

        if (
            getattr(authorization, "operation_id", None) != operation.operation_id
            or getattr(authorization, "ledger_root", None) != operation.ledger_root
            or getattr(receipt, "action", None) != expected_action
            or getattr(receipt, "authorization_path", None)
            != operation.authorization_path
            or getattr(receipt, "state", None) != "COMMITTED"
            or getattr(receipt, "manifest_path", None) != operation.backup_manifest_path
        ):
            return False
        _safe_directory(Path(operation.ledger_root), "INVALID_LEDGER_ROOT")
        manifest_data, _ = _safe_file(
            Path(operation.backup_manifest_path), "INVALID_EVENT_PATH"
        )
        observation_data, _ = _safe_file(
            Path(operation.post_observation_path), "INVALID_EVENT_PATH"
        )
        manifest = parse_event_bytes(manifest_data)
        observation = parse_event_bytes(observation_data)
        return bool(
            isinstance(manifest, BackupManifest)
            and isinstance(observation, PostWriteObservation)
            and manifest.action == expected_action
            and observation.action == expected_action
            and manifest.operation_id == operation.operation_id
            and observation.operation_id == operation.operation_id
            and manifest.authorization_hash
            == getattr(authorization, "authorization_hash", None)
            and observation.authorization_hash
            == getattr(authorization, "authorization_hash", None)
            and manifest.event_hash == getattr(receipt, "manifest_event_hash", None)
            and observation.event_hash
            == getattr(receipt, "final_observation_event_hash", None)
            and observation.observed_sha256
            == getattr(receipt, "target_after_sha256", None)
            and observation.observed_size_bytes
            == getattr(receipt, "target_after_size_bytes", None)
            and observation.expected_match
            and observation.scope_match
            and observation.observed_sha256
            == getattr(receipt, "final_observed_sha256", None)
            and observation.observed_size_bytes
            == getattr(receipt, "target_final_size_bytes", None)
        )
    except (Phase3EError, ValueError, OSError):
        return False


def _verify_round1_contract_v3(study: Phase3EStudyV3, round_: Phase3ERoundV3) -> bool:
    try:
        contract = study.round1_contract
        result = _canonical_record(_record_evidence(round_, "result"), "result")
        plan = _canonical_record(_record_evidence(round_, "apply_plan"), "apply_plan")
        proposal = _canonical_record(
            _record_evidence(round_, "executable_proposal"), "executable_proposal"
        )
        auth = _canonical_record(
            _record_evidence(round_, "user_authorization"), "user_authorization"
        )
        restore_auth = _canonical_record(
            _record_evidence(round_, "restore_authorization"), "restore_authorization"
        )
        apply_receipt = _canonical_record(
            _record_evidence(round_, "apply_receipt"), "apply_receipt"
        )
        restore_receipt = _canonical_record(
            _record_evidence(round_, "restore_receipt"), "restore_receipt"
        )
        if (
            _record_evidence(round_, "result").path != auth.result_path
            or _record_evidence(round_, "result").bytes_sha256
            != auth.result_bytes_sha256
            or _record_evidence(round_, "apply_plan").path != auth.plan_path
            or _record_evidence(round_, "apply_plan").bytes_sha256
            != auth.plan_bytes_sha256
            or _record_evidence(round_, "executable_proposal").path
            != auth.proposal_path
            or _record_evidence(round_, "executable_proposal").bytes_sha256
            != auth.proposal_bytes_sha256
            or plan.result_bytes_sha256
            != _record_evidence(round_, "result").bytes_sha256
            or proposal.apply_plan_bytes_sha256
            != _record_evidence(round_, "apply_plan").bytes_sha256
            or proposal.result_bytes_sha256
            != _record_evidence(round_, "result").bytes_sha256
            or proposal.post_image_sha256 != auth.after_sha256
            or proposal.post_image_size_bytes != auth.after_size_bytes
        ):
            return False
        draft = result.draft
        expected_entries = tuple(
            sorted(
                (
                    (
                        contract.target_relative_path,
                        contract.initial_sha256,
                        contract.initial_size_bytes,
                    ),
                    *(
                        (item.relative_path, item.bytes_sha256, item.size_bytes)
                        for item in contract.source_entries
                    ),
                )
            )
        )
        if (
            result.schema_version != study.lineage.material_result_schema
            or draft.artifact_kind != "readme"
            or draft.readme_target != contract.target_relative_path
            or draft.task != contract.exact_task
            or draft.constraints != contract.constraints
            or draft.selections != tuple(item[0] for item in expected_entries)
            or tuple(
                (item.relative_path, item.sha256, item.size_bytes)
                for item in draft.material_manifest.entries
            )
            != expected_entries
        ):
            return False
        if (
            round_.apply_operation.authorization_path
            != _record_evidence(round_, "user_authorization").path
            or round_.restore_operation.authorization_path
            != _record_evidence(round_, "restore_authorization").path
            or round_.apply_operation.receipt_path
            != _record_evidence(round_, "apply_receipt").path
            or round_.restore_operation.receipt_path
            != _record_evidence(round_, "restore_receipt").path
        ):
            return False
        if not _verify_v3_operation(
            round_.apply_operation,
            auth,
            apply_receipt,
            expected_action="apply-proposal-v1",
            expected_check_status="TARGET_CHANGED_AFTER_RECEIPT",
        ):
            return False
        if not _verify_v3_operation(
            round_.restore_operation,
            restore_auth,
            restore_receipt,
            expected_action="restore-backup-v1",
            expected_check_status="COMMITTED",
        ):
            return False
        if (
            auth.operation_id == restore_auth.operation_id
            or auth.nonce == restore_auth.nonce
            or auth.material_root != contract.material_root
            or auth.target_path != contract.target_path
            or auth.target_relative_path != contract.target_relative_path
            or restore_auth.material_root != auth.material_root
            or restore_auth.target_path != auth.target_path
            or restore_auth.target_relative_path != auth.target_relative_path
            or auth.before_sha256 != contract.initial_sha256
            or auth.before_size_bytes != contract.initial_size_bytes
            or auth.before_permission_mode != contract.initial_permission_mode
            or auth.after_sha256 != contract.expected_post_sha256
            or auth.after_size_bytes != contract.expected_post_size_bytes
            or apply_receipt.target_before_sha256 != contract.initial_sha256
            or apply_receipt.target_after_sha256 != contract.expected_post_sha256
            or restore_auth.original_receipt_path
            != _record_evidence(round_, "apply_receipt").path
            or restore_auth.original_receipt_bytes_sha256
            != _record_evidence(round_, "apply_receipt").bytes_sha256
            or restore_auth.original_receipt_hash != apply_receipt.event_hash
            or restore_auth.source_backup_path != apply_receipt.backup_path
            or restore_auth.source_backup_sha256 != apply_receipt.backup_sha256
            or restore_auth.source_backup_size_bytes != apply_receipt.backup_size_bytes
            or restore_auth.source_backup_manifest_path
            != round_.apply_operation.backup_manifest_path
            or restore_auth.source_backup_manifest_hash
            != apply_receipt.manifest_event_hash
            or restore_receipt.target_after_sha256 != contract.initial_sha256
            or restore_receipt.target_after_size_bytes != contract.initial_size_bytes
            or restore_receipt.target_after_permission_mode
            != contract.initial_permission_mode
        ):
            return False
        _, final_digest = _safe_file(Path(contract.target_path), "TARGET_UNAVAILABLE")
        return (
            final_digest == contract.initial_sha256
            and Path(contract.target_path).stat().st_size == contract.initial_size_bytes
            and _safe_file_permission_mode(
                Path(contract.target_path), "TARGET_UNAVAILABLE"
            )
            == contract.initial_permission_mode
        )
    except (Phase3EError, OSError):
        return False


_V4_PREDECESSOR_ROLE_BY_EVIDENCE = {
    "result": "result",
    "apply_plan": "apply_plan",
    "executable_proposal": "executable_proposal",
    "user_authorization": "apply_authorization",
    "restore_authorization": "restore_authorization",
    "apply_receipt": "apply_receipt",
    "restore_receipt": "restore_receipt",
    "backup": "apply_backup",
    "restore_backup": "restore_backup",
}


def _verify_predecessor_contract_v4(study: Phase3EStudyV4) -> bool:
    """Check that inherited v3 evidence is immutable history, never an execution grant."""
    try:
        contract = study.round1_contract
        if contract.predecessor_study_hash is None:
            return False
        items = {item.role: item for item in contract.predecessor_evidence}
        if len(items) != 10:
            return False
        roots = (
            Path(study.study_root),
            Path(study.work_root),
            Path(study.round2_source.material_source_root),
            Path(__file__).resolve().parents[2],
        )
        for item in items.values():
            path = Path(item.path)
            _, digest = _safe_file(path, "INVALID_PREDECESSOR_EVIDENCE")
            if digest != item.bytes_sha256 or any(
                path == root or path.is_relative_to(root) for root in roots
            ):
                return False
        prior = load_record(Path(items["v3_study"].path))
        return (
            isinstance(prior, Phase3EStudyV3)
            and verify_record(prior)
            and prior.study_hash == contract.predecessor_study_hash
            and contract.target_path == prior.round1_contract.target_path
            and contract.material_root == prior.round1_contract.material_root
        )
    except (Phase3EError, OSError, ValueError):
        return False


def _verify_round_binding(study: Study, round_: Round) -> bool:
    try:
        if round_.binding_status != "verified":
            return False
        if isinstance(study, Phase3EStudyV2):
            source = study.round2_source
            if round_.participant is not None and (
                round_.participant.participant_identity
                != source.expected_participant_identity
            ):
                return False
            if round_.reviewer is not None and (
                round_.reviewer.reviewer_identity != source.expected_reviewer_identity
            ):
                return False
        if isinstance(study, Phase3EStudyV3):
            expected = (
                study.round1_contract
                if round_.round_id == ROUND_IDS[0]
                else study.round2_source
            )
            if round_.participant is not None and (
                round_.participant.participant_identity
                != expected.expected_participant_identity
            ):
                return False
            if round_.reviewer is not None and (
                round_.reviewer.reviewer_identity != expected.expected_reviewer_identity
            ):
                return False
        if isinstance(study, Phase3EStudyV4):
            if not isinstance(
                round_, Phase3ERoundV4
            ) or not _verify_predecessor_contract_v4(study):
                return False
            expected_identity = (
                study.round1_contract.expected_participant_identity
                if round_.round_id == ROUND_IDS[0]
                else study.round2_source.expected_participant_identity
            )
            if (
                round_.participant is None
                or round_.engineering_acceptance is None
                or round_.participant.participant_identity != expected_identity
                or not round_.engineering_acceptance.citation_traceable
                or not round_.engineering_acceptance.citation_usable
                or round_.engineering_acceptance.blocking_defect
                or round_.engineering_acceptance.outcome != "PASS"
            ):
                return False
        for evidence in round_.evidence:
            if evidence.kind == "material_manifest":
                if (
                    evidence.path != study.lineage.manifest_path
                    or evidence.bytes_sha256 != study.lineage.manifest_sha256
                ):
                    return False
            elif evidence.kind not in {
                "result",
                "apply_plan",
                "executable_proposal",
                "user_authorization",
                "restore_authorization",
                "apply_receipt",
                "restore_receipt",
                "loopback_binding",
            }:
                _, digest = _safe_file(Path(evidence.path), "INVALID_EVIDENCE_PATH")
                if digest != evidence.bytes_sha256:
                    return False
        result = _canonical_record(_record_evidence(round_, "result"), "result")
        if round_.round_id == ROUND_IDS[1]:
            base = (
                result.schema_version == study.lineage.material_result_schema
                and bool(result.citations)
            )
            if isinstance(study, Phase3EStudyV2):
                return (
                    base
                    and isinstance(round_, Phase3ERoundV2)
                    and _verify_round2_source_contract(study, round_, result)
                )
            if isinstance(study, Phase3EStudyV3):
                return (
                    base
                    and isinstance(round_, Phase3ERoundV3)
                    and _verify_round2_source_contract(study, round_, result)
                )
            if isinstance(study, Phase3EStudyV4):
                source = study.round2_source
                manifest = getattr(
                    getattr(result, "draft", None), "material_manifest", None
                )
                return (
                    base
                    and getattr(getattr(result, "draft", None), "task", None)
                    == source.fixed_task
                    and manifest is not None
                    and getattr(manifest, "root_hash", None)
                    == source.source_set_root_hash
                    and tuple(
                        (entry.relative_path, entry.sha256)
                        for entry in getattr(manifest, "entries", ())
                    )
                    == tuple(
                        (entry.relative_path, entry.bytes_sha256)
                        for entry in load_record(
                            Path(source.source_set_manifest_path)
                        ).entries
                    )
                    and _record_evidence(round_, "source_set_manifest").path
                    == source.source_set_manifest_path
                    and _record_evidence(round_, "source_set_manifest").bytes_sha256
                    == source.source_set_manifest_sha256
                    and round_.participant is not None
                    and round_.participant.evidence_path
                    == _record_evidence(round_, "pdf_export").path
                )
            return base
        if isinstance(study, Phase3EStudyV3) and isinstance(round_, Phase3ERoundV3):
            return _verify_round1_contract_v3(study, round_)
        if isinstance(study, Phase3EStudyV4):
            contract_items = {
                item.role: item for item in study.round1_contract.predecessor_evidence
            }
            for (
                evidence_kind,
                predecessor_role,
            ) in _V4_PREDECESSOR_ROLE_BY_EVIDENCE.items():
                binding = _record_evidence(round_, evidence_kind)
                predecessor = contract_items[predecessor_role]
                if (
                    binding.path != predecessor.path
                    or binding.bytes_sha256 != predecessor.bytes_sha256
                ):
                    return False
        plan = _canonical_record(_record_evidence(round_, "apply_plan"), "apply_plan")
        proposal = _canonical_record(
            _record_evidence(round_, "executable_proposal"), "executable_proposal"
        )
        authorization = _canonical_record(
            _record_evidence(round_, "user_authorization"), "user_authorization"
        )
        restore_authorization = _canonical_record(
            _record_evidence(round_, "restore_authorization"),
            "restore_authorization",
        )
        receipt = _canonical_record(
            _record_evidence(round_, "apply_receipt"), "apply_receipt"
        )
        restore_receipt = _canonical_record(
            _record_evidence(round_, "restore_receipt"), "restore_receipt"
        )
        result_binding, plan_binding, proposal_binding, auth_binding = (
            _record_evidence(round_, name)
            for name in (
                "result",
                "apply_plan",
                "executable_proposal",
                "user_authorization",
            )
        )
        backup = _record_evidence(round_, "backup")
        restore_backup = _record_evidence(round_, "restore_backup")
        return not (
            result.schema_version != study.lineage.material_result_schema
            or plan.result_bytes_sha256 != result_binding.bytes_sha256
            or proposal.apply_plan_bytes_sha256 != plan_binding.bytes_sha256
            or proposal.result_bytes_sha256 != result_binding.bytes_sha256
            or authorization.proposal_bytes_sha256 != proposal_binding.bytes_sha256
            or authorization.plan_bytes_sha256 != plan_binding.bytes_sha256
            or authorization.result_bytes_sha256 != result_binding.bytes_sha256
            or authorization.proposal_path != proposal_binding.path
            or authorization.plan_path != plan_binding.path
            or authorization.result_path != result_binding.path
            or receipt.action != "apply-proposal-v1"
            or receipt.authorization_path != auth_binding.path
            or receipt.target_path != authorization.target_path
            or receipt.backup_path != backup.path
            or restore_authorization.original_receipt_path
            != _record_evidence(round_, "apply_receipt").path
            or restore_authorization.source_backup_path != backup.path
            or restore_authorization.target_path != authorization.target_path
            or restore_receipt.action != "restore-backup-v1"
            or restore_receipt.authorization_path
            != _record_evidence(round_, "restore_authorization").path
            or restore_receipt.target_path != authorization.target_path
            or restore_receipt.backup_path != restore_backup.path
            or restore_receipt.target_after_sha256 != receipt.target_before_sha256
            or restore_receipt.target_after_size_bytes
            != receipt.target_before_size_bytes
        )
    except Phase3EError:
        return False


def create_study(
    study_id: str,
    study_root: Path,
    work_root: Path,
    manifest_path: Path,
    *,
    p0: P0Values,
    material_source_root: Path | None = None,
    source_set_manifest_path: Path | None = None,
    expected_participant_identity: str | None = None,
    expected_reviewer_identity: str | None = None,
    round1_contract: Round1ContractV3 | Round1ContractV4 | None = None,
    expected_post_image_path: Path | None = None,
    protocol_v4: bool = False,
) -> Study:
    study_root, work_root = (
        _safe_directory(study_root, "INVALID_STUDY_ROOT"),
        _safe_directory(work_root, "INVALID_WORK_ROOT"),
    )
    if study_root.parent != work_root.parent or study_root == work_root:
        raise Phase3EError("INVALID_SIBLING_ROOTS")
    repository = Path(__file__).resolve().parents[2]
    expected_manifest = repository / "examples" / "v3-phase-3"
    v1_manifest = expected_manifest / "projecttown-phase3e-manifest-v1.json"
    v2_manifest = expected_manifest / "projecttown-phase3e-manifest-v2.json"
    v3_manifest = expected_manifest / "projecttown-phase3e-manifest-v3.json"
    v4_manifest = expected_manifest / "projecttown-phase3e-manifest-v4.json"
    v2_requested = material_source_root is not None
    v3_requested = isinstance(round1_contract, Round1ContractV3)
    v4_requested = protocol_v4
    if v4_requested and not isinstance(round1_contract, Round1ContractV4):
        raise Phase3EError("MISSING_V4_ROUND1_CONTRACT")
    if (v3_requested or v4_requested) and not v2_requested:
        raise Phase3EError("MISSING_V3_SOURCE_BINDING")
    if not v4_requested and (expected_post_image_path is not None) != v3_requested:
        raise Phase3EError("INVALID_R1_EXPECTED_POST_IMAGE")
    if manifest_path != (
        v4_manifest
        if v4_requested
        else v3_manifest
        if v3_requested
        else v2_manifest
        if v2_requested
        else v1_manifest
    ):
        raise Phase3EError("INVALID_MANIFEST_PATH")
    _, manifest_hash = _safe_file(manifest_path, "MANIFEST_UNAVAILABLE")
    if not v2_requested:
        if any(
            value is not None
            for value in (
                source_set_manifest_path,
                expected_participant_identity,
                expected_reviewer_identity,
            )
        ):
            raise Phase3EError("V1_STUDY_DOES_NOT_ACCEPT_V2_BINDING")
        return _make(
            Phase3EStudy,
            STUDY_HASH_DOMAIN,
            "study_hash",
            {
                "schema_version": STUDY_SCHEMA,
                "hash_domain": STUDY_HASH_DOMAIN,
                "study_id": study_id,
                "study_root": str(study_root),
                "work_root": str(work_root),
                "lineage": CandidateLineage(
                    candidate_profile=CANDIDATE_PROFILE,
                    procedure_version="phase3e-release-candidate-v1",
                    material_result_schema="v3-material-result-session-v1",
                    controlled_write_authorization_schema="v3-controlled-write-authorization-v1",
                    manifest_path=str(manifest_path),
                    manifest_sha256=manifest_hash,
                ),
                "round_ids": ROUND_IDS,
                "p0": p0,
            },
        )  # type: ignore[return-value]
    if (
        source_set_manifest_path is None
        or expected_participant_identity is None
        or (not v4_requested and expected_reviewer_identity is None)
    ):
        raise Phase3EError("MISSING_V2_SOURCE_BINDING")
    material_source_root = _safe_directory(
        material_source_root, "INVALID_MATERIAL_SOURCE_ROOT"
    )
    if any(
        material_source_root == root
        or material_source_root.is_relative_to(root)
        or root.is_relative_to(material_source_root)
        for root in (study_root, work_root, repository)
    ):
        raise Phase3EError("INVALID_MATERIAL_SOURCE_ROOT")
    expected_source_manifest = work_root / "source-set-manifest.json"
    if source_set_manifest_path != expected_source_manifest:
        raise Phase3EError("INVALID_SOURCE_SET_MANIFEST_PATH")
    source_set = load_record(source_set_manifest_path)
    if not isinstance(source_set, SourceSetManifestV2) or (
        source_set.material_source_root != str(material_source_root)
        or source_set != create_source_set_manifest(material_source_root)
    ):
        raise Phase3EError("SOURCE_SET_MANIFEST_MISMATCH")
    _, source_set_bytes = _safe_file(
        source_set_manifest_path, "SOURCE_SET_MANIFEST_UNAVAILABLE"
    )
    if v4_requested:
        assert isinstance(round1_contract, Round1ContractV4)
        study = _make(
            Phase3EStudyV4,
            STUDY_HASH_DOMAIN_V4,
            "study_hash",
            {
                "schema_version": STUDY_SCHEMA_V4,
                "hash_domain": STUDY_HASH_DOMAIN_V4,
                "study_id": study_id,
                "study_root": str(study_root),
                "work_root": str(work_root),
                "lineage": CandidateLineageV4(
                    candidate_profile=CANDIDATE_PROFILE_V4,
                    procedure_version="phase3e-release-candidate-v4",
                    material_result_schema="v3-material-result-session-v1",
                    controlled_write_authorization_schema="v3-controlled-write-authorization-v1",
                    manifest_path=str(manifest_path),
                    manifest_sha256=manifest_hash,
                ),
                "gate_model": GATE_MODEL_V4,
                "expected_participant_identity": expected_participant_identity,
                "round_ids": ROUND_IDS,
                "p0": p0,
                "round1_contract": round1_contract,
                "round2_source": Round2SourceContractV4(
                    material_source_root=str(material_source_root),
                    fixed_task=_V2_FIXED_TASK,
                    source_set_manifest_path=str(source_set_manifest_path),
                    source_set_manifest_sha256=source_set_bytes,
                    source_set_hash=source_set.source_set_hash,
                    source_set_root_hash=source_set.source_set_root_hash,
                    expected_participant_identity=expected_participant_identity,
                ),
            },
        )
        if not isinstance(study, Phase3EStudyV4) or not _verify_predecessor_contract_v4(
            study
        ):
            raise Phase3EError("INVALID_V4_PREDECESSOR_CONTRACT")
        return study
    if v3_requested:
        _verify_round1_contract_files(
            round1_contract,
            study_root=study_root,
            work_root=work_root,
            repository=repository,
            round2_material_source_root=material_source_root,
            expected_post_image_path=expected_post_image_path,
        )
        return _make(
            Phase3EStudyV3,
            STUDY_HASH_DOMAIN_V3,
            "study_hash",
            {
                "schema_version": STUDY_SCHEMA_V3,
                "hash_domain": STUDY_HASH_DOMAIN_V3,
                "study_id": study_id,
                "study_root": str(study_root),
                "work_root": str(work_root),
                "lineage": CandidateLineageV3(
                    candidate_profile=CANDIDATE_PROFILE_V3,
                    procedure_version="phase3e-release-candidate-v3",
                    material_result_schema="v3-material-result-session-v1",
                    controlled_write_authorization_schema="v3-controlled-write-authorization-v1",
                    manifest_path=str(manifest_path),
                    manifest_sha256=manifest_hash,
                ),
                "round_ids": ROUND_IDS,
                "p0": p0,
                "round1_contract": round1_contract,
                "round2_source": Round2SourceContractV2(
                    material_source_root=str(material_source_root),
                    fixed_task=_V2_FIXED_TASK,
                    source_set_manifest_path=str(source_set_manifest_path),
                    source_set_manifest_sha256=source_set_bytes,
                    source_set_hash=source_set.source_set_hash,
                    source_set_root_hash=source_set.source_set_root_hash,
                    expected_participant_identity=expected_participant_identity,
                    expected_reviewer_identity=expected_reviewer_identity,
                ),
            },
        )  # type: ignore[return-value]
    return _make(
        Phase3EStudyV2,
        STUDY_HASH_DOMAIN_V2,
        "study_hash",
        {
            "schema_version": STUDY_SCHEMA_V2,
            "hash_domain": STUDY_HASH_DOMAIN_V2,
            "study_id": study_id,
            "study_root": str(study_root),
            "work_root": str(work_root),
            "lineage": CandidateLineageV2(
                candidate_profile=CANDIDATE_PROFILE_V2,
                procedure_version="phase3e-release-candidate-v2",
                material_result_schema="v3-material-result-session-v1",
                controlled_write_authorization_schema="v3-controlled-write-authorization-v1",
                manifest_path=str(manifest_path),
                manifest_sha256=manifest_hash,
            ),
            "round_ids": ROUND_IDS,
            "p0": p0,
            "round2_source": Round2SourceContractV2(
                material_source_root=str(material_source_root),
                fixed_task=_V2_FIXED_TASK,
                source_set_manifest_path=str(source_set_manifest_path),
                source_set_manifest_sha256=source_set_bytes,
                source_set_hash=source_set.source_set_hash,
                source_set_root_hash=source_set.source_set_root_hash,
                expected_participant_identity=expected_participant_identity,
                expected_reviewer_identity=expected_reviewer_identity,
            ),
        },
    )  # type: ignore[return-value]


def create_source_set_manifest(material_source_root: Path) -> SourceSetManifestV2:
    root = _safe_directory(material_source_root, "INVALID_MATERIAL_SOURCE_ROOT")
    entries: list[SourceSetEntryV2] = []
    for relative_path in _V2_SOURCE_PATHS:
        _, digest = _safe_file(root / relative_path, "SOURCE_SET_SOURCE_UNAVAILABLE")
        entries.append(
            SourceSetEntryV2(relative_path=relative_path, bytes_sha256=digest)
        )
    try:
        root_hash = inspect_material_set(root, _V2_SOURCE_PATHS).root_hash
    except MaterialWorkflowError as error:
        raise Phase3EError("SOURCE_SET_SOURCE_UNAVAILABLE") from error
    return _make(
        SourceSetManifestV2,
        SOURCE_SET_HASH_DOMAIN_V2,
        "source_set_hash",
        {
            "schema_version": SOURCE_SET_SCHEMA_V2,
            "hash_domain": SOURCE_SET_HASH_DOMAIN_V2,
            "material_source_root": str(root),
            "fixed_task": _V2_FIXED_TASK,
            "entries": tuple(entries),
            "source_set_root_hash": root_hash,
        },
    )  # type: ignore[return-value]


def create_round1_contract_v3(
    *,
    material_root: Path,
    source_paths: tuple[Path, ...],
    no_external_sources: bool,
    exact_task: str,
    constraints: tuple[tuple[str, str], ...],
    target_path: Path,
    expected_post_image_path: Path,
    restore_executor_label: str,
    expected_participant_identity: str,
    expected_reviewer_identity: str,
) -> Round1ContractV3:
    root = _safe_directory(material_root, "INVALID_R1_MATERIAL_ROOT")
    entries: list[Round1SourceEntryV3] = []
    for source_path in source_paths:
        try:
            relative = source_path.relative_to(root)
        except ValueError as error:
            raise Phase3EError("R1_SOURCE_OUTSIDE_MATERIAL_ROOT") from error
        data, digest = _safe_file(source_path, "INVALID_R1_SOURCE_PATH")
        entries.append(
            Round1SourceEntryV3(
                relative_path=relative.as_posix(),
                bytes_sha256=digest,
                size_bytes=len(data),
            )
        )
    try:
        target_relative = target_path.relative_to(root)
    except ValueError as error:
        raise Phase3EError("R1_TARGET_OUTSIDE_MATERIAL_ROOT") from error
    target_data, target_digest = _safe_file(target_path, "INVALID_R1_TARGET_PATH")
    post_data, post_digest = _safe_file(
        expected_post_image_path, "INVALID_R1_EXPECTED_POST_IMAGE"
    )
    contract = Round1ContractV3(
        material_root=str(root),
        source_entries=tuple(sorted(entries, key=lambda item: item.relative_path)),
        no_external_sources=no_external_sources,
        exact_task=exact_task,
        constraints=constraints,
        target_path=str(target_path),
        target_relative_path=target_relative.as_posix(),
        initial_sha256=target_digest,
        initial_size_bytes=len(target_data),
        initial_permission_mode=_safe_file_permission_mode(
            target_path, "INVALID_R1_TARGET_PATH"
        ),
        expected_post_sha256=post_digest,
        expected_post_size_bytes=len(post_data),
        restore_executor_label=restore_executor_label,
        identity_attestation_mode="self_attested_privacy_label_v1",
        expected_participant_identity=expected_participant_identity,
        expected_reviewer_identity=expected_reviewer_identity,
    )
    if no_external_sources != (not source_paths):
        raise Phase3EError("R1_SOURCE_POLICY_MISMATCH")
    return contract


def create_round(
    study: Study,
    *,
    round_id: Literal["R1-CONTROLLED-APPLY", "R2-REPORT-EXPORT"],
    binding_status: Literal["verified", "stale", "conflict", "missing"],
    target_is_disposable_external_fixture: bool,
    evidence_paths: tuple[tuple[str, Path] | tuple[str, Path, str, str | None], ...],
    participant: ParticipantEvidence | None = None,
    reviewer: ReviewerEvidence | None = None,
    engineering_acceptance: EngineeringAcceptanceV4 | None = None,
    citation_usable: bool | None = None,
    structural_rewrite: bool | None = None,
    blocking_defect: bool = False,
    apply_operation: ControlledOperationBindingV3 | None = None,
    restore_operation: ControlledOperationBindingV3 | None = None,
) -> Round:
    serialize_record(study)
    if isinstance(study, Phase3EStudyV3):
        raise Phase3EError("PROTOCOL_HOLD")
    for input_ in evidence_paths:
        _, path, *_ = input_
        if input_[0] == "material_manifest" and path == Path(
            study.lineage.manifest_path
        ):
            continue
        if (
            isinstance(study, (Phase3EStudyV2, Phase3EStudyV3, Phase3EStudyV4))
            and input_[0] == "source_set_manifest"
            and path == Path(study.round2_source.source_set_manifest_path)
        ):
            continue
        if isinstance(study, Phase3EStudyV4) and round_id == ROUND_IDS[0]:
            predecessor_role = _V4_PREDECESSOR_ROLE_BY_EVIDENCE.get(input_[0])
            if predecessor_role is not None:
                predecessor = next(
                    item
                    for item in study.round1_contract.predecessor_evidence
                    if item.role == predecessor_role
                )
                _, digest = _safe_file(path, "INVALID_EVIDENCE_PATH")
                if str(path) != predecessor.path or digest != predecessor.bytes_sha256:
                    raise Phase3EError("V4_PREDECESSOR_EVIDENCE_MISMATCH")
                continue
        try:
            path.relative_to(Path(study.work_root))
        except ValueError as error:
            raise Phase3EError("EVIDENCE_OUTSIDE_WORK_ROOT") from error
    if participant is not None:
        try:
            Path(participant.evidence_path).relative_to(Path(study.work_root))
        except ValueError as error:
            raise Phase3EError("PARTICIPANT_EVIDENCE_OUTSIDE_WORK_ROOT") from error
        _, participant_digest = _safe_file(
            Path(participant.evidence_path), "INVALID_PARTICIPANT_EVIDENCE_PATH"
        )
        participant = participant.model_copy(
            update={"evidence_sha256": participant_digest}
        )
    if reviewer is not None:
        try:
            Path(reviewer.evidence_path).relative_to(Path(study.work_root))
        except ValueError as error:
            raise Phase3EError("REVIEWER_EVIDENCE_OUTSIDE_WORK_ROOT") from error
        _, reviewer_digest = _safe_file(
            Path(reviewer.evidence_path), "INVALID_REVIEWER_EVIDENCE_PATH"
        )
        reviewer = reviewer.model_copy(update={"evidence_sha256": reviewer_digest})
    if engineering_acceptance is not None:
        try:
            checked_engineering = EngineeringAcceptanceV4.model_validate_json(
                canonical_json(engineering_acceptance.model_dump(mode="json"))
            )
        except ValidationError as error:
            raise Phase3EError("INVALID_ENGINEERING_ACCEPTANCE") from error
        try:
            Path(checked_engineering.evidence_path).relative_to(Path(study.work_root))
        except ValueError as error:
            raise Phase3EError("ENGINEERING_EVIDENCE_OUTSIDE_WORK_ROOT") from error
        _, digest = _safe_file(
            Path(checked_engineering.evidence_path),
            "INVALID_ENGINEERING_EVIDENCE_PATH",
        )
        engineering_acceptance = create_engineering_acceptance_v4(
            outcome=checked_engineering.outcome,
            verifier_identity=checked_engineering.verifier_identity,
            checks=checked_engineering.checks,
            notes=checked_engineering.notes,
            actions=checked_engineering.actions,
            timestamp=checked_engineering.timestamp,
            evidence_path=checked_engineering.evidence_path,
            evidence_sha256=digest,
            citation_traceable=checked_engineering.citation_traceable,
            citation_usable=checked_engineering.citation_usable,
            blocking_defect=checked_engineering.blocking_defect,
        )
    evidence = tuple(_binding(item[1], item[0], *item[2:]) for item in evidence_paths)
    if isinstance(study, Phase3EStudyV2):
        evidence = tuple(
            EvidenceBindingV2.model_validate(item.model_dump(mode="json"))
            for item in evidence
        )
    if isinstance(study, Phase3EStudyV3):
        evidence = tuple(
            EvidenceBindingV3.model_validate(item.model_dump(mode="json"))
            for item in evidence
        )
    if isinstance(study, Phase3EStudyV4):
        evidence = tuple(
            EvidenceBindingV4.model_validate(item.model_dump(mode="json"))
            for item in evidence
        )
        for operation in (apply_operation, restore_operation):
            if operation is not None:
                for value in (
                    operation.authorization_path,
                    operation.ledger_root,
                    operation.backup_manifest_path,
                    operation.post_observation_path,
                    operation.receipt_path,
                ):
                    try:
                        Path(value).relative_to(Path(study.work_root))
                    except ValueError as error:
                        raise Phase3EError("EVIDENCE_OUTSIDE_WORK_ROOT") from error
    round_model: type[Round] = (
        Phase3ERoundV4
        if isinstance(study, Phase3EStudyV4)
        else Phase3ERoundV3
        if isinstance(study, Phase3EStudyV3)
        else Phase3ERoundV2
        if isinstance(study, Phase3EStudyV2)
        else Phase3ERound
    )
    round_domain = (
        ROUND_HASH_DOMAIN_V4
        if isinstance(study, Phase3EStudyV4)
        else ROUND_HASH_DOMAIN_V3
        if isinstance(study, Phase3EStudyV3)
        else ROUND_HASH_DOMAIN_V2
        if isinstance(study, Phase3EStudyV2)
        else ROUND_HASH_DOMAIN
    )
    values: dict[str, object] = {
        "schema_version": ROUND_SCHEMA_V4
        if isinstance(study, Phase3EStudyV4)
        else ROUND_SCHEMA_V3
        if isinstance(study, Phase3EStudyV3)
        else ROUND_SCHEMA_V2
        if isinstance(study, Phase3EStudyV2)
        else ROUND_SCHEMA,
        "hash_domain": round_domain,
        "study_id": study.study_id,
        "study_hash": study.study_hash,
        "round_id": round_id,
        "round_kind": "controlled_apply"
        if round_id == ROUND_IDS[0]
        else "report_export",
        "binding_status": binding_status,
        "target_is_disposable_external_fixture": target_is_disposable_external_fixture,
        "evidence": evidence,
        "participant": participant,
        "reviewer": reviewer,
        "citation_usable": citation_usable,
        "structural_rewrite": structural_rewrite,
        "blocking_defect": blocking_defect,
    }
    if isinstance(study, Phase3EStudyV3):
        values.update(
            apply_operation=apply_operation, restore_operation=restore_operation
        )
    if isinstance(study, Phase3EStudyV4):
        values.pop("reviewer")
        values.pop("citation_usable")
        values.pop("structural_rewrite")
        values.pop("blocking_defect")
        values["engineering_acceptance"] = engineering_acceptance
    value = _make(
        round_model,
        round_domain,
        "round_hash",
        values,
    )  # type: ignore[assignment]
    if not isinstance(
        value, (Phase3ERound, Phase3ERoundV2, Phase3ERoundV3, Phase3ERoundV4)
    ):
        raise Phase3EError("INVALID_ROUND")
    if round_id == ROUND_IDS[1] and participant is not None:
        export = next(item for item in value.evidence if item.kind == "pdf_export")
        if participant.evidence_path != export.path:
            raise Phase3EError("PARTICIPANT_EVIDENCE_PATH_MISMATCH")
    if binding_status == "verified" and not _verify_round_binding(study, value):
        raise Phase3EError("UNPROVEN_VERIFIED_BINDING")
    return value


def create_summary(study: Study, rounds: tuple[Round, Round]) -> Summary:
    if isinstance(study, Phase3EStudyV3):
        raise Phase3EError("PROTOCOL_HOLD")
    if not verify_record(study):
        raise Phase3EError("INVALID_STUDY_BINDING")
    if tuple(item.round_id for item in rounds) != ROUND_IDS or any(
        item.study_id != study.study_id or item.study_hash != study.study_hash
        for item in rounds
    ):
        raise Phase3EError("ROUND_BINDING_MISMATCH")
    for round_ in rounds:
        if not verify_record(round_) or (
            round_.binding_status == "verified"
            and not _verify_round_binding(study, round_)
        ):
            raise Phase3EError("INVALID_ROUND_BINDING")
    if isinstance(study, Phase3EStudyV4):
        if not all(isinstance(item, Phase3ERoundV4) for item in rounds):
            raise Phase3EError("ROUND_BINDING_MISMATCH")
        v4_rounds = tuple(rounds)  # retained for type narrowing and canonical ordering
        projections = tuple(
            RoundProjectionV4(
                round_id=item.round_id,
                round_hash=item.round_hash,
                participant_disposition=None
                if item.participant is None
                else item.participant.disposition,
                participant_control_rating=None
                if item.participant is None
                else item.participant.control_rating,
                citation_usable=None
                if item.participant is None
                else item.participant.citation_usable,
                structural_rewrite=None
                if item.participant is None
                else item.participant.structural_rewrite,
                engineering_outcome=None
                if item.engineering_acceptance is None
                else item.engineering_acceptance.outcome,
                binding_status=item.binding_status,
                blocking_defect=False
                if item.engineering_acceptance is None
                else item.engineering_acceptance.blocking_defect,
            )
            for item in v4_rounds
        )
        blockers: list[str] = []
        identities: set[str] = set()
        for item in v4_rounds:
            participant = item.participant
            engineering = item.engineering_acceptance
            if participant is None:
                blockers.append(f"{item.round_id}:MISSING_PARTICIPANT_EVIDENCE")
                continue
            identities.add(participant.participant_identity)
            if participant.participant_identity != study.expected_participant_identity:
                blockers.append(f"{item.round_id}:PARTICIPANT_IDENTITY_MISMATCH")
            if participant.disposition != "retained":
                blockers.append(f"{item.round_id}:PARTICIPANT_NOT_RETAINED")
            if participant.control_rating < study.p0.control_rating_threshold:
                blockers.append(f"{item.round_id}:RATING_BELOW_THRESHOLD")
            if not participant.citation_usable:
                blockers.append(f"{item.round_id}:CITATION_UNUSABLE")
            if participant.structural_rewrite:
                blockers.append(f"{item.round_id}:STRUCTURAL_REWRITE")
            if engineering is None:
                blockers.append(f"{item.round_id}:MISSING_ENGINEERING_ACCEPTANCE")
            elif engineering.outcome != "PASS" or engineering.blocking_defect:
                blockers.append(f"{item.round_id}:ENGINEERING_NOT_PASS")
            if item.binding_status != "verified":
                blockers.append(
                    f"{item.round_id}:BINDING_{item.binding_status.upper()}"
                )
        if len(identities) != study.p0.participant_count:
            blockers.append("PARTICIPANT_COUNT_MISMATCH")
        gate = (
            "criteria_met_awaiting_user_rc_acceptance"
            if not blockers
            else "criteria_not_met"
        )
        return _make(
            Phase3ESummaryV4,
            SUMMARY_HASH_DOMAIN_V4,
            "summary_hash",
            {
                "schema_version": SUMMARY_SCHEMA_V4,
                "hash_domain": SUMMARY_HASH_DOMAIN_V4,
                "study_id": study.study_id,
                "study_hash": study.study_hash,
                "gate_model": GATE_MODEL_V4,
                "round_projections": projections,
                "gate_state": gate,
                "blockers": tuple(blockers),
            },
        )  # type: ignore[return-value]
    projections = tuple(
        RoundProjection(
            round_id=item.round_id,
            round_hash=item.round_hash,
            reviewer_disposition=None
            if item.reviewer is None
            else item.reviewer.disposition,
            citation_usable=item.citation_usable,
            structural_rewrite=item.structural_rewrite,
            binding_status=item.binding_status,
            blocking_defect=item.blocking_defect,
        )
        for item in rounds
    )
    blockers: list[str] = []
    if any(item.reviewer is None or item.participant is None for item in rounds):
        gate = "engineering_only"
        blockers.append("MISSING_HUMAN_EVIDENCE")
    else:
        identities = {
            item.participant.participant_identity
            for item in rounds
            if item.participant is not None
        }
        if len(identities) != study.p0.participant_count:
            blockers.append("PARTICIPANT_COUNT_MISMATCH")
        for item in rounds:
            if item.binding_status != "verified":
                blockers.append(
                    f"{item.round_id}:BINDING_{item.binding_status.upper()}"
                )
            if item.blocking_defect:
                blockers.append(f"{item.round_id}:BLOCKING_DEFECT")
            if item.reviewer is None or item.reviewer.disposition != "PASS":
                blockers.append(f"{item.round_id}:REVIEWER_NOT_PASS")
            if (
                item.reviewer is not None
                and item.participant is not None
                and item.reviewer.reviewer_identity
                == item.participant.participant_identity
            ):
                blockers.append(f"{item.round_id}:REVIEWER_NOT_INDEPENDENT")
            if isinstance(study, Phase3EStudyV2):
                if (
                    item.participant is not None
                    and item.participant.participant_identity
                    != study.round2_source.expected_participant_identity
                ):
                    blockers.append(f"{item.round_id}:PARTICIPANT_IDENTITY_MISMATCH")
                if (
                    item.reviewer is not None
                    and item.reviewer.reviewer_identity
                    != study.round2_source.expected_reviewer_identity
                ):
                    blockers.append(f"{item.round_id}:REVIEWER_IDENTITY_MISMATCH")
            if item.citation_usable is not True:
                blockers.append(f"{item.round_id}:CITATION_UNUSABLE")
            if item.structural_rewrite is not False:
                blockers.append(f"{item.round_id}:STRUCTURAL_REWRITE")
            if (
                item.reviewer is not None
                and item.reviewer.control_rating < study.p0.control_rating_threshold
            ):
                blockers.append(f"{item.round_id}:RATING_BELOW_THRESHOLD")
        gate = (
            "criteria_met_awaiting_user_rc_acceptance"
            if not blockers
            else "criteria_not_met"
        )
    summary_model: type[Summary] = (
        Phase3ESummaryV3
        if isinstance(study, Phase3EStudyV3)
        else Phase3ESummaryV2
        if isinstance(study, Phase3EStudyV2)
        else Phase3ESummary
    )
    summary_domain = (
        SUMMARY_HASH_DOMAIN_V3
        if isinstance(study, Phase3EStudyV3)
        else SUMMARY_HASH_DOMAIN_V2
        if isinstance(study, Phase3EStudyV2)
        else SUMMARY_HASH_DOMAIN
    )
    return _make(
        summary_model,
        summary_domain,
        "summary_hash",
        {
            "schema_version": SUMMARY_SCHEMA_V3
            if isinstance(study, Phase3EStudyV3)
            else SUMMARY_SCHEMA_V2
            if isinstance(study, Phase3EStudyV2)
            else SUMMARY_SCHEMA,
            "hash_domain": summary_domain,
            "study_id": study.study_id,
            "study_hash": study.study_hash,
            "round_projections": projections,
            "gate_state": gate,
            "blockers": tuple(blockers),
        },
    )  # type: ignore[return-value]


def create_user_rc_decision(
    study: Study,
    summary: Summary,
    *,
    decision: Literal["ACCEPT", "RETAIN", "REVISE", "DISCARD", "STOP"],
    user_timestamp: str,
    evidence_path: Path,
    notes: str,
) -> Decision:
    if isinstance(study, Phase3EStudyV3):
        raise Phase3EError("PROTOCOL_HOLD")
    if not verify_record(study):
        raise Phase3EError("INVALID_STUDY_BINDING")
    serialize_record(summary)
    if (
        summary.study_id != study.study_id
        or summary.study_hash != study.study_hash
        or summary.gate_state != "criteria_met_awaiting_user_rc_acceptance"
    ):
        raise Phase3EError("PREMATURE_USER_RC_DECISION")
    try:
        evidence_path.relative_to(Path(study.work_root))
    except ValueError as error:
        raise Phase3EError("DECISION_EVIDENCE_OUTSIDE_WORK_ROOT") from error
    _, evidence_digest = _safe_file(evidence_path, "INVALID_DECISION_EVIDENCE_PATH")
    outcome = {
        "ACCEPT": "rc_accepted_pending_version_gate",
        "RETAIN": "retained_no_release_authority",
        "REVISE": "revise_new_candidate_required",
        "DISCARD": "discarded_no_release_authority",
        "STOP": "stopped_no_release_authority",
    }[decision]
    decision_model: type[Decision] = (
        UserRCDecisionV4
        if isinstance(study, Phase3EStudyV4)
        else UserRCDecisionV3
        if isinstance(study, Phase3EStudyV3)
        else UserRCDecisionV2
        if isinstance(study, Phase3EStudyV2)
        else UserRCDecision
    )
    decision_domain = (
        USER_DECISION_HASH_DOMAIN_V4
        if isinstance(study, Phase3EStudyV4)
        else USER_DECISION_HASH_DOMAIN_V3
        if isinstance(study, Phase3EStudyV3)
        else USER_DECISION_HASH_DOMAIN_V2
        if isinstance(study, Phase3EStudyV2)
        else USER_DECISION_HASH_DOMAIN
    )
    return _make(
        decision_model,
        decision_domain,
        "decision_hash",
        {
            "schema_version": USER_DECISION_SCHEMA_V4
            if isinstance(study, Phase3EStudyV4)
            else USER_DECISION_SCHEMA_V3
            if isinstance(study, Phase3EStudyV3)
            else USER_DECISION_SCHEMA_V2
            if isinstance(study, Phase3EStudyV2)
            else USER_DECISION_SCHEMA,
            "hash_domain": decision_domain,
            "study_id": study.study_id,
            "study_hash": study.study_hash,
            "summary_hash": summary.summary_hash,
            "decision": decision,
            "user_timestamp": user_timestamp,
            "evidence_path": str(evidence_path),
            "evidence_sha256": evidence_digest,
            "notes": notes,
            "outcome": outcome,
        },
    )  # type: ignore[return-value]


def verify_record(record: Record) -> bool:
    try:
        serialize_record(record)
        if isinstance(
            record, (Phase3EStudy, Phase3EStudyV2, Phase3EStudyV3, Phase3EStudyV4)
        ):
            _, manifest_hash = _safe_file(
                Path(record.lineage.manifest_path), "MANIFEST_UNAVAILABLE"
            )
            if manifest_hash != record.lineage.manifest_sha256:
                return False
            _safe_directory(Path(record.study_root), "INVALID_STUDY_ROOT")
            _safe_directory(Path(record.work_root), "INVALID_WORK_ROOT")
            if isinstance(record, (Phase3EStudyV2, Phase3EStudyV3, Phase3EStudyV4)):
                source = record.round2_source
                if (
                    Path(source.source_set_manifest_path).parent
                    != Path(record.work_root)
                    or not Path(source.source_set_manifest_path).is_file()
                ):
                    return False
                source_set = load_record(Path(source.source_set_manifest_path))
                if not isinstance(source_set, SourceSetManifestV2) or (
                    source_set.source_set_hash != source.source_set_hash
                    or source_set.source_set_root_hash != source.source_set_root_hash
                    or source_set.material_source_root != source.material_source_root
                    or source_set
                    != create_source_set_manifest(Path(source.material_source_root))
                ):
                    return False
            if isinstance(record, Phase3EStudyV3):
                _verify_round1_contract_files(
                    record.round1_contract,
                    study_root=Path(record.study_root),
                    work_root=Path(record.work_root),
                    repository=Path(__file__).resolve().parents[2],
                    round2_material_source_root=Path(
                        record.round2_source.material_source_root
                    ),
                )
            if isinstance(
                record, Phase3EStudyV4
            ) and not _verify_predecessor_contract_v4(record):
                return False
        if isinstance(
            record, (Phase3ERound, Phase3ERoundV2, Phase3ERoundV3, Phase3ERoundV4)
        ):
            for evidence in record.evidence:
                _, digest = _safe_file(Path(evidence.path), "INVALID_EVIDENCE_PATH")
                if digest != evidence.bytes_sha256:
                    return False
                if evidence.kind in {
                    "result",
                    "apply_plan",
                    "executable_proposal",
                    "user_authorization",
                    "restore_authorization",
                    "apply_receipt",
                    "restore_receipt",
                    "loopback_binding",
                }:
                    _canonical_record(evidence, evidence.kind)
            if isinstance(record, Phase3ERoundV3):
                for operation in (record.apply_operation, record.restore_operation):
                    if operation is not None:
                        _safe_directory(
                            Path(operation.ledger_root), "INVALID_LEDGER_ROOT"
                        )
                        _safe_file(
                            Path(operation.backup_manifest_path), "INVALID_EVENT_PATH"
                        )
                        _safe_file(
                            Path(operation.post_observation_path), "INVALID_EVENT_PATH"
                        )
            human_items = (
                (record.participant, record.reviewer)
                if not isinstance(record, Phase3ERoundV4)
                else (record.participant,)
            )
            for item in human_items:
                if item is not None:
                    _, digest = _safe_file(
                        Path(item.evidence_path), "INVALID_EVIDENCE_PATH"
                    )
                    if digest != item.evidence_sha256:
                        return False
            if (
                isinstance(record, Phase3ERoundV4)
                and record.engineering_acceptance is not None
            ):
                _, digest = _safe_file(
                    Path(record.engineering_acceptance.evidence_path),
                    "INVALID_ENGINEERING_EVIDENCE_PATH",
                )
                if digest != record.engineering_acceptance.evidence_sha256:
                    return False
            if (
                record.round_id == ROUND_IDS[1]
                and record.participant is not None
                and (
                    record.participant.evidence_path
                    != _record_evidence(record, "pdf_export").path
                )
            ):
                return False
        if isinstance(record, SourceSetManifestV2):
            return record == create_source_set_manifest(
                Path(record.material_source_root)
            )
        if isinstance(
            record,
            (UserRCDecision, UserRCDecisionV2, UserRCDecisionV3, UserRCDecisionV4),
        ):
            _, digest = _safe_file(
                Path(record.evidence_path), "INVALID_DECISION_EVIDENCE_PATH"
            )
            if digest != record.evidence_sha256:
                return False
        return True
    except Phase3EError:
        return False


def verify_round_for_study(study: Study, round_: Round) -> bool:
    """Verify record integrity and its Study binding without upgrading its state."""
    if (
        not verify_record(study)
        or not verify_record(round_)
        or _record_version(study) != _record_version(round_)
        or round_.study_id != study.study_id
        or round_.study_hash != study.study_hash
    ):
        return False
    return round_.binding_status != "verified" or _verify_round_binding(study, round_)


def verify_summary_for_study(
    study: Study,
    rounds: tuple[Round, Round],
    summary: Summary,
) -> bool:
    """Recompute the Summary from its bound records and compare canonical values."""
    try:
        return (
            _record_version(study) == _record_version(summary)
            and all(_record_version(study) == _record_version(item) for item in rounds)
            and verify_record(summary)
            and create_summary(study, rounds) == summary
        )
    except Phase3EError:
        return False


def verify_user_decision_for_study(
    study: Study,
    summary: Summary,
    decision: Decision,
) -> bool:
    """Verify the decision's Study, Summary, path-placement and bytes bindings."""
    try:
        Path(decision.evidence_path).relative_to(Path(study.work_root))
    except ValueError:
        return False
    return bool(
        verify_record(study)
        and verify_record(summary)
        and verify_record(decision)
        and _record_version(study)
        == _record_version(summary)
        == _record_version(decision)
        and summary.gate_state == "criteria_met_awaiting_user_rc_acceptance"
        and summary.study_id == study.study_id
        and summary.study_hash == study.study_hash
        and decision.study_id == study.study_id
        and decision.study_hash == study.study_hash
        and decision.summary_hash == summary.summary_hash
    )


def status_projection(
    study: Study | None,
    rounds: tuple[Round, ...] = (),
    summary: Summary | None = None,
    decision: Decision | None = None,
) -> dict[str, object]:
    blockers: list[str] = []
    if study is None:
        blockers.append("MISSING_STUDY")
    round_map = {item.round_id: item for item in rounds}
    if len(round_map) != len(rounds):
        blockers.append("DUPLICATE_ROUND_ID")
    for round_id in ROUND_IDS:
        if round_id not in round_map:
            blockers.append(f"MISSING_{round_id}")
        elif not verify_record(round_map[round_id]):
            blockers.append(f"INVALID_{round_id}")
        elif study is not None and not verify_round_for_study(
            study, round_map[round_id]
        ):
            blockers.append(f"INVALID_{round_id}_BINDING")
    gate = "engineering_only"
    if summary is None:
        blockers.append("MISSING_SUMMARY")
    elif study is None or any(round_id not in round_map for round_id in ROUND_IDS):
        blockers.append("SUMMARY_CROSS_BINDING_MISMATCH")
    elif not verify_summary_for_study(
        study,
        (round_map[ROUND_IDS[0]], round_map[ROUND_IDS[1]]),
        summary,
    ):
        blockers.append("INVALID_SUMMARY")
    else:
        gate = summary.gate_state
    if gate == "criteria_met_awaiting_user_rc_acceptance" and decision is None:
        blockers.append("WAITING_USER_RC_DECISION")
    if decision is not None and (
        summary is None
        or study is None
        or not verify_user_decision_for_study(study, summary, decision)
    ):
        blockers.append("INVALID_USER_RC_DECISION")
    next_action = (
        "create_study"
        if study is None
        else "record_missing_rounds"
        if any(item.startswith("MISSING_R") for item in blockers)
        else "create_summary"
        if summary is None
        else "obtain_explicit_user_rc_decision"
        if "WAITING_USER_RC_DECISION" in blockers
        else "hold_for_version_gate"
        if decision is not None
        and decision.outcome == "rc_accepted_pending_version_gate"
        else "resolve_blockers"
    )
    return {
        "present_records": {
            "study": study is not None,
            "rounds": tuple(sorted(round_map)),
            "summary": summary is not None,
            "user_rc_decision": decision is not None,
        },
        "gate_state": gate,
        "blockers": tuple(blockers),
        "next_action": next_action,
    }


def publish_record(root: Path, name: str, record: Record) -> None:
    root = _safe_directory(root, "INVALID_OUTPUT_ROOT")
    if Path(name).name != name or not name.endswith(".json"):
        raise Phase3EError("INVALID_OUTPUT_PATH")
    try:
        publish_new_direct_child(root, root / name, serialize_record(record))
    except (
        PublicationAttentionError,
        PublicationRollbackError,
        MaterialWorkflowError,
    ) as error:
        raise Phase3EError(getattr(error, "code", "OUTPUT_PUBLISH_FAILED")) from error


def load_record(path: Path) -> Record:
    data, _ = _safe_file(path, "RECORD_UNAVAILABLE")
    return parse_record_bytes(data)
