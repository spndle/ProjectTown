"""Additive, two-round Phase 2 closeout receipts.

This deliberately is not a usability Summary.  A receipt records a narrowly
approved longitudinal decision across two immutable studies without changing
the ten-task, single-study Summary protocol.
"""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .material_workflow import (
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    publish_new_direct_child,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file
from .usability_trials import (
    StudyV2,
    TrialV2,
    TrialV3,
    UsabilityTrialError,
    load_study,
    load_trial,
    pdf_presentation_pair_for_profile,
    verify_trial,
)

SCHEMA_VERSION = "v3-phase2-closeout-v1"
HASH_DOMAIN = "projecttown/v3/phase2-closeout/v1"
RECEIPT_NAME = "phase2-closeout.json"
MAX_RECORD_BYTES = 256 * 1024
_ROOT = Path(__file__).resolve().parents[2]

_ROUNDS = (
    {
        "task_id": "T001",
        "study_id": "projecttown-v3-phase2-human-pdf-v3-20260826-001",
        "profile": "projecttown-human-pdf-v3",
        "trial_hash": "44c5ce09771453a1c46aebbc339464cea261b646a7f5a644306de1338f568069",
        "trial_schema": "v3-usability-trial-v2",
        "manifest": "projecttown-trial-manifest-v3.json",
        "result": "T001-result.json",
        "pdf": "T001-plan-visual-v2.pdf",
        "elapsed_seconds": 120,
        "manual_baseline_seconds": 1200,
    },
    {
        "task_id": "T002",
        "study_id": "projecttown-v3-phase2-human-pdf-v9-20260829-001",
        "profile": "projecttown-human-pdf-v9",
        "trial_hash": "fb0e05b58150d1766a4de3a057d4aa4540988bf7b5c38257255c188138a61d42",
        "trial_schema": "v3-usability-trial-v3",
        "manifest": "projecttown-trial-manifest-v9.json",
        "result": "T002-result-generator-v8.json",
        "pdf": "T002-runbook-v9.pdf",
        "elapsed_seconds": 180,
        "manual_baseline_seconds": 1200,
    },
)


class Phase2CloseoutError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("phase 2 closeout rejected")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ParticipantEvidencePresence(_Model):
    notes: bool
    timestamp: bool
    evidence_path: bool


class PresentationReceipt(_Model):
    pdf_bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_file_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_export_version: str
    pdf_renderer_version: str
    pdf_source_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RoundReceipt(_Model):
    task_id: Literal["T001", "T002"]
    artifact_kind: Literal["plan"]
    study_id: str
    study_schema_version: Literal["v3-usability-study-v2"]
    study_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    study_file_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_schema_version: Literal["v3-usability-trial-v2", "v3-usability-trial-v3"]
    trial_record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_file_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_profile: str
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_file_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    citations_complete: Literal[True]
    presentation: PresentationReceipt
    disposition: Literal["retained"]
    structural_rewrite: Literal[False]
    citation_usable: Literal[True]
    call_observation: Literal["observed_zero"]
    action_count: Literal[1]
    control_rating: Literal[4]
    improvement_reason: Literal["none"]
    elapsed_seconds: Literal[120, 180]
    manual_baseline_seconds: Literal[1200]
    participant_evidence_presence: ParticipantEvidencePresence


class PolicyRevision(_Model):
    original_task_threshold: Literal[10]
    revised_round_threshold: Literal[2]
    reason: Literal["participant_burden"]
    scope: Literal["longitudinal_cross_profile"]
    acceptance: Literal["scope_limited_accepted_by_user"]
    user_decision_timestamp: None = None
    record_created_on: str

    @model_validator(mode="after")
    def created_on_is_date(self) -> PolicyRevision:
        try:
            parsed = date.fromisoformat(self.record_created_on)
        except ValueError as error:
            raise ValueError("record_created_on must be an explicit date") from error
        if parsed.isoformat() != self.record_created_on:
            raise ValueError("record_created_on must be an explicit date")
        return self


class Phase2Closeout(_Model):
    schema_version: Literal["v3-phase2-closeout-v1"]
    policy_revision: PolicyRevision
    rounds: tuple[RoundReceipt, RoundReceipt]
    legacy_limitation: Literal[
        "not_a_single_profile_summary; does_not_validate_v10_or_report_or_readme; does_not_authorize_apply"
    ]
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_order(self) -> Phase2Closeout:
        if tuple(round_.task_id for round_ in self.rounds) != ("T001", "T002"):
            raise ValueError("rounds must be T001 then T002")
        return self


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )


def _payload(receipt: Phase2Closeout) -> dict[str, object]:
    value = receipt.model_dump(mode="json")
    value.pop("receipt_hash")
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(
        HASH_DOMAIN.encode("ascii") + b"\x00" + _canonical_json(value)
    ).hexdigest()


def serialize_closeout(receipt: Phase2Closeout) -> bytes:
    try:
        validated = Phase2Closeout.model_validate_json(
            _canonical_json(receipt.model_dump(mode="json"))
        )
    except ValidationError as error:
        raise Phase2CloseoutError("INVALID_RECEIPT") from error
    if validated.receipt_hash != _hash(_payload(validated)):
        raise Phase2CloseoutError("INVALID_RECEIPT_HASH")
    return _canonical_json(validated.model_dump(mode="json"))


def parse_closeout_bytes(data: bytes) -> Phase2Closeout:
    if not isinstance(data, bytes) or len(data) > MAX_RECORD_BYTES:
        raise Phase2CloseoutError("INVALID_RECEIPT")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate key")
            parsed[key] = value
        return parsed

    try:
        json.loads(data.decode("utf-8", "strict"), object_pairs_hook=reject_duplicates)
        parsed = Phase2Closeout.model_validate_json(data)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Phase2CloseoutError("INVALID_RECEIPT") from error
    if data != _canonical_json(parsed.model_dump(mode="json")):
        raise Phase2CloseoutError("NONCANONICAL_RECEIPT")
    if parsed.receipt_hash != _hash(_payload(parsed)):
        raise Phase2CloseoutError("INVALID_RECEIPT_HASH")
    return parsed


def _safe_file_bytes(path: Path, code: str) -> bytes:
    try:
        metadata = path.lstat()
        canonical = path.resolve(strict=True)
        parent = path.parent.lstat()
    except OSError as error:
        raise Phase2CloseoutError(code) from error
    if (
        canonical != path
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or not is_safe_directory(parent)
        or is_reparse(parent)
    ):
        raise Phase2CloseoutError(code)
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise Phase2CloseoutError(code)
    return stable[2]


def _safe_file_hash(path: Path, code: str) -> str:
    return hashlib.sha256(_safe_file_bytes(path, code)).hexdigest()


def _safe_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise Phase2CloseoutError("INVALID_RECEIPT_ROOT")
    try:
        metadata = root.lstat()
    except OSError as error:
        raise Phase2CloseoutError("INVALID_RECEIPT_ROOT") from error
    if (
        not is_safe_directory(metadata)
        or is_reparse(metadata)
        or root.resolve(strict=True) != root
    ):
        raise Phase2CloseoutError("INVALID_RECEIPT_ROOT")
    return root


def _manifest_hash(name: str) -> str:
    return _safe_file_hash(
        _ROOT / "examples" / "v3-phase-2" / name, "MANIFEST_UNAVAILABLE"
    )


def _round_receipt(
    spec: dict[str, str], study_root: Path, work_root: Path
) -> RoundReceipt:
    try:
        study = load_study(study_root)
        trial = load_trial(study_root, spec["task_id"])
    except UsabilityTrialError as error:
        raise Phase2CloseoutError("EVIDENCE_UNAVAILABLE") from error
    if not isinstance(study, StudyV2) or not isinstance(trial, (TrialV2, TrialV3)):
        raise Phase2CloseoutError("EVIDENCE_MISMATCH")
    if not verify_trial(trial) or trial.result is None or trial.presentation is None:
        raise Phase2CloseoutError("EVIDENCE_MISMATCH")
    manifest_hash = _manifest_hash(spec["manifest"])
    expected_presentation = pdf_presentation_pair_for_profile(spec["profile"])
    if (
        study.study_id != spec["study_id"]
        or study.candidate_profile != spec["profile"]
        or study.candidate_manifest_hash != manifest_hash
        or trial.task_id != spec["task_id"]
        or trial.record_hash != spec["trial_hash"]
        or trial.schema_version != spec["trial_schema"]
        or trial.study_id != study.study_id
        or trial.study_hash != study.study_hash
        or trial.artifact_kind != "plan"
        or trial.state != "completed"
        or trial.disposition != "retained"
        or trial.structural_rewrite is not False
        or trial.citation_usable is not True
        or trial.call_observation != "observed_zero"
        or trial.actions != ("open_task",)
        or trial.control_rating != 4
        or trial.improvement_reason != "none"
        or trial.elapsed_seconds != int(spec["elapsed_seconds"])
        or trial.manual_baseline_seconds != int(spec["manual_baseline_seconds"])
        or trial.result.citations_complete is not True
        or expected_presentation is None
        or (
            trial.presentation.pdf_export_version,
            trial.presentation.pdf_renderer_version,
        )
        != expected_presentation
    ):
        raise Phase2CloseoutError("EVIDENCE_MISMATCH")
    result_hash = _safe_file_hash(work_root / spec["result"], "RESULT_UNAVAILABLE")
    pdf_hash = _safe_file_hash(work_root / spec["pdf"], "PDF_UNAVAILABLE")
    if (
        result_hash != trial.result.result_bytes_hash
        or pdf_hash != trial.presentation.pdf_bytes_hash
    ):
        raise Phase2CloseoutError("EVIDENCE_MISMATCH")
    presence = ParticipantEvidencePresence(
        notes=isinstance(trial, TrialV3) and bool(trial.participant_notes),
        timestamp=isinstance(trial, TrialV3) and bool(trial.participant_timestamp),
        evidence_path=isinstance(trial, TrialV3)
        and trial.participant_evidence_path is not None,
    )
    return RoundReceipt(
        task_id=spec["task_id"],
        artifact_kind="plan",
        study_id=study.study_id,
        study_schema_version=study.schema_version,
        study_hash=study.study_hash,
        study_file_hash=_safe_file_hash(
            study_root / "study.json", "EVIDENCE_UNAVAILABLE"
        ),
        trial_schema_version=trial.schema_version,
        trial_record_hash=trial.record_hash,
        trial_file_hash=_safe_file_hash(
            study_root / f"{trial.task_id}.json", "EVIDENCE_UNAVAILABLE"
        ),
        candidate_profile=study.candidate_profile,
        manifest_hash=manifest_hash,
        result_bytes_hash=trial.result.result_bytes_hash,
        result_file_hash=result_hash,
        result_session_hash=trial.result.result_session_hash,
        artifact_hash=trial.result.artifact_hash,
        preview_hash=trial.result.preview_hash,
        citations_complete=True,
        presentation=PresentationReceipt(
            pdf_bytes_hash=trial.presentation.pdf_bytes_hash,
            pdf_file_hash=pdf_hash,
            pdf_export_version=trial.presentation.pdf_export_version,
            pdf_renderer_version=trial.presentation.pdf_renderer_version,
            pdf_source_artifact_hash=trial.presentation.pdf_source_artifact_hash,
        ),
        disposition="retained",
        structural_rewrite=False,
        citation_usable=True,
        call_observation="observed_zero",
        action_count=1,
        control_rating=4,
        improvement_reason="none",
        elapsed_seconds=trial.elapsed_seconds,
        manual_baseline_seconds=trial.manual_baseline_seconds,
        participant_evidence_presence=presence,
    )


def _build_closeout(
    t001_study_root: Path,
    t001_work_root: Path,
    t002_study_root: Path,
    t002_work_root: Path,
    *,
    record_created_on: str,
) -> Phase2Closeout:
    rounds = (
        _round_receipt(_ROUNDS[0], t001_study_root, t001_work_root),
        _round_receipt(_ROUNDS[1], t002_study_root, t002_work_root),
    )
    policy = PolicyRevision(
        original_task_threshold=10,
        revised_round_threshold=2,
        reason="participant_burden",
        scope="longitudinal_cross_profile",
        acceptance="scope_limited_accepted_by_user",
        user_decision_timestamp=None,
        record_created_on=record_created_on,
    )
    raw = {
        "schema_version": SCHEMA_VERSION,
        "policy_revision": policy.model_dump(mode="json"),
        "rounds": tuple(item.model_dump(mode="json") for item in rounds),
        "legacy_limitation": "not_a_single_profile_summary; does_not_validate_v10_or_report_or_readme; does_not_authorize_apply",
    }
    try:
        candidate = Phase2Closeout.model_validate({**raw, "receipt_hash": "0" * 64})
    except ValidationError as error:
        raise Phase2CloseoutError("INVALID_RECEIPT") from error
    return candidate.model_copy(update={"receipt_hash": _hash(_payload(candidate))})


def create_closeout(
    receipt_root: Path,
    t001_study_root: Path,
    t001_work_root: Path,
    t002_study_root: Path,
    t002_work_root: Path,
    *,
    record_created_on: str,
) -> Phase2Closeout:
    _safe_root(receipt_root)
    return _build_closeout(
        t001_study_root,
        t001_work_root,
        t002_study_root,
        t002_work_root,
        record_created_on=record_created_on,
    )


def publish_closeout(root: Path, receipt: Phase2Closeout) -> None:
    try:
        publish_new_direct_child(
            _safe_root(root),
            _safe_root(root) / RECEIPT_NAME,
            serialize_closeout(receipt),
        )
    except PublicationAttentionError as error:
        raise Phase2CloseoutError(error.code) from error
    except PublicationRollbackError as error:
        raise Phase2CloseoutError(error.code) from error
    except MaterialWorkflowError as error:
        raise Phase2CloseoutError(
            getattr(error, "code", "OUTPUT_PUBLISH_FAILED")
        ) from error


def load_closeout(root: Path) -> Phase2Closeout:
    root = _safe_root(root)
    path = root / RECEIPT_NAME
    try:
        return parse_closeout_bytes(_safe_file_bytes(path, "RECEIPT_UNAVAILABLE"))
    except OSError as error:
        raise Phase2CloseoutError("RECEIPT_UNAVAILABLE") from error


def verify_closeout(
    receipt: Phase2Closeout,
    t001_study_root: Path,
    t001_work_root: Path,
    t002_study_root: Path,
    t002_work_root: Path,
) -> bool:
    try:
        serialize_closeout(receipt)
        current = _build_closeout(
            t001_study_root,
            t001_work_root,
            t002_study_root,
            t002_work_root,
            record_created_on=receipt.policy_revision.record_created_on,
        )
        return current == receipt
    except Phase2CloseoutError:
        return False
