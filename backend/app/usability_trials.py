"""Offline, immutable Phase 2 usability-study records.

This module intentionally records only structured observations.  It is not a
user-profile, analytics, authentication, or free-text feedback facility.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

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
    ResultSession,
    load_session,
    publish_new_direct_child,
    render_export,
    render_pdf_export,
    revalidate_result_sources,
    serialize_session,
    verify_result_integrity,
)
from .pdf_export import PDF_EXPORT_VERSION
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

STUDY_SCHEMA_VERSION = "v3-usability-study-v1"
TRIAL_SCHEMA_VERSION = "v3-usability-trial-v1"
SUMMARY_SCHEMA_VERSION = "v3-usability-summary-v1"
STUDY_SCHEMA_VERSION_V2 = "v3-usability-study-v2"
TRIAL_SCHEMA_VERSION_V2 = "v3-usability-trial-v2"
SUMMARY_SCHEMA_VERSION_V2 = "v3-usability-summary-v2"
TRIAL_SCHEMA_VERSION_V3 = "v3-usability-trial-v3"
SUMMARY_SCHEMA_VERSION_V3 = "v3-usability-summary-v3"
MAX_RECORD_BYTES = 256 * 1024
MAX_NESTING = 16
TASK_IDS = tuple(f"T{index:03d}" for index in range(1, 11))
ArtifactKind = Literal["plan", "report", "readme"]
CandidateProfile = Literal[
    "projecttown-human-pdf-v2",
    "projecttown-human-pdf-v3",
    "projecttown-human-pdf-v4",
    "projecttown-human-pdf-v5",
    "projecttown-human-pdf-v6",
    "projecttown-human-pdf-v7",
    "projecttown-human-pdf-v8",
    "projecttown-human-pdf-v9",
    "projecttown-human-pdf-v10",
]
EvaluationKind = Literal["human_usability", "synthetic_engineering_fixture"]
TrialState = Literal["completed", "workflow_failed", "abandoned"]
Disposition = Literal["exported", "retained", "not_kept"]
FailureStage = Literal[
    "material_selection", "draft", "confirmation", "generation", "preview", "export"
]
FailureCode = Literal[
    "invalid_input",
    "source_changed",
    "unresolved_conflict",
    "publication_rolled_back",
    "publication_needs_attention",
    "unexpected_error",
    "user_stopped",
]
ImprovementReason = Literal[
    "none", "clarity", "citation", "workflow", "artifact_quality", "other_structured"
]
Action = Literal[
    "open_task",
    "select_materials",
    "confirm_and_generate",
    "preview",
    "export_or_retain",
    "resolve_conflict",
    "regenerate",
    "manual_rewrite",
    "stop",
]

_PDF_PRESENTATION_PAIRS = {
    "v3-material-pdf-export-v1": "projecttown-reportlab-pdf-v1",
    "v3-material-pdf-export-v2": "projecttown-reportlab-pdf-v2",
    "v3-material-pdf-export-v3": "projecttown-reportlab-pdf-v3",
    "v3-material-pdf-export-v4": "projecttown-reportlab-pdf-v4",
    "v3-material-pdf-export-v5": "projecttown-reportlab-pdf-v5",
    "v3-material-pdf-export-v6": "projecttown-reportlab-pdf-v6",
    "v3-material-pdf-export-v7": "projecttown-reportlab-pdf-v7",
    "v3-material-pdf-export-v8": "projecttown-reportlab-pdf-v8",
    "v3-material-pdf-export-v9": "projecttown-reportlab-pdf-v9",
}
_PDF_PROFILE_PRESENTATIONS = {
    "projecttown-human-pdf-v2": (
        "v3-material-pdf-export-v1",
        "projecttown-reportlab-pdf-v1",
    ),
    "projecttown-human-pdf-v3": (
        "v3-material-pdf-export-v2",
        "projecttown-reportlab-pdf-v2",
    ),
    "projecttown-human-pdf-v4": (
        "v3-material-pdf-export-v3",
        "projecttown-reportlab-pdf-v3",
    ),
    "projecttown-human-pdf-v5": (
        "v3-material-pdf-export-v4",
        "projecttown-reportlab-pdf-v4",
    ),
    "projecttown-human-pdf-v6": (
        "v3-material-pdf-export-v5",
        "projecttown-reportlab-pdf-v5",
    ),
    "projecttown-human-pdf-v7": (
        "v3-material-pdf-export-v6",
        "projecttown-reportlab-pdf-v6",
    ),
    "projecttown-human-pdf-v8": (
        "v3-material-pdf-export-v7",
        "projecttown-reportlab-pdf-v7",
    ),
    "projecttown-human-pdf-v9": (
        "v3-material-pdf-export-v8",
        "projecttown-reportlab-pdf-v8",
    ),
    "projecttown-human-pdf-v10": (
        "v3-material-pdf-export-v9",
        "projecttown-reportlab-pdf-v9",
    ),
}


def pdf_presentation_pair_for_profile(
    candidate_profile: str,
) -> tuple[str, str] | None:
    """Return the only PDF presentation pair accepted by a human PDF profile."""
    return _PDF_PROFILE_PRESENTATIONS.get(candidate_profile)


class UsabilityTrialError(ValueError):
    """A stable rejection that never embeds caller supplied content or paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("usability trial rejected")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class StudyTask(_Model):
    task_id: Literal[
        "T001", "T002", "T003", "T004", "T005", "T006", "T007", "T008", "T009", "T010"
    ]
    artifact_kind: ArtifactKind


class Study(_Model):
    schema_version: Literal["v3-usability-study-v1"]
    study_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    evaluation_kind: EvaluationKind
    candidate_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tasks: tuple[StudyTask, ...] = Field(min_length=10, max_length=10)
    study_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def preregistered_tasks(self) -> Study:
        if tuple(item.task_id for item in self.tasks) != TASK_IDS:
            raise ValueError("task ids must be T001 through T010")
        if min(Counter(item.artifact_kind for item in self.tasks).values()) < 2:
            raise ValueError("each artifact kind needs two tasks")
        if len({item.artifact_kind for item in self.tasks}) != 3:
            raise ValueError("all artifact kinds required")
        return self


class StudyV2(Study):
    """A separately hashed PDF-candidate study; v1 records stay immutable."""

    schema_version: Literal["v3-usability-study-v2"]
    candidate_profile: CandidateProfile


class ResultBinding(_Model):
    result_session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_state: Literal["generated", "needs_user_decision"]
    citations_complete: bool
    provider_calls: int = Field(ge=0)
    embedding_calls: int = Field(ge=0)
    mcp_calls: int = Field(ge=0)
    call_observation: Literal["observed_zero", "observed_nonzero"]

    @model_validator(mode="after")
    def call_counts_match(self) -> ResultBinding:
        expected = (
            "observed_zero"
            if self.provider_calls == self.embedding_calls == self.mcp_calls == 0
            else "observed_nonzero"
        )
        if self.call_observation != expected:
            raise ValueError("call observation must match counts")
        return self


class PresentationBinding(_Model):
    """Path-free binding to the exact PDF actually shown to the participant."""

    presentation_format: Literal["pdf"]
    pdf_bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_export_version: Literal[
        "v3-material-pdf-export-v1",
        "v3-material-pdf-export-v2",
        "v3-material-pdf-export-v3",
        "v3-material-pdf-export-v4",
        "v3-material-pdf-export-v5",
        "v3-material-pdf-export-v6",
        "v3-material-pdf-export-v7",
        "v3-material-pdf-export-v8",
        "v3-material-pdf-export-v9",
    ]
    pdf_renderer_version: Literal[
        "projecttown-reportlab-pdf-v1",
        "projecttown-reportlab-pdf-v2",
        "projecttown-reportlab-pdf-v3",
        "projecttown-reportlab-pdf-v4",
        "projecttown-reportlab-pdf-v5",
        "projecttown-reportlab-pdf-v6",
        "projecttown-reportlab-pdf-v7",
        "projecttown-reportlab-pdf-v8",
        "projecttown-reportlab-pdf-v9",
    ]
    pdf_source_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def supported_version_pair(self) -> PresentationBinding:
        if (
            _PDF_PRESENTATION_PAIRS.get(self.pdf_export_version)
            != self.pdf_renderer_version
        ):
            raise ValueError("unsupported PDF presentation pair")
        return self


class Trial(_Model):
    schema_version: Literal["v3-usability-trial-v1"]
    study_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    study_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_kind: EvaluationKind
    task_id: Literal[
        "T001", "T002", "T003", "T004", "T005", "T006", "T007", "T008", "T009", "T010"
    ]
    artifact_kind: ArtifactKind
    state: TrialState
    actions: tuple[Action, ...] = Field(min_length=1, max_length=64)
    elapsed_seconds: int = Field(ge=1, le=86_400)
    manual_baseline_seconds: int = Field(ge=1, le=86_400)
    control_rating: int = Field(ge=1, le=5)
    structural_rewrite: bool | None = None
    citation_usable: bool | None = None
    disposition: Disposition
    failure_stage: FailureStage | None = None
    failure_code: FailureCode | None = None
    improvement_reason: ImprovementReason
    measurement_provenance: Literal[
        "human_reported_current_invocation", "synthetic_fixture"
    ]
    call_observation: Literal["observed_zero", "not_observed"]
    result: ResultBinding | None = None
    retained_result_bytes_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def state_contract(self) -> Trial:
        has_result = self.result is not None
        expected_observation = (
            "not_observed" if not has_result else self.result.call_observation
        )
        if self.call_observation != expected_observation:
            raise ValueError("call observation must be derived from result binding")
        expected_provenance = (
            "human_reported_current_invocation"
            if self.evaluation_kind == "human_usability"
            else "synthetic_fixture"
        )
        if self.measurement_provenance != expected_provenance:
            raise ValueError("measurement provenance must match evaluation kind")
        if self.state == "completed":
            if (
                not has_result
                or self.result.result_state != "generated"
                or self.failure_stage is not None
                or self.failure_code is not None
            ):
                raise ValueError("completed requires a result and no failure")
            if self.structural_rewrite is None or self.citation_usable is None:
                raise ValueError("completed needs human observations")
            if self.disposition not in {
                "exported",
                "retained",
                "not_kept",
            }:
                raise ValueError("invalid completed disposition")
        else:
            if self.failure_stage is None or self.failure_code is None:
                raise ValueError("non-completed state needs failure")
            if self.structural_rewrite is not None or self.citation_usable is not None:
                raise ValueError("failed trial cannot claim outcome")
            if self.disposition != "not_kept":
                raise ValueError("failed trial cannot export or retain")
        if self.disposition == "retained":
            if (
                not has_result
                or self.retained_result_bytes_hash != self.result.result_bytes_hash
            ):
                raise ValueError("retention must bind exact result bytes")
        elif self.retained_result_bytes_hash is not None:
            raise ValueError("unexpected retained bytes")
        if (self.state == "completed" and _adoptable(self)) != (
            self.improvement_reason == "none"
        ):
            raise ValueError("improvement reason must match derived adoptability")
        return self


class TrialV2(Trial):
    schema_version: Literal["v3-usability-trial-v2"]
    presentation: PresentationBinding | None = None

    @model_validator(mode="after")
    def presentation_contract(self) -> TrialV2:
        if self.state == "completed":
            if self.presentation is None or self.result is None:
                raise ValueError("completed v2 trials require the actual PDF")
            if self.presentation.pdf_source_artifact_hash != self.result.artifact_hash:
                raise ValueError("PDF must bind the frozen artifact")
        elif self.presentation is not None:
            raise ValueError("failed v2 trials cannot claim a PDF presentation")
        return self


_PARTICIPANT_TIMESTAMP = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(?P<zone>Z|[+-]\d{2}:\d{2})$"
)
_BIDI_FORBIDDEN = {
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
}


def _normalise_participant_notes(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("participant notes must be text")
    value = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    if not value.strip() or len(value) > 2000:
        raise ValueError("invalid participant notes")
    for character in value:
        category = unicodedata.category(character)
        if character == "\x00" or character in _BIDI_FORBIDDEN or character == "\ud800":
            raise ValueError("invalid participant notes")
        if category == "Cc" and character not in {"\n", "\t"}:
            raise ValueError("invalid participant notes")
        if category == "Cs":
            raise ValueError("invalid participant notes")
    return value


def _normalise_participant_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("participant timestamp must be text")
    match = _PARTICIPANT_TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError("invalid participant timestamp")
    zone = match.group("zone")
    if zone == "-00:00":
        raise ValueError("invalid participant timestamp")
    if zone != "Z":
        hours, minutes = int(zone[1:3]), int(zone[4:6])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
            raise ValueError("invalid participant timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid participant timestamp") from error
    return value[:-6] + "Z" if zone == "+00:00" else value


class TrialV3(TrialV2):
    """V5 human-PDF trial with participant-confirmed viewing evidence."""

    schema_version: Literal["v3-usability-trial-v3"]
    participant_notes: str
    participant_timestamp: str
    participant_evidence_path: str | None = None

    @field_validator("participant_notes")
    @classmethod
    def canonical_notes(cls, value: str) -> str:
        return _normalise_participant_notes(value)

    @field_validator("participant_timestamp")
    @classmethod
    def canonical_timestamp(cls, value: str) -> str:
        return _normalise_participant_timestamp(value)

    @model_validator(mode="after")
    def participant_evidence_contract(self) -> TrialV3:
        if self.state == "completed":
            if self.participant_evidence_path is None:
                raise ValueError("completed v3 trial requires participant evidence")
        elif (
            self.participant_evidence_path is not None or self.presentation is not None
        ):
            raise ValueError("failed v3 trial cannot claim participant PDF")
        return self


class SummaryMetrics(_Model):
    total_tasks: Literal[10]
    completed: int = Field(ge=0, le=10)
    adoptable: int = Field(ge=0, le=10)
    elapsed_seconds: int = Field(ge=0)
    manual_baseline_seconds: int = Field(ge=0)
    control_rating_total: int = Field(ge=0)
    citation_usable_true: int = Field(ge=0, le=10)
    calls_observed_zero: int = Field(ge=0, le=10)
    citations_complete: int = Field(ge=0, le=10)
    max_action_count: int = Field(ge=0)
    within_five_actions: int = Field(ge=0, le=10)
    time_saved_seconds: int
    improvement_reasons: tuple[tuple[ImprovementReason, int], ...]
    blockers: tuple[tuple[str, int], ...]


class TrialProjection(_Model):
    task_id: str
    artifact_kind: ArtifactKind
    state: TrialState
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: ResultBinding | None
    action_count: int = Field(ge=1)
    elapsed_seconds: int = Field(ge=1)
    manual_baseline_seconds: int = Field(ge=1)
    control_rating: int = Field(ge=1, le=5)
    structural_rewrite: bool | None
    citation_usable: bool | None
    disposition: Disposition
    improvement_reason: ImprovementReason
    call_observation: Literal["observed_zero", "observed_nonzero", "not_observed"]
    citations_complete: bool
    adoptable: bool
    failure_stage: FailureStage | None
    failure_code: FailureCode | None
    within_five_actions: bool
    time_saved_seconds: int


class TrialProjectionV2(TrialProjection):
    presentation: PresentationBinding | None = None


class TrialProjectionV3(TrialProjectionV2):
    participant_notes_present: bool
    participant_timestamp_present: bool
    participant_evidence_path_present: bool


class Summary(_Model):
    schema_version: Literal["v3-usability-summary-v1"]
    study: Study
    projections: tuple[TrialProjection, ...] = Field(min_length=10, max_length=10)
    metrics: SummaryMetrics
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_unanchored_awaiting_user_acceptance",
    ]
    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def task_order(self) -> Summary:
        if tuple(item.task_id for item in self.projections) != TASK_IDS:
            raise ValueError("summary must contain every task once")
        return self


class SummaryV2(_Model):
    schema_version: Literal["v3-usability-summary-v2"]
    study: StudyV2
    projections: tuple[TrialProjectionV2, ...] = Field(min_length=10, max_length=10)
    metrics: SummaryMetrics
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_unanchored_awaiting_user_acceptance",
    ]
    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def task_order(self) -> SummaryV2:
        if tuple(item.task_id for item in self.projections) != TASK_IDS:
            raise ValueError("summary must contain every task once")
        return self


class SummaryV3(_Model):
    schema_version: Literal["v3-usability-summary-v3"]
    study: StudyV2
    projections: tuple[TrialProjectionV3, ...] = Field(min_length=10, max_length=10)
    metrics: SummaryMetrics
    gate_state: Literal[
        "engineering_only",
        "criteria_not_met",
        "criteria_met_unanchored_awaiting_user_acceptance",
    ]
    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def task_order(self) -> SummaryV3:
        if tuple(item.task_id for item in self.projections) != TASK_IDS:
            raise ValueError("summary must contain every task once")
        return self


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\x00" + _canonical_json(value)
    ).hexdigest()


def _payload(model: _Model, hash_field: str) -> dict[str, object]:
    data = model.model_dump(mode="json")
    data.pop(hash_field)
    return data


def _strict_json(data: bytes) -> object:
    if not isinstance(data, bytes) or len(data) > MAX_RECORD_BYTES:
        raise UsabilityTrialError("INVALID_RECORD")

    def reject_constant(_value: str) -> object:
        raise ValueError("nonfinite")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        outcome: dict[str, object] = {}
        for key, value in pairs:
            if key in outcome:
                raise ValueError("duplicate")
            outcome[key] = value
        return outcome

    try:
        decoded = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise UsabilityTrialError("INVALID_RECORD") from error

    def depth(value: object, level: int = 0) -> None:
        if level > MAX_NESTING:
            raise UsabilityTrialError("INVALID_RECORD")
        if isinstance(value, dict):
            for child in value.values():
                depth(child, level + 1)
        elif isinstance(value, list):
            for child in value:
                depth(child, level + 1)

    depth(decoded)
    return decoded


def _parse(data: bytes, model: type[_Model], hash_field: str, domain: str) -> _Model:
    _strict_json(data)
    try:
        parsed = model.model_validate_json(data)
    except ValidationError as error:
        raise UsabilityTrialError("INVALID_RECORD") from error
    if data != _canonical_json(parsed.model_dump(mode="json")):
        raise UsabilityTrialError("NONCANONICAL_RECORD")
    if getattr(parsed, hash_field) != _hash(domain, _payload(parsed, hash_field)):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    return parsed


def serialize_study(study: Study | StudyV2) -> bytes:
    if not isinstance(study, (Study, StudyV2)):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    try:
        study = (StudyV2 if isinstance(study, StudyV2) else Study).model_validate_json(
            _canonical_json(study.model_dump(mode="json"))
        )
    except ValidationError as error:
        raise UsabilityTrialError("INVALID_RECORD") from error
    domain = (
        "projecttown/v3/usability-study/v2"
        if isinstance(study, StudyV2)
        else "projecttown/v3/usability-study/v1"
    )
    if study.study_hash != _hash(domain, _payload(study, "study_hash")):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    return _canonical_json(study.model_dump(mode="json"))


def serialize_trial(trial: Trial | TrialV2 | TrialV3) -> bytes:
    if not isinstance(trial, (Trial, TrialV2, TrialV3)):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    try:
        model = (
            TrialV3
            if isinstance(trial, TrialV3)
            else TrialV2
            if isinstance(trial, TrialV2)
            else Trial
        )
        validated = model.model_validate_json(
            _canonical_json(trial.model_dump(mode="json"))
        )
    except ValidationError as error:
        raise UsabilityTrialError("INVALID_RECORD") from error
    domain = (
        "projecttown/v3/usability-trial/v3"
        if isinstance(validated, TrialV3)
        else "projecttown/v3/usability-trial/v2"
        if isinstance(validated, TrialV2)
        else "projecttown/v3/usability-trial/v1"
    )
    if validated.record_hash != _hash(domain, _payload(validated, "record_hash")):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    return _canonical_json(validated.model_dump(mode="json"))


def serialize_summary(summary: Summary | SummaryV2 | SummaryV3) -> bytes:
    if not isinstance(summary, (Summary, SummaryV2, SummaryV3)):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    try:
        model = (
            SummaryV3
            if isinstance(summary, SummaryV3)
            else SummaryV2
            if isinstance(summary, SummaryV2)
            else Summary
        )
        summary = model.model_validate_json(
            _canonical_json(summary.model_dump(mode="json"))
        )
    except ValidationError as error:
        raise UsabilityTrialError("INVALID_RECORD") from error
    domain = (
        "projecttown/v3/usability-summary/v3"
        if isinstance(summary, SummaryV3)
        else "projecttown/v3/usability-summary/v2"
        if isinstance(summary, SummaryV2)
        else "projecttown/v3/usability-summary/v1"
    )
    if summary.summary_hash != _hash(domain, _payload(summary, "summary_hash")):
        raise UsabilityTrialError("INVALID_RECORD_HASH")
    _verify_summary_semantics(summary)
    return _canonical_json(summary.model_dump(mode="json"))


def _schema_version(data: bytes) -> str:
    parsed = _strict_json(data)
    if not isinstance(parsed, dict) or not isinstance(
        parsed.get("schema_version"), str
    ):
        raise UsabilityTrialError("INVALID_RECORD")
    return parsed["schema_version"]


def parse_study_bytes(data: bytes) -> Study | StudyV2:
    version = _schema_version(data)
    if version == STUDY_SCHEMA_VERSION:
        return _parse(data, Study, "study_hash", "projecttown/v3/usability-study/v1")  # type: ignore[return-value]
    if version == STUDY_SCHEMA_VERSION_V2:
        return _parse(data, StudyV2, "study_hash", "projecttown/v3/usability-study/v2")  # type: ignore[return-value]
    raise UsabilityTrialError("UNSUPPORTED_SCHEMA_VERSION")


def parse_trial_bytes(data: bytes) -> Trial | TrialV2 | TrialV3:
    version = _schema_version(data)
    if version == TRIAL_SCHEMA_VERSION:
        return _parse(data, Trial, "record_hash", "projecttown/v3/usability-trial/v1")  # type: ignore[return-value]
    if version == TRIAL_SCHEMA_VERSION_V2:
        return _parse(data, TrialV2, "record_hash", "projecttown/v3/usability-trial/v2")  # type: ignore[return-value]
    if version == TRIAL_SCHEMA_VERSION_V3:
        return _parse(data, TrialV3, "record_hash", "projecttown/v3/usability-trial/v3")  # type: ignore[return-value]
    raise UsabilityTrialError("UNSUPPORTED_SCHEMA_VERSION")


def parse_summary_bytes(data: bytes) -> Summary | SummaryV2 | SummaryV3:
    version = _schema_version(data)
    if version == SUMMARY_SCHEMA_VERSION:
        summary = _parse(
            data, Summary, "summary_hash", "projecttown/v3/usability-summary/v1"
        )
    elif version == SUMMARY_SCHEMA_VERSION_V2:
        summary = _parse(
            data, SummaryV2, "summary_hash", "projecttown/v3/usability-summary/v2"
        )
    elif version == SUMMARY_SCHEMA_VERSION_V3:
        summary = _parse(
            data, SummaryV3, "summary_hash", "projecttown/v3/usability-summary/v3"
        )
    else:
        raise UsabilityTrialError("UNSUPPORTED_SCHEMA_VERSION")
    _verify_summary_semantics(summary)  # type: ignore[arg-type]
    return summary  # type: ignore[return-value]


def _verify_summary_semantics(summary: Summary | SummaryV2 | SummaryV3) -> None:
    serialize_study(summary.study)
    v5 = isinstance(summary.study, StudyV2) and summary.study.candidate_profile in {
        "projecttown-human-pdf-v5",
        "projecttown-human-pdf-v6",
        "projecttown-human-pdf-v7",
        "projecttown-human-pdf-v8",
        "projecttown-human-pdf-v9",
        "projecttown-human-pdf-v10",
    }
    if v5 != isinstance(summary, SummaryV3):
        raise UsabilityTrialError("STUDY_MISMATCH")
    expected_presentation = (
        _PDF_PROFILE_PRESENTATIONS.get(summary.study.candidate_profile)
        if isinstance(summary, (SummaryV2, SummaryV3))
        else None
    )
    if isinstance(summary, (SummaryV2, SummaryV3)) and expected_presentation is None:
        raise UsabilityTrialError("INVALID_SUMMARY")
    items = summary.projections
    if tuple(item.artifact_kind for item in items) != tuple(
        task.artifact_kind for task in summary.study.tasks
    ):
        raise UsabilityTrialError("INVALID_SUMMARY")
    for item in items:
        if isinstance(summary, (SummaryV2, SummaryV3)):
            if not isinstance(item, TrialProjectionV2):
                raise UsabilityTrialError("INVALID_SUMMARY")
            if item.state == "completed":
                if item.presentation is None or item.result is None:
                    raise UsabilityTrialError("INVALID_SUMMARY")
                if (
                    item.presentation.pdf_source_artifact_hash
                    != item.result.artifact_hash
                ):
                    raise UsabilityTrialError("INVALID_SUMMARY")
                if (
                    item.presentation.pdf_export_version,
                    item.presentation.pdf_renderer_version,
                ) != expected_presentation:
                    raise UsabilityTrialError("INVALID_SUMMARY")
            elif item.presentation is not None:
                raise UsabilityTrialError("INVALID_SUMMARY")
        if isinstance(summary, SummaryV3):
            if not isinstance(item, TrialProjectionV3) or not (
                item.participant_notes_present and item.participant_timestamp_present
            ):
                raise UsabilityTrialError("INVALID_SUMMARY")
            if item.participant_evidence_path_present != (item.state == "completed"):
                raise UsabilityTrialError("INVALID_SUMMARY")
        if (
            item.within_five_actions != (item.action_count <= 5)
            or item.time_saved_seconds
            != item.manual_baseline_seconds - item.elapsed_seconds
            or item.adoptable
            != (
                item.state == "completed"
                and item.structural_rewrite is False
                and item.disposition in {"exported", "retained"}
            )
            or item.call_observation
            != (item.result.call_observation if item.result else "not_observed")
            or item.citations_complete
            != (item.result.citations_complete if item.result else False)
        ):
            raise UsabilityTrialError("INVALID_SUMMARY")
        if item.result is not None and (
            item.result.call_observation
            != (
                "observed_zero"
                if item.result.provider_calls
                == item.result.embedding_calls
                == item.result.mcp_calls
                == 0
                else "observed_nonzero"
            )
        ):
            raise UsabilityTrialError("INVALID_SUMMARY")
        if item.state == "completed":
            if (
                item.result is None
                or item.result.result_state != "generated"
                or item.structural_rewrite is None
                or item.citation_usable is None
                or item.failure_stage is not None
                or item.failure_code is not None
            ):
                raise UsabilityTrialError("INVALID_SUMMARY")
        elif (
            item.failure_stage is None
            or item.failure_code is None
            or item.structural_rewrite is not None
            or item.citation_usable is not None
            or item.disposition != "not_kept"
        ):
            raise UsabilityTrialError("INVALID_SUMMARY")
        if item.disposition in {"exported", "retained"} and item.result is None:
            raise UsabilityTrialError("INVALID_SUMMARY")
        if item.call_observation == "observed_nonzero":
            raise UsabilityTrialError("INVALID_SUMMARY")
        if (item.adoptable) != (item.improvement_reason == "none"):
            raise UsabilityTrialError("INVALID_SUMMARY")
    bindings = [item.result for item in items if item.result is not None]
    for attribute in (
        "result_bytes_hash",
        "result_session_hash",
        "artifact_hash",
        "preview_hash",
    ):
        if len({getattr(item, attribute) for item in bindings}) != len(bindings):
            raise UsabilityTrialError("INVALID_SUMMARY")
    completed = sum(item.state == "completed" for item in items)
    adoptable = sum(item.adoptable for item in items)
    expected = SummaryMetrics(
        total_tasks=10,
        completed=completed,
        adoptable=adoptable,
        elapsed_seconds=sum(item.elapsed_seconds for item in items),
        manual_baseline_seconds=sum(item.manual_baseline_seconds for item in items),
        control_rating_total=sum(item.control_rating for item in items),
        citation_usable_true=sum(item.citation_usable is True for item in items),
        calls_observed_zero=sum(
            item.call_observation == "observed_zero" for item in items
        ),
        citations_complete=sum(item.citations_complete for item in items),
        max_action_count=max(item.action_count for item in items),
        within_five_actions=sum(item.within_five_actions for item in items),
        time_saved_seconds=sum(item.time_saved_seconds for item in items),
        improvement_reasons=tuple(
            (reason, sum(item.improvement_reason == reason for item in items))
            for reason in (
                "none",
                "clarity",
                "citation",
                "workflow",
                "artifact_quality",
                "other_structured",
            )
        ),
        blockers=tuple(
            sorted(
                Counter(
                    f"{item.failure_stage}:{item.failure_code}"
                    for item in items
                    if item.failure_stage is not None
                ).items()
            )
        ),
    )
    if summary.metrics != expected:
        raise UsabilityTrialError("INVALID_SUMMARY")
    gate = (
        "engineering_only"
        if summary.study.evaluation_kind == "synthetic_engineering_fixture"
        else "criteria_met_unanchored_awaiting_user_acceptance"
        if completed == 10
        and adoptable >= 7
        and expected.calls_observed_zero
        == expected.citations_complete
        == expected.citation_usable_true
        == 10
        and expected.max_action_count <= 5
        else "criteria_not_met"
    )
    if summary.gate_state != gate:
        raise UsabilityTrialError("INVALID_SUMMARY")


def create_study(
    study_id: str,
    evaluation_kind: EvaluationKind,
    artifact_kinds: Iterable[ArtifactKind],
    candidate_manifest_hash: str,
    *,
    candidate_profile: CandidateProfile | None = None,
) -> Study | StudyV2:
    kinds = tuple(artifact_kinds)
    if len(kinds) != 10:
        raise UsabilityTrialError("INVALID_STUDY")
    if candidate_profile is not None and (
        evaluation_kind != "human_usability"
        or candidate_profile not in _PDF_PROFILE_PRESENTATIONS
    ):
        raise UsabilityTrialError("INVALID_STUDY")
    raw: dict[str, object] = {
        "schema_version": STUDY_SCHEMA_VERSION_V2
        if candidate_profile
        else STUDY_SCHEMA_VERSION,
        "study_id": study_id,
        "evaluation_kind": evaluation_kind,
        "candidate_manifest_hash": candidate_manifest_hash,
        "tasks": tuple(
            {"task_id": task_id, "artifact_kind": kind}
            for task_id, kind in zip(TASK_IDS, kinds, strict=True)
        ),
    }
    if candidate_profile:
        raw["candidate_profile"] = candidate_profile
    try:
        model = StudyV2 if candidate_profile else Study
        candidate = model.model_validate({**raw, "study_hash": "0" * 64})
    except (ValidationError, ValueError) as error:
        raise UsabilityTrialError("INVALID_STUDY") from error
    return candidate.model_copy(
        update={
            "study_hash": _hash(
                "projecttown/v3/usability-study/v2"
                if candidate_profile
                else "projecttown/v3/usability-study/v1",
                _payload(candidate, "study_hash"),
            )
        }
    )


def _validated_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise UsabilityTrialError("INVALID_STUDY_ROOT")
    try:
        metadata = root.lstat()
        canonical = root.resolve(strict=True)
    except OSError as error:
        raise UsabilityTrialError("INVALID_STUDY_ROOT") from error
    if canonical != root or not is_safe_directory(metadata) or is_reparse(metadata):
        raise UsabilityTrialError("INVALID_STUDY_ROOT")
    return root


def _direct(root: Path, name: str) -> Path:
    root = _validated_root(root)
    path = root / name
    if path.parent != root:
        raise UsabilityTrialError("INVALID_RECORD_PATH")
    return path


def _load_direct(root: Path, name: str, parser: object) -> object:
    root = _validated_root(root)
    root_before = root.lstat()
    path = _direct(root, name)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise UsabilityTrialError("RECORD_UNAVAILABLE") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or metadata.st_size > MAX_RECORD_BYTES
    ):
        raise UsabilityTrialError("INVALID_RECORD_PATH")
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise UsabilityTrialError("UNSTABLE_RECORD")
    try:
        root_after = root.lstat()
    except OSError as error:
        raise UsabilityTrialError("UNSTABLE_RECORD") from error
    if (
        root_before.st_dev != root_after.st_dev
        or root_before.st_ino != root_after.st_ino
    ):
        raise UsabilityTrialError("UNSTABLE_RECORD")
    return parser(stable[2])  # type: ignore[operator]


def publish_study(root: Path, study: Study | StudyV2) -> None:
    publish_new_direct_child(
        _validated_root(root), _direct(root, "study.json"), serialize_study(study)
    )


def publish_trial(root: Path, trial: Trial | TrialV2 | TrialV3) -> None:
    publish_new_direct_child(
        _validated_root(root),
        _direct(root, f"{trial.task_id}.json"),
        serialize_trial(trial),
    )


def publish_summary(root: Path, summary: Summary | SummaryV2 | SummaryV3) -> None:
    publish_new_direct_child(
        _validated_root(root), _direct(root, "summary.json"), serialize_summary(summary)
    )


def load_study(root: Path) -> Study | StudyV2:
    return _load_direct(root, "study.json", parse_study_bytes)  # type: ignore[return-value]


def load_trial(root: Path, task_id: str) -> Trial | TrialV2 | TrialV3:
    if task_id not in TASK_IDS:
        raise UsabilityTrialError("INVALID_TASK")
    return _load_direct(root, f"{task_id}.json", parse_trial_bytes)  # type: ignore[return-value]


def load_summary(root: Path) -> Summary | SummaryV2 | SummaryV3:
    return _load_direct(root, "summary.json", parse_summary_bytes)  # type: ignore[return-value]


def _binding(
    material_root: Path,
    result_path: Path,
    export_path: Path | None,
    pdf_export_path: Path | None = None,
    pdf_export_version: str = PDF_EXPORT_VERSION,
) -> tuple[ResultBinding, PresentationBinding | None, ArtifactKind]:
    if (
        not isinstance(material_root, Path)
        or not material_root.is_absolute()
        or not isinstance(result_path, Path)
        or not result_path.is_absolute()
    ):
        raise UsabilityTrialError("INVALID_RESULT_BINDING")
    try:
        session = load_session(material_root, result_path)
    except MaterialWorkflowError as error:
        raise UsabilityTrialError("INVALID_RESULT_BINDING") from error
    if (
        not isinstance(session, ResultSession)
        or not verify_result_integrity(session)
        or not revalidate_result_sources(material_root, session)
    ):
        raise UsabilityTrialError("INVALID_RESULT_BINDING")
    result_bytes = serialize_session(session)
    if export_path is not None:
        if not export_path.is_absolute():
            raise UsabilityTrialError("INVALID_EXPORT")
        try:
            export_path.relative_to(material_root)
        except ValueError:
            pass
        else:
            raise UsabilityTrialError("INVALID_EXPORT")
        try:
            metadata = export_path.lstat()
            canonical = export_path.resolve(strict=True)
            parent = export_path.parent.lstat()
        except OSError as error:
            raise UsabilityTrialError("INVALID_EXPORT") from error
        if (
            canonical != export_path
            or not is_safe_directory(parent)
            or not stat.S_ISREG(metadata.st_mode)
            or is_reparse(metadata)
        ):
            raise UsabilityTrialError("INVALID_EXPORT")
        stable = read_stable_regular_file(
            export_path, metadata, capture_bytes=True, require_single_link=True
        )
        if stable is None or stable[2] != render_export(material_root, session):
            raise UsabilityTrialError("INVALID_EXPORT")
    presentation: PresentationBinding | None = None
    if pdf_export_path is not None:
        if not pdf_export_path.is_absolute():
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        try:
            pdf_export_path.relative_to(Path(__file__).resolve().parents[2])
        except ValueError:
            pass
        else:
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        try:
            pdf_export_path.relative_to(material_root)
        except ValueError:
            pass
        else:
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        try:
            metadata = pdf_export_path.lstat()
            canonical = pdf_export_path.resolve(strict=True)
            parent = pdf_export_path.parent.lstat()
        except OSError as error:
            raise UsabilityTrialError("INVALID_PDF_EXPORT") from error
        if (
            canonical != pdf_export_path
            or not is_safe_directory(parent)
            or not stat.S_ISREG(metadata.st_mode)
            or is_reparse(metadata)
        ):
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        stable = read_stable_regular_file(
            pdf_export_path, metadata, capture_bytes=True, require_single_link=True
        )
        try:
            expected_pdf = render_pdf_export(
                material_root, session, export_version=pdf_export_version
            )
        except MaterialWorkflowError as error:
            raise UsabilityTrialError("INVALID_PDF_EXPORT") from error
        if stable is None or stable[2] != expected_pdf:
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        try:
            presentation = PresentationBinding(
                presentation_format="pdf",
                pdf_bytes_hash=hashlib.sha256(stable[2]).hexdigest(),
                pdf_export_version=pdf_export_version,
                pdf_renderer_version=_PDF_PRESENTATION_PAIRS.get(
                    pdf_export_version, ""
                ),
                pdf_source_artifact_hash=session.artifact_hash,
            )
        except ValidationError as error:
            raise UsabilityTrialError("INVALID_PDF_EXPORT") from error
    coverage = session.coverage
    future = session.draft.future_parameters
    return (
        ResultBinding(
            result_session_hash=session.session_hash,
            result_bytes_hash=hashlib.sha256(result_bytes).hexdigest(),
            contract_hash=session.confirmed_contract_hash,
            artifact_hash=session.artifact_hash,
            preview_hash=session.preview_hash,
            result_state=session.state,
            citations_complete=coverage.cited_sources == coverage.total_sources
            and not coverage.uncited_sources,
            provider_calls=future.provider_calls,
            embedding_calls=future.embedding_calls,
            mcp_calls=future.mcp_calls,
            call_observation="observed_zero"
            if future.provider_calls == future.embedding_calls == future.mcp_calls == 0
            else "observed_nonzero",
        ),
        presentation,
        session.draft.artifact_kind,
    )


def _validated_participant_evidence(
    evidence_path: Path,
    pdf_export_path: Path,
    material_root: Path,
    presentation: PresentationBinding,
) -> str:
    """Bind the user-confirmed path to the same safe PDF used at creation."""
    if not all(
        isinstance(path, Path) and path.is_absolute()
        for path in (evidence_path, pdf_export_path, material_root)
    ):
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    repository = Path(__file__).resolve().parents[2]
    try:
        evidence_metadata = evidence_path.lstat()
        pdf_metadata = pdf_export_path.lstat()
        evidence_canonical = evidence_path.resolve(strict=True)
        pdf_canonical = pdf_export_path.resolve(strict=True)
        parent = evidence_path.parent.lstat()
        evidence_path.relative_to(repository)
    except ValueError:
        pass
    except OSError as error:
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH") from error
    else:
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    try:
        evidence_path.relative_to(material_root)
    except ValueError:
        pass
    else:
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    if (
        evidence_canonical != evidence_path
        or pdf_canonical != pdf_export_path
        or not is_safe_directory(parent)
        or is_reparse(parent)
        or not stat.S_ISREG(evidence_metadata.st_mode)
        or not stat.S_ISREG(pdf_metadata.st_mode)
        or is_reparse(evidence_metadata)
        or is_reparse(pdf_metadata)
    ):
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    if evidence_canonical != pdf_canonical or (
        evidence_metadata.st_dev != pdf_metadata.st_dev
        or evidence_metadata.st_ino != pdf_metadata.st_ino
    ):
        raise UsabilityTrialError("PARTICIPANT_EVIDENCE_PATH_MISMATCH")
    stable = read_stable_regular_file(
        evidence_path, evidence_metadata, capture_bytes=False, require_single_link=True
    )
    if stable is None or stable[0] != presentation.pdf_bytes_hash:
        raise UsabilityTrialError("PARTICIPANT_EVIDENCE_DRIFT")
    return str(evidence_path)


def _verify_participant_evidence(trial: TrialV3) -> None:
    if trial.state != "completed":
        return
    if trial.participant_evidence_path is None or trial.presentation is None:
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    path = Path(trial.participant_evidence_path)
    if not path.is_absolute() or str(path) != trial.participant_evidence_path:
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    repository = Path(__file__).resolve().parents[2]
    try:
        metadata = path.lstat()
        canonical = path.resolve(strict=True)
        parent = path.parent.lstat()
        path.relative_to(repository)
    except ValueError:
        pass
    except OSError as error:
        raise UsabilityTrialError("PARTICIPANT_EVIDENCE_UNAVAILABLE") from error
    else:
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    if (
        canonical != path
        or not is_safe_directory(parent)
        or is_reparse(parent)
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
    ):
        raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    stable = read_stable_regular_file(path, metadata, require_single_link=True)
    if stable is None:
        raise UsabilityTrialError("PARTICIPANT_EVIDENCE_UNAVAILABLE")
    if stable[0] != trial.presentation.pdf_bytes_hash:
        raise UsabilityTrialError("PARTICIPANT_EVIDENCE_DRIFT")


def verify_trial(trial: Trial | TrialV2 | TrialV3) -> bool:
    try:
        serialize_trial(trial)
        if isinstance(trial, TrialV3):
            _verify_participant_evidence(trial)
        return True
    except UsabilityTrialError:
        return False


def create_trial(
    study: Study | StudyV2,
    task_id: str,
    *,
    state: TrialState,
    actions: Iterable[Action] = ("open_task",),
    elapsed_seconds: int = 1,
    manual_baseline_seconds: int = 1,
    control_rating: int = 1,
    structural_rewrite: bool | None = None,
    citation_usable: bool | None = None,
    disposition: Disposition = "not_kept",
    failure_stage: FailureStage | None = None,
    failure_code: FailureCode | None = None,
    improvement_reason: ImprovementReason = "none",
    material_root: Path | None = None,
    result_path: Path | None = None,
    export_path: Path | None = None,
    pdf_export_path: Path | None = None,
    participant_notes: str | None = None,
    participant_timestamp: str | None = None,
    participant_evidence_path: Path | None = None,
) -> Trial | TrialV2 | TrialV3:
    if not isinstance(study, (Study, StudyV2)):
        raise UsabilityTrialError("INVALID_STUDY")
    serialize_study(study)
    task = next((item for item in study.tasks if item.task_id == task_id), None)
    if task is None:
        raise UsabilityTrialError("INVALID_TASK")
    if (material_root is None) != (result_path is None):
        raise UsabilityTrialError("INVALID_RESULT_BINDING")
    v5 = isinstance(study, StudyV2) and study.candidate_profile in {
        "projecttown-human-pdf-v5",
        "projecttown-human-pdf-v6",
        "projecttown-human-pdf-v7",
        "projecttown-human-pdf-v8",
        "projecttown-human-pdf-v9",
        "projecttown-human-pdf-v10",
    }
    if not v5 and any(
        value is not None
        for value in (
            participant_notes,
            participant_timestamp,
            participant_evidence_path,
        )
    ):
        raise UsabilityTrialError("PARTICIPANT_EVIDENCE_NOT_ALLOWED")
    if v5 and (participant_notes is None or participant_timestamp is None):
        raise UsabilityTrialError("MISSING_PARTICIPANT_EVIDENCE")
    if v5:
        try:
            participant_notes = _normalise_participant_notes(participant_notes)
        except (TypeError, ValueError) as error:
            raise UsabilityTrialError("INVALID_PARTICIPANT_NOTES") from error
        try:
            participant_timestamp = _normalise_participant_timestamp(
                participant_timestamp
            )
        except (TypeError, ValueError) as error:
            raise UsabilityTrialError("INVALID_PARTICIPANT_TIMESTAMP") from error
    expected_pdf_export = (
        _PDF_PROFILE_PRESENTATIONS[study.candidate_profile][0]
        if isinstance(study, StudyV2)
        else PDF_EXPORT_VERSION
    )
    binding = (
        _binding(
            material_root,
            result_path,
            export_path,
            pdf_export_path,
            expected_pdf_export,
        )
        if material_root is not None and result_path is not None
        else None
    )
    result, presentation, result_artifact_kind = (
        binding if binding is not None else (None, None, None)
    )
    if isinstance(study, StudyV2):
        expected_presentation = _PDF_PROFILE_PRESENTATIONS.get(study.candidate_profile)
        if expected_presentation is None:
            raise UsabilityTrialError("INVALID_STUDY")
        if export_path is not None:
            raise UsabilityTrialError("INVALID_EXPORT")
        if result is not None and result_artifact_kind != task.artifact_kind:
            raise UsabilityTrialError("INVALID_RESULT_BINDING")
        if state == "completed" and (pdf_export_path is None or presentation is None):
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        if (
            presentation is not None
            and (
                presentation.pdf_export_version,
                presentation.pdf_renderer_version,
            )
            != expected_presentation
        ):
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        if state != "completed" and pdf_export_path is not None:
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        if disposition == "exported" and result is None:
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        if disposition == "retained" and result is None:
            raise UsabilityTrialError("INVALID_RESULT_BINDING")
    else:
        if pdf_export_path is not None:
            raise UsabilityTrialError("INVALID_PDF_EXPORT")
        if export_path is not None and disposition != "exported":
            raise UsabilityTrialError("INVALID_EXPORT")
        if disposition == "exported" and (export_path is None or result is None):
            raise UsabilityTrialError("INVALID_EXPORT")
        if disposition == "retained" and result is None:
            raise UsabilityTrialError("INVALID_RESULT_BINDING")
    evidence: str | None = None
    if v5:
        if state == "completed":
            if (
                participant_evidence_path is None
                or pdf_export_path is None
                or presentation is None
                or material_root is None
            ):
                raise UsabilityTrialError("MISSING_PARTICIPANT_EVIDENCE")
            evidence = _validated_participant_evidence(
                participant_evidence_path, pdf_export_path, material_root, presentation
            )
        elif participant_evidence_path is not None:
            raise UsabilityTrialError("INVALID_PARTICIPANT_EVIDENCE_PATH")
    raw: dict[str, object] = {
        "schema_version": TRIAL_SCHEMA_VERSION_V3
        if v5
        else TRIAL_SCHEMA_VERSION_V2
        if isinstance(study, StudyV2)
        else TRIAL_SCHEMA_VERSION,
        "study_id": study.study_id,
        "study_hash": study.study_hash,
        "evaluation_kind": study.evaluation_kind,
        "task_id": task_id,
        "artifact_kind": task.artifact_kind,
        "state": state,
        "actions": tuple(actions),
        "elapsed_seconds": elapsed_seconds,
        "manual_baseline_seconds": manual_baseline_seconds,
        "control_rating": control_rating,
        "structural_rewrite": structural_rewrite,
        "citation_usable": citation_usable,
        "disposition": disposition,
        "failure_stage": failure_stage,
        "failure_code": failure_code,
        "improvement_reason": improvement_reason,
        "measurement_provenance": "human_reported_current_invocation"
        if study.evaluation_kind == "human_usability"
        else "synthetic_fixture",
        "call_observation": result.call_observation
        if result is not None
        else "not_observed",
        "result": None if result is None else result,
        "retained_result_bytes_hash": result.result_bytes_hash
        if disposition == "retained" and result
        else None,
    }
    if isinstance(study, StudyV2):
        raw["presentation"] = presentation
    if v5:
        raw.update(
            participant_notes=participant_notes,
            participant_timestamp=participant_timestamp,
            participant_evidence_path=evidence,
        )
    try:
        model = TrialV3 if v5 else TrialV2 if isinstance(study, StudyV2) else Trial
        candidate = model.model_validate({**raw, "record_hash": "0" * 64})
    except ValidationError as error:
        raise UsabilityTrialError("INVALID_TRIAL") from error
    return candidate.model_copy(
        update={
            "record_hash": _hash(
                "projecttown/v3/usability-trial/v3"
                if v5
                else "projecttown/v3/usability-trial/v2"
                if isinstance(study, StudyV2)
                else "projecttown/v3/usability-trial/v1",
                _payload(candidate, "record_hash"),
            )
        }
    )


def _adoptable(trial: Trial) -> bool:
    return (
        trial.state == "completed"
        and trial.structural_rewrite is False
        and trial.disposition in {"exported", "retained"}
    )


def aggregate_summary(
    study: Study | StudyV2, trials: Iterable[Trial | TrialV2 | TrialV3]
) -> Summary | SummaryV2 | SummaryV3:
    serialize_study(study)
    records = tuple(trials)
    if len(records) != 10 or tuple(item.task_id for item in records) != TASK_IDS:
        raise UsabilityTrialError("INVALID_TRIAL_SET")
    if len({item.record_hash for item in records}) != 10:
        raise UsabilityTrialError("DUPLICATE_BINDING")
    result_bytes: set[str] = set()
    sessions: set[str] = set()
    artifacts: set[str] = set()
    previews: set[str] = set()
    for trial, task in zip(records, study.tasks, strict=True):
        if not verify_trial(trial):
            raise UsabilityTrialError("INVALID_TRIAL")
        v5 = isinstance(study, StudyV2) and study.candidate_profile in {
            "projecttown-human-pdf-v5",
            "projecttown-human-pdf-v6",
            "projecttown-human-pdf-v7",
            "projecttown-human-pdf-v8",
            "projecttown-human-pdf-v9",
            "projecttown-human-pdf-v10",
        }
        if v5 != isinstance(trial, TrialV3):
            raise UsabilityTrialError("STUDY_MISMATCH")
        if not v5 and isinstance(study, StudyV2) != isinstance(trial, TrialV2):
            raise UsabilityTrialError("STUDY_MISMATCH")
        if (
            isinstance(study, StudyV2)
            and trial.state == "completed"
            and trial.presentation is None
        ):
            raise UsabilityTrialError("INVALID_RESULT_BINDING")
        if (
            isinstance(study, StudyV2)
            and trial.presentation is not None
            and (
                trial.presentation.pdf_export_version,
                trial.presentation.pdf_renderer_version,
            )
            != _PDF_PROFILE_PRESENTATIONS.get(study.candidate_profile)
        ):
            raise UsabilityTrialError("INVALID_RESULT_BINDING")
        if (
            trial.study_id,
            trial.study_hash,
            trial.evaluation_kind,
            trial.artifact_kind,
        ) != (
            study.study_id,
            study.study_hash,
            study.evaluation_kind,
            task.artifact_kind,
        ):
            raise UsabilityTrialError("STUDY_MISMATCH")
        if trial.result is not None:
            binding_values = (
                (result_bytes, trial.result.result_bytes_hash),
                (sessions, trial.result.result_session_hash),
                (artifacts, trial.result.artifact_hash),
                (previews, trial.result.preview_hash),
            )
            if any(value in seen for seen, value in binding_values):
                raise UsabilityTrialError("DUPLICATE_BINDING")
            for seen, value in binding_values:
                seen.add(value)
    reasons = tuple(
        (reason, sum(item.improvement_reason == reason for item in records))
        for reason in (
            "none",
            "clarity",
            "citation",
            "workflow",
            "artifact_quality",
            "other_structured",
        )
    )
    completed = sum(item.state == "completed" for item in records)
    metrics = SummaryMetrics(
        total_tasks=10,
        completed=completed,
        adoptable=sum(_adoptable(item) for item in records),
        elapsed_seconds=sum(item.elapsed_seconds for item in records),
        manual_baseline_seconds=sum(item.manual_baseline_seconds for item in records),
        control_rating_total=sum(item.control_rating for item in records),
        citation_usable_true=sum(item.citation_usable is True for item in records),
        calls_observed_zero=sum(
            item.call_observation == "observed_zero" for item in records
        ),
        citations_complete=sum(
            item.result is not None and item.result.citations_complete
            for item in records
        ),
        max_action_count=max((len(item.actions) for item in records), default=0),
        within_five_actions=sum(len(item.actions) <= 5 for item in records),
        time_saved_seconds=sum(
            item.manual_baseline_seconds - item.elapsed_seconds for item in records
        ),
        improvement_reasons=reasons,
        blockers=tuple(
            sorted(
                Counter(
                    f"{item.failure_stage}:{item.failure_code}"
                    for item in records
                    if item.failure_stage is not None
                ).items()
            )
        ),
    )
    if study.evaluation_kind == "synthetic_engineering_fixture":
        gate = "engineering_only"
    elif (
        completed == 10
        and metrics.adoptable >= 7
        and metrics.calls_observed_zero == 10
        and metrics.citations_complete == 10
        and metrics.citation_usable_true == 10
        and metrics.max_action_count <= 5
    ):
        gate = "criteria_met_unanchored_awaiting_user_acceptance"
    else:
        gate = "criteria_not_met"
    v5 = isinstance(study, StudyV2) and study.candidate_profile in {
        "projecttown-human-pdf-v5",
        "projecttown-human-pdf-v6",
        "projecttown-human-pdf-v7",
        "projecttown-human-pdf-v8",
        "projecttown-human-pdf-v9",
        "projecttown-human-pdf-v10",
    }
    projection_model = (
        TrialProjectionV3
        if v5
        else TrialProjectionV2
        if isinstance(study, StudyV2)
        else TrialProjection
    )
    projections = tuple(
        projection_model(
            task_id=item.task_id,
            artifact_kind=item.artifact_kind,
            state=item.state,
            record_hash=item.record_hash,
            result=item.result,
            action_count=len(item.actions),
            elapsed_seconds=item.elapsed_seconds,
            manual_baseline_seconds=item.manual_baseline_seconds,
            control_rating=item.control_rating,
            structural_rewrite=item.structural_rewrite,
            citation_usable=item.citation_usable,
            disposition=item.disposition,
            improvement_reason=item.improvement_reason,
            call_observation=item.call_observation,
            citations_complete=item.result.citations_complete if item.result else False,
            adoptable=_adoptable(item),
            failure_stage=item.failure_stage,
            failure_code=item.failure_code,
            within_five_actions=len(item.actions) <= 5,
            time_saved_seconds=item.manual_baseline_seconds - item.elapsed_seconds,
            **(
                {"presentation": item.presentation} if isinstance(item, TrialV2) else {}
            ),
            **(
                {
                    "participant_notes_present": True,
                    "participant_timestamp_present": True,
                    "participant_evidence_path_present": item.participant_evidence_path
                    is not None,
                }
                if isinstance(item, TrialV3)
                else {}
            ),
        )
        for item in records
    )
    raw: dict[str, object] = {
        "schema_version": SUMMARY_SCHEMA_VERSION_V3
        if v5
        else SUMMARY_SCHEMA_VERSION_V2
        if isinstance(study, StudyV2)
        else SUMMARY_SCHEMA_VERSION,
        "study": study,
        "projections": projections,
        "metrics": metrics,
        "gate_state": gate,
    }
    model = SummaryV3 if v5 else SummaryV2 if isinstance(study, StudyV2) else Summary
    candidate = model.model_validate({**raw, "summary_hash": "0" * 64})
    return candidate.model_copy(
        update={
            "summary_hash": _hash(
                "projecttown/v3/usability-summary/v3"
                if v5
                else "projecttown/v3/usability-summary/v2"
                if isinstance(study, StudyV2)
                else "projecttown/v3/usability-summary/v1",
                _payload(candidate, "summary_hash"),
            )
        }
    )


def verify_summary(
    study: Study | StudyV2,
    trials: Iterable[Trial | TrialV2 | TrialV3],
    summary: Summary | SummaryV2 | SummaryV3,
) -> bool:
    try:
        return serialize_summary(summary) == serialize_summary(
            aggregate_summary(study, trials)
        )
    except UsabilityTrialError:
        return False


def render_summary(summary: Summary | SummaryV2 | SummaryV3) -> str:
    serialize_summary(summary)
    blockers = (
        "none"
        if not summary.metrics.blockers
        else ",".join(
            f"{stage_code}={count}" for stage_code, count in summary.metrics.blockers
        )
    )
    return (
        "\n".join(
            (
                "# Phase 2 trial summary",
                f"- Evaluation: `{summary.study.evaluation_kind}`",
                f"- Gate: `{summary.gate_state}`",
                f"- Completed: {summary.metrics.completed}/10",
                f"- Derived adoptable: {summary.metrics.adoptable}/10",
                f"- Within five actions: {summary.metrics.within_five_actions}/10",
                f"- Time saved seconds: {summary.metrics.time_saved_seconds}",
                f"- Control total: {summary.metrics.control_rating_total}",
                f"- Citation usable/complete: {summary.metrics.citation_usable_true}/{summary.metrics.citations_complete}",
                f"- Zero-call observations: {summary.metrics.calls_observed_zero}/10",
                f"- Blockers: {blockers}",
                "- Integrity: self-consistent, external bindings unanchored",
            )
        )
        + "\n"
    )
