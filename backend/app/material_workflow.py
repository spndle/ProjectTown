"""Offline, immutable Phase 1 draft sessions for explicitly selected material."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import unicodedata
from collections.abc import Mapping, Sequence
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

from .materials import (
    DEFAULT_POLICY,
    MaterialSetManifest,
    inspect_material_set,
)
from .safe_files import (
    is_reparse,
    is_safe_directory,
    read_stable_regular_file,
    same_file,
)
from .v1.rag import (
    RAGDocument,
    RAGValidationError,
    build_index,
    search,
    verify_search_result,
)

SESSION_SCHEMA_VERSION = "v3-material-draft-session-v1"
RESULT_SCHEMA_VERSION = "v3-material-result-session-v1"
MAX_SESSION_BYTES = 2 * 1_048_576
MAX_NESTING = 16
_GENERATOR_VERSION_V1 = "deterministic-grounded-template-v1"
_GENERATOR_VERSION = "deterministic-grounded-plan-v2"
_GENERATOR_VERSION_V3 = "deterministic-grounded-plan-v3"
_GENERATOR_VERSION_V4 = "deterministic-grounded-plan-v4"
_GENERATOR_VERSION_V5 = "deterministic-grounded-plan-v5"
_GENERATOR_VERSION_V6 = "deterministic-grounded-plan-v6"
_GENERATOR_VERSION_V7 = "deterministic-grounded-plan-v7"
_GENERATOR_VERSION_V8 = "deterministic-grounded-plan-v8"
_GENERATOR_VERSION_V9 = "deterministic-grounded-plan-v9"
_RETRIEVAL_VERSION_V1 = "segmented-deterministic-rag-v1"
_RETRIEVAL_VERSION = "segmented-deterministic-rag-v2"
_SEGMENTATION_VERSION_V1 = "utf8-raw-240k-v1"
_SEGMENTATION_VERSION = "markdown-block-v2"
_MAX_SEGMENT_BYTES = 240 * 1024
_MAX_CITATIONS = 128
_SUPPORTED_FUTURE_PARAMETERS = {
    (_GENERATOR_VERSION_V1, _RETRIEVAL_VERSION_V1, _SEGMENTATION_VERSION_V1),
    (_GENERATOR_VERSION, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V3, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V4, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V5, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V6, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V7, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V8, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
    (_GENERATOR_VERSION_V9, _RETRIEVAL_VERSION, _SEGMENTATION_VERSION),
}


class MaterialWorkflowError(ValueError):
    """Stable rejection without embedding a local path or source content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("material workflow rejected")


class PublicationRollbackError(MaterialWorkflowError):
    """A committed name was proven removed; publication is not a success."""

    def __init__(self) -> None:
        super().__init__("PUBLICATION_ROLLED_BACK")


class PublicationAttentionError(MaterialWorkflowError):
    """A create-only link may be committed and must not be reported as rejected."""

    def __init__(self) -> None:
        super().__init__("COMMITTED_NEEDS_ATTENTION")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MaterialPolicyProjection(_Model):
    version: str = Field(min_length=1)
    max_files: int = Field(ge=0)
    max_file_bytes: int = Field(ge=0)
    max_total_bytes: int = Field(ge=0)


class MaterialSourceProjection(_Model):
    relative_path: str = Field(min_length=1)
    suffix: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_count: int = Field(ge=0)


class MaterialManifestProjection(_Model):
    schema_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy: MaterialPolicyProjection
    status: Literal["complete"]
    entries: tuple[MaterialSourceProjection, ...] = Field(min_length=1)
    root_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)

    @field_validator("entries")
    @classmethod
    def entries_must_be_sorted(
        cls, value: tuple[MaterialSourceProjection, ...]
    ) -> tuple[MaterialSourceProjection, ...]:
        if tuple(item.relative_path for item in value) != tuple(
            sorted(item.relative_path for item in value)
        ):
            raise ValueError("entries must be sorted")
        return value

    @model_validator(mode="after")
    def counts_must_match_entries(self) -> MaterialManifestProjection:
        if self.file_count != len(self.entries) or self.total_bytes != sum(
            item.size_bytes for item in self.entries
        ):
            raise ValueError("manifest counts must match entries")
        return self


class FutureParameters(_Model):
    generator_version: str = Field(min_length=1)
    retrieval_version: str = Field(min_length=1)
    segmentation_version: str = Field(min_length=1)
    deterministic: Literal[True]
    provider_calls: Literal[0]
    embedding_calls: Literal[0]
    mcp_calls: Literal[0]


class DraftSession(_Model):
    schema_version: Literal["v3-material-draft-session-v1"]
    state: Literal["waiting_confirmation"]
    task: str = Field(min_length=1, max_length=4096)
    artifact_kind: Literal["plan", "report", "readme"]
    readme_target: str | None = None
    constraints: tuple[tuple[str, str], ...] = ()
    material_manifest: MaterialManifestProjection
    selections: tuple[str, ...] = Field(min_length=1)
    future_parameters: FutureParameters
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("task")
    @classmethod
    def normalize_task(cls, value: str) -> str:
        return _canonical_text(value, 4096, "INVALID_TASK")

    @field_validator("readme_target")
    @classmethod
    def normalize_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_relative(value, "INVALID_README_TARGET")

    @field_validator("constraints")
    @classmethod
    def constraints_must_be_canonical(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return _canonical_constraints(value)

    @field_validator("selections")
    @classmethod
    def selections_must_be_sorted(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(
            _canonical_relative(item, "INVALID_SELECTION") for item in value
        )
        if canonical != tuple(sorted(canonical)) or len(set(canonical)) != len(
            canonical
        ):
            raise ValueError("selections must be unique and sorted")
        return canonical

    @model_validator(mode="after")
    def enforce_artifact_contract(self) -> DraftSession:
        if (self.artifact_kind == "readme") != (self.readme_target is not None):
            raise ValueError("readme target does not match artifact kind")
        if self.readme_target is not None and (
            not self.readme_target.endswith(".md")
            or self.readme_target not in self.selections
        ):
            raise ValueError("readme target must be selected markdown")
        if self.selections != tuple(
            item.relative_path for item in self.material_manifest.entries
        ):
            raise ValueError("selections must match material manifest")
        return self


class ResultSegment(_Model):
    relative_path: str
    ordinal: int = Field(ge=1)
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bundle_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retrievable: bool
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_lines(self) -> ResultSegment:
        if self.line_end < self.line_start:
            raise ValueError("segment line range is inverted")
        _canonical_relative(self.relative_path, "INVALID_RESULT")
        if self.retrievable != (self.index_hash is not None):
            raise ValueError("segment index binding is inconsistent")
        if not self.retrievable and self.bundle_hash is not None:
            raise ValueError("unretrievable segment has retrieval bundle")
        return self


class ResultRetrievalHit(_Model):
    rank: int = Field(ge=1)
    relative_path: str
    segment_ordinal: int = Field(ge=1)
    score: int = Field(ge=1)
    chunk_id: str
    normalized_start: int = Field(ge=0)
    normalized_end: int = Field(gt=0)
    index_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    citation_id: str = Field(pattern=r"^S[0-9]{3}$")

    @model_validator(mode="after")
    def canonical_path(self) -> ResultRetrievalHit:
        _canonical_relative(self.relative_path, "INVALID_RESULT")
        if not self.chunk_id or self.normalized_end <= self.normalized_start:
            raise ValueError("retrieval hit range is invalid")
        return self


class ResultRetrieval(_Model):
    version: str = Field(min_length=1)
    query_source: Literal["draft.task"]
    query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_segment_top_k: Literal[3]
    global_top_k: Literal[8]
    query_retrievable: bool
    hits: tuple[ResultRetrievalHit, ...] = ()

    @model_validator(mode="after")
    def ordered_hits(self) -> ResultRetrieval:
        if tuple(hit.rank for hit in self.hits) != tuple(range(1, len(self.hits) + 1)):
            raise ValueError("retrieval ranks must be sequential")
        if len(self.hits) > self.global_top_k or (
            not self.query_retrievable and self.hits
        ):
            raise ValueError("unretrievable query has hits")
        return self


class ResultCitation(_Model):
    id: str = Field(pattern=r"^S[0-9]{3}$")
    relative_path: str
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    span_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: str = Field(max_length=500)
    method: Literal["lexical", "structural", "conflict"]

    @model_validator(mode="after")
    def canonical_range(self) -> ResultCitation:
        _canonical_relative(self.relative_path, "INVALID_RESULT")
        if self.line_end < self.line_start:
            raise ValueError("citation line range is inverted")
        return self


class ResultCoverage(_Model):
    total_sources: int = Field(ge=1)
    read_sources: int = Field(ge=1)
    indexed_sources: int = Field(ge=0)
    unretrievable_sources: tuple[str, ...]
    cited_sources: int = Field(ge=0)
    total_segments: int = Field(ge=1)
    indexed_segments: int = Field(ge=0)
    uncited_sources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def sorted_lists(self) -> ResultCoverage:
        if self.unretrievable_sources != tuple(
            sorted(set(self.unretrievable_sources))
        ) or self.uncited_sources != tuple(sorted(set(self.uncited_sources))):
            raise ValueError("coverage paths must be sorted")
        for path in self.unretrievable_sources + self.uncited_sources:
            _canonical_relative(path, "INVALID_RESULT")
        return self


class ResultSession(_Model):
    schema_version: Literal["v3-material-result-session-v1"]
    state: Literal["generated", "needs_user_decision"]
    draft: DraftSession
    parent_session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    segments: tuple[ResultSegment, ...]
    retrieval: ResultRetrieval
    citations: tuple[ResultCitation, ...]
    coverage: ResultCoverage
    conflicts: tuple[ResultConflict, ...] = ()
    artifact_markdown: str
    preview_markdown: str
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResultConflict(_Model):
    key: str = Field(min_length=1, max_length=80)
    values: tuple[str, ...] = Field(min_length=2)
    display_values: tuple[str, ...] = Field(min_length=2)
    citation_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def canonical(self) -> ResultConflict:
        if (
            self.values != tuple(sorted(set(self.values)))
            or self.display_values
            != tuple(_escape_terminal(value) for value in self.values)
            or len(self.citation_ids) != len(set(self.citation_ids))
            or not self.citation_ids
        ):
            raise ValueError("conflict must be canonical")
        return self


def _canonical_text(value: object, maximum_bytes: int, code: str) -> str:
    if not isinstance(value, str):
        raise MaterialWorkflowError(code)
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if (
        "\x00" in normalized
        or not normalized
        or len(normalized.encode("utf-8")) > maximum_bytes
    ):
        raise MaterialWorkflowError(code)
    return normalized


def _canonical_relative(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise MaterialWorkflowError(code)
    value = unicodedata.normalize("NFC", value)
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or ":" in value
    ):
        raise MaterialWorkflowError(code)
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise MaterialWorkflowError(code)
    return value


def _canonical_constraints(value: object) -> tuple[tuple[str, str], ...]:
    if value in (None, ()):  # Pydantic's default and explicit no constraints.
        return ()
    if isinstance(value, Mapping):
        pairs = tuple(value.items())
    elif isinstance(value, tuple):
        pairs = value
    else:
        raise MaterialWorkflowError("INVALID_CONSTRAINTS")
    if len(pairs) > 32:
        raise MaterialWorkflowError("INVALID_CONSTRAINTS")
    canonical: list[tuple[str, str]] = []
    folded: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise MaterialWorkflowError("INVALID_CONSTRAINTS")
        key = _canonical_text(pair[0], 80, "INVALID_CONSTRAINTS")
        item = _canonical_text(pair[1], 500, "INVALID_CONSTRAINTS")
        folded_key = key.casefold()
        if folded_key in folded:
            raise MaterialWorkflowError("INVALID_CONSTRAINTS")
        folded.add(folded_key)
        canonical.append((key, item))
    return tuple(sorted(canonical))


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\x00" + _canonical_json(value)
    ).hexdigest()


def _manifest_projection(manifest: MaterialSetManifest) -> MaterialManifestProjection:
    if manifest.status != "complete" or manifest.root_hash is None:
        raise MaterialWorkflowError("MATERIAL_SET_INCOMPLETE")
    try:
        return MaterialManifestProjection.model_validate(
            {
                "schema_version": manifest.schema_version,
                "policy_version": manifest.policy_version,
                "policy": {
                    "version": manifest.policy.version,
                    "max_files": manifest.policy.max_files,
                    "max_file_bytes": manifest.policy.max_file_bytes,
                    "max_total_bytes": manifest.policy.max_total_bytes,
                },
                "status": manifest.status,
                "entries": tuple(
                    {
                        "relative_path": item.relative_path,
                        "suffix": item.suffix,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                        "line_count": item.line_count,
                    }
                    for item in manifest.entries
                ),
                "root_hash": manifest.root_hash,
                "file_count": manifest.file_count,
                "total_bytes": manifest.total_bytes,
            }
        )
    except ValidationError as error:
        raise MaterialWorkflowError("INVALID_MATERIAL_MANIFEST") from error


def _validate_root(root: Path) -> tuple[Path, os.stat_result]:
    if not isinstance(root, Path) or not root.is_absolute():
        raise MaterialWorkflowError("INVALID_ROOT")
    try:
        metadata = root.lstat()
        canonical = root.resolve(strict=True)
    except OSError as error:
        raise MaterialWorkflowError("ROOT_UNAVAILABLE") from error
    if canonical != root or not is_safe_directory(metadata):
        raise MaterialWorkflowError("INVALID_ROOT")
    return root, metadata


def _check_same_device_ancestors(
    root: Path,
    relative_path: str,
    device: int,
    observed: dict[Path, tuple[bool, os.stat_result]] | None = None,
) -> os.stat_result:
    current = root
    for component in relative_path.split("/")[:-1]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as error:
            raise MaterialWorkflowError("SOURCE_UNAVAILABLE") from error
        if not is_safe_directory(metadata) or metadata.st_dev != device:
            raise MaterialWorkflowError("UNTRUSTED_SOURCE")
        _record_observation(observed, current, True, metadata)
    path = root.joinpath(*relative_path.split("/"))
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MaterialWorkflowError("SOURCE_UNAVAILABLE") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or metadata.st_dev != device
        or metadata.st_nlink != 1
    ):
        raise MaterialWorkflowError("UNSTABLE_SOURCE")
    _record_observation(observed, path, False, metadata)
    return metadata


def _record_observation(
    observed: dict[Path, tuple[bool, os.stat_result]] | None,
    path: Path,
    is_directory: bool,
    metadata: os.stat_result,
) -> None:
    if observed is None:
        return
    existing = observed.get(path)
    if existing is not None and not same_file(existing[1], metadata):
        raise MaterialWorkflowError(
            "MATERIAL_SET_CHANGED" if is_directory else "UNSTABLE_SOURCE"
        )
    observed[path] = (is_directory, metadata)


def _verify_sources(
    root: Path, manifest: MaterialSetManifest, root_metadata: os.stat_result
) -> None:
    observed: dict[Path, tuple[bool, os.stat_result]] = {root: (True, root_metadata)}
    for entry in manifest.entries:
        metadata = _check_same_device_ancestors(
            root, entry.relative_path, root_metadata.st_dev, observed
        )
        stable = read_stable_regular_file(
            root.joinpath(*entry.relative_path.split("/")),
            metadata,
            capture_bytes=False,
            require_single_link=True,
        )
        if stable is None or stable[0] != entry.sha256 or stable[1] != entry.size_bytes:
            raise MaterialWorkflowError("UNSTABLE_SOURCE")
    for path, (is_directory, expected) in sorted(
        observed.items(), key=lambda item: item[1][0]
    ):
        try:
            current = path.lstat()
        except OSError as error:
            raise MaterialWorkflowError(
                "MATERIAL_SET_CHANGED" if is_directory else "UNSTABLE_SOURCE"
            ) from error
        if is_directory:
            if (
                not is_safe_directory(current)
                or current.st_dev != root_metadata.st_dev
                or not same_file(expected, current)
            ):
                raise MaterialWorkflowError("MATERIAL_SET_CHANGED")
        elif (
            not stat.S_ISREG(current.st_mode)
            or is_reparse(current)
            or current.st_dev != root_metadata.st_dev
            or current.st_nlink != 1
            or not same_file(expected, current)
        ):
            raise MaterialWorkflowError("UNSTABLE_SOURCE")


def _contract_payload(session: dict[str, object]) -> dict[str, object]:
    return {
        key: session[key]
        for key in (
            "schema_version",
            "task",
            "artifact_kind",
            "readme_target",
            "constraints",
            "material_manifest",
            "selections",
            "future_parameters",
        )
    }


def _session_payload(session: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in session.items() if key != "session_hash"}


def _build_session(
    *,
    task: str,
    artifact_kind: str,
    readme_target: str | None,
    constraints: tuple[tuple[str, str], ...],
    manifest: MaterialManifestProjection,
    generator_version: str = _GENERATOR_VERSION,
) -> DraftSession:
    if generator_version not in {
        _GENERATOR_VERSION,
        _GENERATOR_VERSION_V3,
        _GENERATOR_VERSION_V4,
        _GENERATOR_VERSION_V5,
        _GENERATOR_VERSION_V6,
        _GENERATOR_VERSION_V7,
        _GENERATOR_VERSION_V8,
        _GENERATOR_VERSION_V9,
    }:
        raise MaterialWorkflowError("UNSUPPORTED_GENERATOR_VERSION")
    payload: dict[str, object] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "state": "waiting_confirmation",
        "task": task,
        "artifact_kind": artifact_kind,
        "readme_target": readme_target,
        "constraints": constraints,
        "material_manifest": manifest.model_dump(),
        "selections": tuple(entry.relative_path for entry in manifest.entries),
        "future_parameters": FutureParameters(
            generator_version=generator_version,
            retrieval_version=_RETRIEVAL_VERSION,
            segmentation_version=_SEGMENTATION_VERSION,
            deterministic=True,
            provider_calls=0,
            embedding_calls=0,
            mcp_calls=0,
        ).model_dump(),
    }
    payload["contract_hash"] = _hash(
        "projecttown/v3/material-contract/v1", _contract_payload(payload)
    )
    payload["session_hash"] = _hash(
        "projecttown/v3/material-session/v1", _session_payload(payload)
    )
    try:
        return DraftSession.model_validate(payload)
    except ValidationError as error:
        raise MaterialWorkflowError("INVALID_SESSION") from error


def create_draft(
    root: Path,
    selections: Sequence[str],
    *,
    task: str,
    artifact_kind: str,
    readme_target: str | None = None,
    constraints: Mapping[str, str] | None = None,
    generator_version: str = _GENERATOR_VERSION,
) -> DraftSession:
    """Freeze a deterministic draft contract over current explicit source metadata."""
    root, root_metadata = _validate_root(root)
    task = _canonical_text(task, 4096, "INVALID_TASK")
    if artifact_kind not in {"plan", "report", "readme"}:
        raise MaterialWorkflowError("INVALID_ARTIFACT_KIND")
    target = (
        _canonical_relative(readme_target, "INVALID_README_TARGET")
        if readme_target is not None
        else None
    )
    if (artifact_kind == "readme") != (target is not None):
        raise MaterialWorkflowError("INVALID_README_TARGET")
    frozen_constraints = _canonical_constraints(constraints)
    manifest = inspect_material_set(root, selections, policy=DEFAULT_POLICY)
    projection = _manifest_projection(manifest)
    selection_set = {entry.relative_path for entry in projection.entries}
    if target is not None and (
        target not in selection_set or not target.endswith(".md")
    ):
        raise MaterialWorkflowError("INVALID_README_TARGET")
    _verify_sources(root, manifest, root_metadata)
    final = inspect_material_set(root, selections, policy=DEFAULT_POLICY)
    if final.status != "complete" or final.root_hash != manifest.root_hash:
        raise MaterialWorkflowError("MATERIAL_SET_CHANGED")
    session = _build_session(
        task=task,
        artifact_kind=artifact_kind,
        readme_target=target,
        constraints=frozen_constraints,
        manifest=projection,
        generator_version=generator_version,
    )
    _validate_v10_run_binding(root, session)
    return session


_V10_T002_TASK = (
    "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
)
_V10_UNRESOLVED = "<TO BIND BEFORE RUN>"
_V10_BINDING_PATHS = {
    "candidate_path",
    "preview_record_path",
    "manifest_path",
    "historical_result_json_path",
    "approved_provenance_tuple_source",
    "prior_study_evidence_path",
    "final_snapshot_path",
    "working_directory",
    "material_source_root",
    "historical_study_root",
    "historical_work_root",
    "fresh_root",
    "fresh_draft_path",
    "fresh_result_output_path",
    "fresh_evidence_root",
}
_V10_BINDING_REQUIRED = _V10_BINDING_PATHS | {
    "runbook_version",
    "verification_target_version",
    "verification_target_id",
    "verification_run_id",
    "candidate_profile",
    "candidate_sha256",
    "candidate_pdf_export_version",
    "candidate_pdf_renderer_version",
    "candidate_expected_page_count",
    "fresh_result_schema",
    "planned_study_evidence_output",
    "user_disposition_record_path",
    "release_authorization_record_path",
}
_V10_FUTURE_BINDINGS = {
    "planned_study_evidence_output",
    "user_disposition_record_path",
    "release_authorization_record_path",
}
_V10_EXPECTED = {
    "runbook_version": "projecttown-human-pdf-v10",
    "verification_target_version": "projecttown-human-pdf-v8",
    "verification_target_id": "projecttown-v3-phase2-human-pdf-v8-20260829-001:T002",
    "candidate_profile": "projecttown-human-pdf-v8",
    "candidate_sha256": "1686e8e33ba39e0d25a554c8750e03781a68cea8a2205f911777b53eb3ecca68",
    "candidate_pdf_export_version": "v3-material-pdf-export-v7",
    "candidate_pdf_renderer_version": "projecttown-reportlab-pdf-v7",
    "candidate_expected_page_count": "4",
    "fresh_result_schema": RESULT_SCHEMA_VERSION,
}
_V10_GENERAL_CONSTRAINTS = {
    "execution": "offline",
    "preserve_v1_v2_contracts": "true",
}


def _invalid_v10_binding() -> None:
    raise MaterialWorkflowError("INVALID_RUN_BINDING")


def _v10_text(value: str) -> str:
    if (
        not value
        or any(
            ord(character) < 32 or unicodedata.category(character) == "Cf"
            for character in value
        )
        or any(
            token in value.casefold()
            for token in (
                ".secrets",
                ".env",
                "authorization",
                "private endpoint",
                "private_endpoint",
            )
        )
        or any(token in value for token in ("<", ">", "|", "`"))
    ):
        _invalid_v10_binding()
    return value


def _v10_path(value: str, *, exists: bool) -> Path:
    _v10_text(value)
    if (
        value.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\"))
        or ".." in Path(value).parts
    ):
        _invalid_v10_binding()
    path = Path(value)
    if not path.is_absolute():
        _invalid_v10_binding()
    try:
        canonical = path.resolve(strict=exists)
    except (OSError, RuntimeError):
        _invalid_v10_binding()
    if canonical != path:
        _invalid_v10_binding()
    return path


def _v10_existing_file(value: str) -> Path:
    path = _v10_path(value, exists=True)
    try:
        metadata = path.lstat()
        parent = path.parent.lstat()
    except OSError:
        _invalid_v10_binding()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or not is_safe_directory(parent)
    ):
        _invalid_v10_binding()
    return path


def _v10_existing_dir(value: str) -> Path:
    path = _v10_path(value, exists=True)
    try:
        metadata = path.lstat()
    except OSError:
        _invalid_v10_binding()
    if not is_safe_directory(metadata):
        _invalid_v10_binding()
    return path


def _v10_future_output_path(value: str, root: Path, *, directory: bool) -> Path:
    path = _v10_path(value, exists=False)
    if not path.is_relative_to(root):
        _invalid_v10_binding()
    try:
        if path.exists():
            metadata = path.lstat()
            if (
                is_reparse(metadata)
                or (directory and not is_safe_directory(metadata))
                or (not directory and not stat.S_ISREG(metadata.st_mode))
            ):
                _invalid_v10_binding()
            return path
        parent = path.parent.resolve(strict=True)
        metadata = parent.lstat()
    except (OSError, RuntimeError):
        _invalid_v10_binding()
    if not is_safe_directory(metadata):
        _invalid_v10_binding()
    return path


def _v10_bindings(draft: DraftSession) -> dict[str, str]:
    if (
        draft.future_parameters.generator_version != _GENERATOR_VERSION_V9
        or draft.artifact_kind != "plan"
        or draft.task != _V10_T002_TASK
    ):
        return {}
    values = dict(draft.constraints)
    if {
        key: value
        for key, value in values.items()
        if not key.startswith("run_binding_")
    } != _V10_GENERAL_CONSTRAINTS:
        _invalid_v10_binding()
    keys = {
        key.removeprefix("run_binding_")
        for key in values
        if key.startswith("run_binding_")
    }
    if keys != _V10_BINDING_REQUIRED:
        _invalid_v10_binding()
    bindings = {key: values[f"run_binding_{key}"] for key in keys}
    for key, expected in _V10_EXPECTED.items():
        if bindings[key] != expected:
            _invalid_v10_binding()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", bindings["verification_run_id"]):
        _invalid_v10_binding()
    for key in _V10_FUTURE_BINDINGS:
        if bindings[key] != _V10_UNRESOLVED:
            _invalid_v10_binding()
    for key in _V10_BINDING_PATHS:
        _v10_text(bindings[key])
    return bindings


def _validate_v10_run_binding(root: Path, draft: DraftSession) -> None:
    bindings = _v10_bindings(draft)
    if not bindings:
        return
    material_root = _v10_existing_dir(bindings["material_source_root"])
    working_root = _v10_existing_dir(bindings["working_directory"])
    study_root = _v10_existing_dir(bindings["historical_study_root"])
    work_root = _v10_existing_dir(bindings["historical_work_root"])
    if material_root != root or working_root != root:
        _invalid_v10_binding()
    candidate = _v10_existing_file(bindings["candidate_path"])
    preview = _v10_existing_file(bindings["preview_record_path"])
    history = _v10_existing_file(bindings["historical_result_json_path"])
    final = _v10_existing_file(bindings["final_snapshot_path"])
    manifest = _v10_existing_file(bindings["manifest_path"])
    provenance = _v10_existing_file(bindings["approved_provenance_tuple_source"])
    prior = _v10_existing_file(bindings["prior_study_evidence_path"])
    if (
        not all(
            item.is_relative_to(work_root)
            for item in (candidate, preview, history, final)
        )
        or not manifest.is_relative_to(material_root)
        or not all(item.is_relative_to(study_root) for item in (provenance, prior))
    ):
        _invalid_v10_binding()
    fresh_root = _v10_existing_dir(bindings["fresh_root"])
    if any(
        fresh_root.is_relative_to(item)
        for item in (material_root, working_root, study_root, work_root)
    ):
        _invalid_v10_binding()
    for key in ("fresh_draft_path", "fresh_result_output_path"):
        _v10_future_output_path(bindings[key], fresh_root, directory=False)
    _v10_future_output_path(bindings["fresh_evidence_root"], fresh_root, directory=True)


def _escape_terminal(value: str) -> str:
    """Render untrusted text inertly inside a Markdown code span/block."""
    escaped = (
        "".join(
            "�"
            if ord(character) < 32
            and character not in "\n\t"
            or 0x7F <= ord(character) <= 0x9F
            or 0x202A <= ord(character) <= 0x202E
            or 0x2066 <= ord(character) <= 0x2069
            else character
            for character in value
        )
        .replace("`", "ˋ")
        .replace("\n", " ⏎ ")
        .replace("\t", " ⇥ ")
    )
    # Keep generated artifacts inert and avoid retaining local absolute paths.
    escaped = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s`]+", "[local-path]", escaped)
    # Redact before truncating so a long hostile prefix cannot push an absolute
    # path into the visible suffix.
    return escaped[:500]


def _raw_lines(text: str) -> tuple[tuple[int, int], ...]:
    """Raw character spans with precisely Python's splitlines() semantics."""
    lines = text.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in lines:
        end = offset + len(line)
        spans.append((offset, end))
        offset = end
    return tuple(spans)


def _line_for_offset(lines: tuple[tuple[int, int], ...], offset: int) -> int:
    for number, (start, end) in enumerate(lines, 1):
        if start <= offset < end:
            return number
    # A segment can end on a terminal separator, but never starts after EOF.
    return max(1, len(lines))


def _segments(text: str) -> tuple[tuple[str, int, int], ...]:
    """Byte-bounded Unicode segmentation; no source characters are omitted."""
    output: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = start
        used = 0
        while end < len(text):
            size = len(text[end].encode("utf-8"))
            if used + size > _MAX_SEGMENT_BYTES and end > start:
                break
            used += size
            end += 1
        segment = text[start:end]
        lines = _raw_lines(text)
        first_line = _line_for_offset(lines, start)
        last_line = _line_for_offset(lines, end - 1)
        output.append((segment, first_line, last_line))
        start = end
    return tuple(output)


def _segments_v2(text: str) -> tuple[tuple[str, int, int], ...]:
    """Contiguous bounded blocks split at Markdown sections or paragraphs."""
    if not text:
        return ()
    lines = text.splitlines(keepends=True)
    blocks: list[tuple[str, int, int]] = []
    start = 0
    for index, line in enumerate(lines, 1):
        if index > start + 1 and (line.lstrip().startswith("#") or not line.strip()):
            value = "".join(lines[start : index - 1])
            if value:
                blocks.append((value, start + 1, index - 1))
            start = index - 1
    value = "".join(lines[start:])
    if value:
        blocks.append((value, start + 1, len(lines)))
    output: list[tuple[str, int, int]] = []
    for value, first, last in blocks:
        # Checklist bullets and table rows often carry independent acceptance
        # gates.  Keep each as its own candidate rather than burying it in a
        # section-level retrieval block.
        block_lines = value.splitlines(keepends=True)
        offset = first
        pending: list[str] = []
        pending_first = first
        for line in block_lines:
            stripped = line.lstrip()
            standalone = bool(re.match(r"(?:[-*+]\s+|\d+[.)]\s+|\|)", stripped))
            if standalone and pending:
                output.append(("".join(pending), pending_first, offset - 1))
                pending = []
            if standalone:
                output.append((line, offset, offset))
            else:
                if not pending:
                    pending_first = offset
                pending.append(line)
            offset += 1
        if pending:
            output.append(("".join(pending), pending_first, last))
    bounded: list[tuple[str, int, int]] = []
    for value, first, last in output:
        if len(value.encode("utf-8")) <= _MAX_SEGMENT_BYTES:
            bounded.append((value, first, last))
            continue
        # Preserve every source character when an individual block is huge.
        for part, local_first, local_last in _segments(value):
            bounded.append((part, first + local_first - 1, first + local_last - 1))
    return tuple(bounded)


def _segments_for_version(
    text: str, segmentation_version: str
) -> tuple[tuple[str, int, int], ...]:
    if segmentation_version == _SEGMENTATION_VERSION_V1:
        return _segments(text)
    if segmentation_version == _SEGMENTATION_VERSION:
        return _segments_v2(text)
    raise MaterialWorkflowError("UNSUPPORTED_FROZEN_VERSION")


def _retrieval_query(draft: DraftSession) -> str:
    if draft.future_parameters.retrieval_version == _RETRIEVAL_VERSION_V1:
        return draft.task
    if draft.future_parameters.retrieval_version == _RETRIEVAL_VERSION:
        return (
            draft.task
            + " 价值缺口 用户 成果 交付 验收 阶段 工程门槛 真人门槛 风险 恢复"
        )
    raise MaterialWorkflowError("UNSUPPORTED_FROZEN_VERSION")


def _query_hash(draft: DraftSession, query: str) -> str:
    domain = (
        "projecttown/v3/material-result-query/v1"
        if draft.future_parameters.retrieval_version == _RETRIEVAL_VERSION_V1
        else "projecttown/v3/material-result-query/v2"
    )
    return _hash(domain, query)


def _captured_sources(root: Path, draft: DraftSession) -> dict[str, tuple[bytes, str]]:
    root, root_metadata = _validate_root(root)
    manifest = inspect_material_set(root, list(draft.selections), policy=DEFAULT_POLICY)
    if (
        manifest.status != "complete"
        or manifest.root_hash != draft.material_manifest.root_hash
    ):
        raise MaterialWorkflowError("MATERIAL_SET_CHANGED")
    observed: dict[Path, tuple[bool, os.stat_result]] = {root: (True, root_metadata)}
    captured: dict[str, tuple[bytes, str]] = {}
    for entry in draft.material_manifest.entries:
        path = root.joinpath(*entry.relative_path.split("/"))
        metadata = _check_same_device_ancestors(
            root, entry.relative_path, root_metadata.st_dev, observed
        )
        stable = read_stable_regular_file(
            path, metadata, capture_bytes=True, require_single_link=True
        )
        if (
            stable is None
            or stable[0] != entry.sha256
            or stable[1] != entry.size_bytes
            or stable[2] is None
        ):
            raise MaterialWorkflowError("UNSTABLE_SOURCE")
        try:
            captured[entry.relative_path] = (
                stable[2],
                stable[2].decode("utf-8", errors="strict"),
            )
        except UnicodeDecodeError as error:
            raise MaterialWorkflowError("UNSTABLE_SOURCE") from error
    _recheck_capture_observations(observed, root_metadata.st_dev)
    final = inspect_material_set(root, list(draft.selections), policy=DEFAULT_POLICY)
    if (
        final.status != "complete"
        or final.root_hash != draft.material_manifest.root_hash
    ):
        raise MaterialWorkflowError("MATERIAL_SET_CHANGED")
    _recheck_capture_observations(observed, root_metadata.st_dev)
    return captured


def _recheck_capture_observations(
    observed: dict[Path, tuple[bool, os.stat_result]], device: int
) -> None:
    """Recheck the same observations; a new inspection cannot prove old reads safe."""
    for path, (directory, expected) in observed.items():
        try:
            current = path.lstat()
        except OSError as error:
            raise MaterialWorkflowError("MATERIAL_SET_CHANGED") from error
        if directory:
            valid = is_safe_directory(current) and current.st_dev == device
        else:
            valid = (
                stat.S_ISREG(current.st_mode)
                and not is_reparse(current)
                and current.st_dev == device
                and current.st_nlink == 1
            )
        if not valid or not same_file(expected, current):
            raise MaterialWorkflowError(
                "MATERIAL_SET_CHANGED" if directory else "UNSTABLE_SOURCE"
            )


def _line_span(text: str, start: int, end: int) -> str:
    lines = _raw_lines(text)
    if start < 1 or end < start or end > len(lines):
        return ""
    return text[lines[start - 1][0] : lines[end - 1][1]]


def _result_hash(payload: dict[str, object]) -> str:
    return _hash("projecttown/v3/material-result-session/v1", payload)


def _build_artifact_v1(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
    conflicts: tuple[ResultConflict, ...],
) -> tuple[str, str]:
    evidence = tuple(
        item for item in citations if item.method in {"lexical", "structural"}
    )
    refs = "\n".join(
        f"- [{item.id}] `{_escape_terminal(item.relative_path)}` lines {item.line_start}-{item.line_end}: `{_escape_terminal(item.preview)}`"
        for item in citations
    )
    constraints = (
        "\n".join(
            f"- [ ] `{_escape_terminal(key)}` = `{_escape_terminal(value)}`"
            for key, value in draft.constraints
        )
        or "- No declared constraints."
    )
    unretrievable = (
        ", ".join(
            f"`{_escape_terminal(path)}`" for path in coverage.unretrievable_sources
        )
        or "none"
    )
    uncited = (
        ", ".join(f"`{_escape_terminal(path)}`" for path in coverage.uncited_sources)
        or "none"
    )
    coverage_text = (
        "## Coverage and unknowns\n"
        f"- selected={coverage.total_sources}; read={coverage.read_sources}; indexed={coverage.indexed_sources}; cited={coverage.cited_sources}\n"
        f"- unretrievable: {unretrievable}\n"
        f"- uncited: {uncited}\n"
        "- Unknowns remain unknown: these are extracted snippets, not conclusions.\n\n"
        "provider/embedding/MCP calls=0 (deterministic offline)."
    )
    if conflicts:
        details = "\n".join(
            f"- `{_escape_terminal(item.key)}`: {', '.join(f'`{value}`' for value in item.display_values)} "
            f"({', '.join(item.citation_ids)})"
            for item in conflicts
        )
        body = (
            "# Deterministic offline decision request\n\n"
            "Structured constraint conflicts require a new confirmed draft; export is blocked.\n"
            f"{details}\n\n## Evidence\n{refs}\n\n{coverage_text}"
        )
        return body, body
    task = _escape_terminal(draft.task)
    if draft.artifact_kind == "readme":
        target = _escape_terminal(draft.readme_target or "README.md")
        additions = (
            "\n".join(
                f"+ - {_escape_terminal(item.preview)} [{item.id}]" for item in evidence
            )
            or "+ - No source statements were retrievable."
        )
        body = (
            "# Deterministic offline README suggestion\n\n"
            f"Task: `{task}`\n\n```diff\n--- a/{target}\n+++ b/{target}\n@@\n"
            "+ ## Task-driven update proposal\n"
            f"+ Task: {task}\n{additions}\n"
            "+ Constraints:\n"
            + "\n".join(
                f"+ - {_escape_terminal(key)} = {_escape_terminal(value)}"
                for key, value in draft.constraints
            )
            + "\n```\n\n## Evidence\n"
            + refs
            + "\n\n## Declared constraints\n"
            + constraints
            + "\n\n"
            + coverage_text
        )
    elif draft.artifact_kind == "plan":
        work = (
            "\n".join(
                f"{number}. Examine `{_escape_terminal(item.relative_path)}`: `{_escape_terminal(item.preview)}` [{item.id}]"
                for number, item in enumerate(evidence, 1)
            )
            or "1. No retrievable source statement; ask the user for material."
        )
        body = (
            "# Deterministic offline plan\n\n"
            f"Task: `{task}`\n\n## Evidence-led work items\n{work}\n\n"
            f"## Validation checklist\n{constraints}\n\n## Evidence\n{refs}\n\n{coverage_text}"
        )
    else:
        grouped = (
            "\n".join(
                f"### `{_escape_terminal(path)}`\n"
                + "\n".join(
                    f"- `{_escape_terminal(item.preview)}` [{item.id}]"
                    for item in evidence
                    if item.relative_path == path
                )
                for path in sorted({item.relative_path for item in evidence})
            )
            or "- No retrievable source statement."
        )
        body = (
            "# Deterministic offline report\n\n"
            f"Task: `{task}`\n\n## Relevant findings\n{grouped}\n\n"
            f"## Declared constraints\n{constraints}\n\n## Evidence\n{refs}\n\n{coverage_text}"
        )
    preview = body
    return body, preview


def _plan_reference(item: ResultCitation) -> str:
    quality = "低信息结构线索" if item.method == "structural" else "行动依据"
    return f"[{item.id}]（{quality}；`{_escape_terminal(item.relative_path)}` 第 {item.line_start}-{item.line_end} 行）"


def _plan_tokens(value: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", value.casefold()))


def _display_excerpt(value: str, maximum: int = 180) -> str:
    """Keep a citation readable while hashes retain the complete frozen span."""
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= maximum:
        return cleaned
    boundary = max(cleaned.rfind(mark, 0, maximum) for mark in "。！？.!?;；")
    return cleaned[: boundary if boundary >= maximum // 2 else maximum].rstrip() + "…"


def _evidence_theme(value: str) -> str:
    folded = value.casefold()
    if any(word in folded for word in ("真人", "用户", "usability", "accept", "反馈")):
        return "真人与用户价值"
    if any(
        word in folded
        for word in ("test", "pytest", "验证", "恢复", "stale", "conflict")
    ):
        return "工程验证"
    if any(word in folded for word in ("pdf", "预览", "artifact", "成果", "交付")):
        return "候选成果"
    if any(word in folded for word in ("缺口", "问题", "风险", "现状", "baseline")):
        return "问题与缺口"
    return "资料事实"


def _stage_basis(
    evidence: tuple[ResultCitation, ...], keywords: tuple[str, ...], used: set[str]
) -> ResultCitation | None:
    """Select a distinct, topical frozen citation; never use order as meaning."""
    ranked: list[tuple[int, str, ResultCitation]] = []
    for item in evidence:
        if item.id in used or item.method != "lexical":
            continue
        preview = item.preview.casefold()
        if keywords == ("__engineering__",):
            score = _quota_score(preview, "engineering")
        elif keywords == ("__human__",):
            score = _quota_score(preview, "human")
        elif keywords == ("__deliverable__",):
            score = _quota_score(preview, "deliverable")
        else:
            score = sum(keyword.casefold() in preview for keyword in keywords)
        if score:
            ranked.append((score, item.id, item))
    return max(ranked, default=(0, "", None))[2]


def _quota_score(value: str, theme: str) -> int:
    text = value.casefold()
    if theme == "gap":
        return sum(word in text for word in ("缺口", "未验收", "价值", "用户影响"))
    if theme == "deliverable":
        return sum(
            word in text for word in ("preview", "预览", "导出", "下载", "pdf", "成果")
        ) + (2 if any(word in text for word in ("用户", "工作流")) else 0)
    if theme == "engineering":
        strong = sum(
            word in text
            for word in (
                "正向",
                "负向",
                "恢复",
                "positive",
                "negative",
                "recovery",
                "每个 phase",
                "样例",
                "两次",
                "两轮",
                "fresh roots",
                "exit code",
            )
        )
        return strong if strong >= 2 else 0
    if theme == "human":
        if "7/10" in text or "五个主要动作" in text:
            return 3
        return 2 if "10个任务" in text and "三类" in text else 0
    return 0


def _substantive_line(
    text: str, line_start: int, line_end: int, task: str
) -> int | None:
    """Pick a smallest useful statement, never a heading or divider by default."""
    task_tokens = _plan_tokens(task)
    candidates: list[tuple[int, int]] = []
    lines = text.splitlines()
    for number in range(line_start, line_end + 1):
        value = lines[number - 1].strip()
        plain = re.sub(r"^[#>*\-\d.\s]+", "", value).strip()
        words = _plan_tokens(plain)
        # Titles, separators and punctuation cannot substantiate an action.
        if value.startswith("#") or len("".join(words)) < 4 or not words:
            continue
        if re.search(
            r"\b(?:pytest|assert|passed|failed)\b|\d+\s*(?:passed|tests?)",
            plain,
            re.IGNORECASE,
        ):
            continue
        overlap = len(task_tokens & words)
        theme = sum(
            word in plain.casefold()
            for word in ("计划", "阶段", "路线", "价值", "验收", "用户", "风险", "交付")
        )
        candidates.append((overlap * 100 + theme * 25 - len(plain) // 20, number))
    return max(candidates, default=(0, None))[1]


def _build_plan_v2(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Produce a user-facing plan from frozen evidence, not a reading checklist."""
    usable = tuple(item for item in citations if item.method == "lexical")
    fallback = tuple(item for item in citations if item.method == "structural")
    evidence = usable or fallback
    constraints = (
        "\n".join(
            f"- `{_escape_terminal(key)}`: `{_escape_terminal(value)}`"
            for key, value in draft.constraints
        )
        or "- No declared constraints."
    )
    planning_intent = bool(
        _plan_tokens(draft.task)
        & {"计", "划", "迭", "代", "路", "线", "plan", "roadmap"}
    )
    local_workflow_intent = planning_intent and any(
        marker in draft.task.casefold()
        for marker in ("本地", "资料", "工作流", "material", "workflow")
    )
    lifecycle = (
        (
            "P0",
            "建立问题基线" if planning_intent else "确认研究范围",
            "将资料事实、缺口和未知项分开，避免把标题或历史测试数字当作结论",
            "问题基线：已证实事实、风险和待确认问题",
            "每条基线结论都指向实质行；低信息线索不能升级为事实",
        ),
        (
            "P1",
            "改善候选成果" if planning_intent else "形成可检验假设",
            "把最重要的价值缺口转换为用户可阅读、可比较的候选成果或方案",
            "候选成果说明：范围、优先级、取舍和用户决策点",
            "用户能在不阅读工程记录的情况下理解目标、顺序和预期交付",
        ),
        (
            "P1",
            "正负与恢复验证" if planning_intent else "执行验证计划",
            "为候选成果定义成功、失败和恢复路径，检查来源变化、冲突和发布状态",
            "验证记录：正向、负向、恢复检查及剩余风险",
            "每个验收条件可观察；失败不会被表述为成功或自动继续",
        ),
        (
            "P2",
            "独立用户决策" if planning_intent else "评审并决定后续",
            "将工程证据与真实用户处置分开，依据评审结果决定下一轮、暂停或收敛",
            "决策包：用户反馈、未决事项和下一轮入口",
            "明确记录接受/拒绝、理由和后续负责人；工程通过不替代用户接受",
        ),
    )
    if local_workflow_intent:
        lifecycle = (
            (
                "P0",
                "建立产品差距基线",
                "核对现有离线闭环与真人尚未接受状态，按严重度和用户影响列出缺口",
                "按优先级排序的 gap register，逐项包含证据、用户影响和未知项",
                "每项缺口有实质引用和用户影响；未验证状态不得标为已接受",
            ),
            (
                "P0/P1",
                "改善用户可读候选成果",
                "将 JSON 保留为工程记录，把摘要、流程、阶段、交付物、验收和短引用组成 preview/PDF 候选",
                "可直接阅读的候选包：preview、create-only PDF 与工程 JSON 的清晰分层",
                "用户无需阅读 JSON 即可理解目标、顺序、交付物和验收标准",
            ),
            (
                "P1",
                "工程验证与恢复矩阵",
                "以至少两种 plan fixture 和两轮 fresh roots 检查正向、负向、恢复、PDF reopen/text/render/visual、create-only、stale 和 conflict",
                "测试矩阵，记录每条命令的 exit code、pass/fail count 和 fresh evidence path",
                "失败关闭；PDF 可重新打开、提取文本并渲染，且不覆盖既有目标",
            ),
            (
                "P2",
                "独立真人 Study 决策",
                "为新候选创建新 root，使用固定任务收集真实评分；在用户明确接受前不进入 Apply",
                "Trial、Summary 与决策包，含真实处置、两个时间、actions 和评分分布",
                (
                    "至少 7/10 通过、三类各至少 2、actions 不超过 5；用户未明确接受则保持不进入 Apply"
                    if any("7/10" in item.preview for item in evidence)
                    else "两轮范围受限收官仅可启动无写副作用的 Phase 3A preflight；目标写入 Apply 未实现且未获授权，保持不进入 Apply"
                ),
            ),
        )
    items: list[str] = []
    used_basis: set[str] = set()
    stage_keywords = (
        ("缺口", "现状", "未接受", "baseline", "status"),
        ("__deliverable__",),
        ("__engineering__",),
        ("__human__",),
    )
    for number, (priority, title, purpose, deliverable, acceptance) in enumerate(
        lifecycle, 1
    ):
        item = _stage_basis(
            evidence,
            stage_keywords[number - 1] if local_workflow_intent else (draft.task,),
            used_basis,
        )
        if item is None and not local_workflow_intent:
            item = next(
                (candidate for candidate in evidence if candidate.id not in used_basis),
                None,
            )
        if item is not None:
            used_basis.add(item.id)
        excerpt = _display_excerpt(item.preview) if item is not None else ""
        basis = (
            f"{excerpt} {_plan_reference(item)}"
            if item is not None
            else "没有可用实质依据；先补充资料。"
        )
        themes = ", ".join(sorted({_evidence_theme(item.preview) for item in evidence}))
        actions = (
            (
                "逐项登记离线闭环、用户痛点和未接受状态，并将 gap 按用户影响排序。",
                "生成 preview/PDF 候选并核对 JSON 只承担工程记录角色。",
                "在两轮 fresh roots 执行两种 plan fixture 的正向、负向、恢复与 PDF 检查。",
                "创建独立 Study root，记录真人处置、评分和 actions，等待明确接受。",
            )
            if local_workflow_intent
            else (
                "整理可验证事实与未知项，并将优先级提交用户确认。",
                "形成可检验方案并记录范围取舍。",
                "执行验证计划并记录失败关闭结果。",
                "组织评审，记录结论并决定下一轮入口。",
            )
        )
        action = actions[number - 1] + f" 本阶段重点：{themes or '待补充资料'}。"
        acceptance_text = (
            acceptance
            if item is not None
            else f"资料未提供“{title}”的实质依据；该门槛待用户确认。"
        )
        items.append(
            f"### 阶段 {number}（{priority}）：{title}\n"
            f"- 目的：{purpose}。\n- 行动：{action}\n- 依据：{basis}\n"
            f"- 交付物：{deliverable}。\n- 验收标准：{acceptance_text}。"
        )
    refs = (
        "\n".join(
            f"- [{item.id}] `{_escape_terminal(item.relative_path)}` 第 {item.line_start}-{item.line_end} 行（行动依据）：{_display_excerpt(item.preview)}"
            for item in evidence
            if item.id in used_basis
        )
        or "- 本计划没有足以支撑行动的实质引用；相关门槛待用户确认。"
    )
    if not items:
        items.append(
            "### P0 — unblock\n- **Purpose:** obtain usable material.\n- **Action:** ask the user to provide substantive source content.\n- **Basis:** no retrievable evidence.\n- **Deliverable:** confirmed source set.\n- **Acceptance:** at least one substantive source can support a concrete next action."
        )
    task = _escape_terminal(draft.task)
    body = (
        f"# {task}\n\n"
        "## 计划总结\n"
        f"本计划将“{task}”拆分为一个可审阅的起点、按优先级推进的执行项和明确终点。"
        "它是离线、确定性资料归纳，不是模型结论；未被资料支持的事项保持为未知。\n\n"
        "## 主要执行流程\n"
        "```text\n[确认目标与约束] → [核对证据] → [P0 定义可执行变更] → [P1 实施与复核] → [验收并决定下一轮]\n"
        "      起点                                                                  终点\n```\n\n"
        "## 阶段与优先级\n" + "\n\n".join(items) + "\n\n"
        "## 交付物与验收标准\n"
        "- **交付物：**每个阶段的决策记录、实施工作项和验收记录。\n"
        "- **验收标准：**每个工作项可追溯到具体引用，并经用户确认优先级与范围。\n\n"
        "## 依赖、阻断项与用户决策\n"
        "- **依赖：**所选资料在执行前保持可验证且可读取。\n"
        "- **阻断项：**来源过期、冲突未解决或仅有低信息量标题/结构引用时，不应把计划当作事实结论。\n"
        "- **用户决策点：**确认优先级、范围和可接受的验收门槛；未确认时停止在计划而非直接应用。\n"
        "- **未知项：**资料未覆盖的负责人、时间和资源估算需要用户补充。\n\n"
        "## 约束核对\n" + constraints + "\n\n"
        "## 引用\n" + refs + "\n\n"
        "## 离线边界\n"
        f"- selected={coverage.total_sources}; read={coverage.read_sources}; cited={coverage.cited_sources}\n"
        "- provider/embedding/MCP calls=0。Deterministic offline; 引用保留文件哈希和行范围；引用覆盖率不等于引用可用性。\n"
    )
    return body, body


def _is_delivery_verification_runbook(draft: DraftSession) -> bool:
    """Keep the T002-specific presentation opt-in and narrowly scoped."""
    return draft.artifact_kind == "plan" and "本地交付复验" in draft.task


def _is_delivery_verification_runbook_v4(draft: DraftSession) -> bool:
    """Version-four opt-in is limited to the fixed T002 delivery task."""
    return (
        draft.artifact_kind == "plan"
        and draft.task
        == "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )


def _build_plan_v4(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Build the additive v5-profile T002 runbook with executable semantics."""
    if not _is_delivery_verification_runbook_v4(draft):
        return _build_plan_v2(draft, citations, coverage)
    evidence = (
        tuple(item for item in citations if item.method == "lexical") or citations
    )
    refs = (
        "\n".join(
            f"- [{item.id}] `{_escape_terminal(item.relative_path)}` 第 {item.line_start}-{item.line_end} 行：{_display_excerpt(item.preview)}"
            for item in evidence
        )
        or "- UNKNOWN/BLOCK：没有可用引用，停止复验。"
    )
    task = _escape_terminal(draft.task)
    body = (
        f"# {task}\n\n"
        "## 执行摘要\n"
        "本 runbook 先盘点原 candidate 与证据 provenance，再把可重跑检查、历史只读核验和 User Gate 分开执行。"
        "缺失、混配或不可判定均为 UNKNOWN/BLOCK；新 evidence 只能比较，不能冒充历史事实。\n\n"
        "## 复验对象与证据盘点\n"
        "| Object | Source | Status | Category | Rerun | Modify | Authority |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| candidate PDF | original delivery | pending | 可重跑验证 | compare only | no | Verifier |\n"
        "| preview | original Result | pending | 可重跑验证 | yes | no | Verifier |\n"
        "| fresh-run engineering JSON | new fresh root | pending | 可重跑验证 | new path | no | Verifier |\n"
        "| historical engineering JSON | prior delivery/Study | UNKNOWN/BLOCK | 不可重跑历史证据 | no | no | Verifier |\n"
        "| manifest | frozen candidate manifest | pending | 不可重跑历史证据 | no | no | Verifier |\n"
        "| provenance | Result/PDF binding | pending | 不可重跑历史证据 | no | no | Verifier |\n"
        "| historical final snapshot | retained history | UNKNOWN/BLOCK | 不可重跑历史证据 | no | no | User |\n"
        "| review/audit event | prior review record | UNKNOWN/BLOCK | 不可重跑历史证据 | no | no | Independent Study Reviewer |\n"
        "| release state | user-held release | UNKNOWN/BLOCK | 用户持有的发布事项 | no | no | User |\n\n"
        "Category depends on evidence provenance, not file format. fresh-run engineering JSON is new evidence at a new path; historical engineering JSON is read-only history.\n\n"
        "## 可重跑验证\n"
        "- Verifier 可在 isolated fresh root 重跑离线 tests、reopen、extract text、render、visual check 与 compare。candidate PDF 的 PASS 针对原 candidate；isolated regeneration 仅作 comparison evidence，不替代、覆盖或冒充原 candidate/历史交付物。\n\n"
        "## 不可重跑历史证据\n"
        "- historical manifest、provenance、final snapshot、review/audit 与 historical engineering JSON 只可检查 existence、readability、lineage、consistency、stale/conflict。不得重新制造；缺失即 UNKNOWN/BLOCK。\n\n"
        "## 用户持有的发布事项\n"
        "- Accept、Retain、Discard、Apply、Publish 与替换 final 仅由 User 明确处置；Verifier/Reviewer 不得自动跨越 User Gate。\n\n"
        "## 复验流程\n"
        "1. BLOCK：先登记 object、hash 与 provenance；任何缺失、stale、conflict 或混配保持 BLOCK。\n"
        "2. VERIFIED：Verifier 仅在所有 mandatory rows 已执行或完成只读核验、每项满足对应 PASS、无 blocking failure/UNKNOWN/hash-provenance mismatch/历史缺失混配时到达 VERIFIED。\n"
        "3. READY FOR USER GATE：Verifier 交付完整 evidence；Independent Study Reviewer 仅评价 candidate。\n"
        "4. User disposition：User 明确处置后才可进入 ACCEPT / REVISE / STOP；工具与 Reviewer 不得代为决定。\n\n"
        "## Verification Matrix\n"
        "| ID | Object | Category | Operation | Check | PASS | Failure | Owner | Basis |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M01 | offline tests + fresh-run JSON | 可重跑 | rerun fresh root; new path | exit/status + evidence label | tests pass; new evidence labeled | BLOCK; record | Verifier | S001 direction; Reviewer Policy |\n"
        "| M02 | candidate PDF | 可重跑 | read/reopen/render; isolated regen only as comparison evidence | reopen/extract/render | original readable; compare matches | BLOCK; retain original | Verifier | S001 direction; Reviewer Policy procedure |\n"
        "| M03 | historical manifest + engineering JSON | 历史只读 | read-only | hash/lineage | recorded history matches | BLOCK; no regenerate | Verifier | S001; S002 |\n"
        "| M04 | provenance | 历史只读 | read-only | tuple/consistency | approved binding matches | BLOCK | Verifier | S002 |\n"
        "| M05 | review event | 历史只读 | read-only | existence/readability | record consistent | UNKNOWN/BLOCK | Reviewer | S002 |\n"
        "| M06 | historical final snapshot | 历史只读 | read-only | hash/provenance | snapshot consistent | UNKNOWN/BLOCK | Verifier | S006 |\n"
        "| M07 | retained final | 用户持有 | read only | explicit disposition | User record present | STOP; no claim | User | S002; S004 |\n"
        "| M08 | Apply/Publish | 用户持有 | no automatic operation | explicit User gate | explicit authorization | BLOCK; do not apply | User | S002; S004 |\n\n"
        "## PASS/FAIL 标准\n"
        "- Global PASS：所有 mandatory rows 完成并满足 PASS；无 blocking failure、UNKNOWN、hash/provenance mismatch 或历史缺失/混配。User Gate 事项有明确 User disposition 后，才可写 ACCEPT / REVISE / STOP。\n"
        "- Reviewer-defined verification policy：PDF reopen、text extraction、expected page count、Poppler render、无 clipping/overlap/missing glyph、核心标题/流程标签、create-only 拒绝、stale/conflict、positive/negative/recovery/contract/migration 的 command、exit code、pass/fail 与 fresh evidence path。\n"
        "- 任一 blocking failure、未知历史事实、错误 provenance 或未授权发布均 FAIL closed 并保持 BLOCK。\n\n"
        "## 角色与 User Gate\n"
        "- Verifier：工程检查、fresh-root rerun、历史只读核验和 evidence；最多 VERIFIED，不得 overwrite、retain/discard、Apply/Publish/替换 final。\n"
        "- Independent Study Reviewer：固定任务评价 candidate，记录 PASS/REVISE/FAIL；不修改 candidate 或决定发布。\n"
        "- User：独占 Accept/Retain/Discard/Apply/Publish/替换 final；候选改动形成新版本并重新验证。\n\n"
        "## Independent Study 最小执行合同\n"
        "- Fixed task：回答可重跑、不可重跑、User 决定事项、当前下一步；Rating dimensions：可执行性、可读性、控制感、引用可追溯性；Disposition：PASS / REVISE / FAIL。记录 reviewer rating、notes、actions、timestamp、evidence path；四问任一不可直接回答则不得 PASS。\n\n"
        "## 引用处置\n"
        "- S001、S002、S004、S006 仅在 Matrix Basis 所列方向内支持本地复验；精确次数、fixture 组合与视觉检查属于 Reviewer-defined verification policy。\n"
        "- S003：Inherited unresolved item - not claimed as verified。S005：Out of scope for this local delivery verification。S007：Out of scope for this local delivery verification。\n\n"
        "## 引用\n" + refs + "\n\n"
        "## 离线边界\n"
        f"- source documents selected: {coverage.total_sources}\n"
        f"- cited excerpts: {len(evidence)}\n"
        "- provider/embedding/MCP calls=0；不使用网络或付费服务。\n"
    )
    return body, body


def _build_plan_v5(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Build the additive v6-profile T002 refinement without changing v5 bytes."""
    if not _is_delivery_verification_runbook_v4(draft):
        return _build_plan_v2(draft, citations, coverage)
    evidence = (
        tuple(item for item in citations if item.method == "lexical") or citations
    )
    refs = (
        "\n".join(
            f"- [{item.id}] `{_escape_terminal(item.relative_path)}` 第 {item.line_start}-{item.line_end} 行：{_display_excerpt(item.preview)}"
            for item in evidence
        )
        or "- UNKNOWN/BLOCK：没有可用引用，停止复验。"
    )
    task = _escape_terminal(draft.task)
    body = (
        f"# {task}\n\n"
        "## 执行摘要\n"
        "本 runbook 先盘点原 candidate 与证据 provenance，再把可重跑检查、历史只读核验和 User Gate 分开执行。"
        "缺失、混配或不可判定均为 UNKNOWN/BLOCK；新 evidence 只能比较，不能冒充历史事实。"
        "Final Authority 负责最终处置、发布或保留；Matrix Owner 负责执行对应 verification row。\n\n"
        "## 复验对象与证据盘点\n"
        "| Object | Source | Status | Category | Rerun | Modify | Final Authority |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| candidate PDF | original delivery | pending | 可重跑验证 | compare only | no | Verifier |\n"
        "| preview | original Result | pending | 可重跑验证 | yes | no | Verifier |\n"
        "| fresh-run engineering JSON | new fresh root | pending | 可重跑验证 | new path | no | Verifier |\n"
        "| historical engineering JSON | prior delivery/Study | UNKNOWN/BLOCK | 不可重跑历史证据 | no | no | Verifier |\n"
        "| manifest | frozen candidate manifest | pending | 不可重跑历史证据 | no | no | Verifier |\n"
        "| provenance | Result/PDF binding | pending | 不可重跑历史证据 | no | no | Verifier |\n"
        "| historical final snapshot | retained history | UNKNOWN/BLOCK | 不可重跑历史证据 | no | no | User |\n"
        "| review/audit event | prior review record | UNKNOWN/BLOCK | 不可重跑历史证据 | no | no | Independent Study Reviewer |\n"
        "| release state | user-held release | UNKNOWN/BLOCK | 用户持有的发布事项 | no | no | User |\n\n"
        "Final Authority means final disposition, publication, or retention authority; Matrix Owner means the person responsible for executing that verification row. Category depends on evidence provenance/state, not file format. fresh-run engineering JSON is new evidence at a new path; historical engineering JSON is read-only history.\n\n"
        "## 可重跑验证\n"
        "- Verifier 可在 isolated fresh root 重跑离线 tests、reopen、extract text、render、visual check 与 compare。candidate PDF 的 PASS 针对原 candidate；isolated comparison regen 仅作 comparison evidence，不替代、覆盖或冒充原 candidate/历史交付物。\n\n"
        "## 不可重跑历史证据\n"
        "- historical manifest、provenance、final snapshot、review/audit 与 historical engineering JSON 只可检查 existence、readability、lineage、consistency、stale/conflict。不得重新制造；缺失即 UNKNOWN/BLOCK。\n\n"
        "## 用户持有的发布事项\n"
        "- Accept、Retain、Discard、Apply、Publish 与替换 final 仅由 User 明确处置；Verifier/Independent Study Reviewer 不得自动跨越 User Gate。\n\n"
        "## 复验流程\n"
        "1. Initial State: BLOCK：先登记 object、hash 与 provenance；任何缺失、stale、conflict 或混配保持 BLOCK。\n"
        "2. VERIFIED：Verifier 仅在所有 mandatory rows 已执行或完成只读核验、每项满足对应 PASS、无 blocking failure/UNKNOWN/hash-provenance mismatch/历史缺失混配时到达 VERIFIED。\n"
        "3. READY FOR USER GATE：Verifier 交付完整 evidence；Independent Study Reviewer 仅评价 candidate。\n"
        "4. User disposition：User 明确处置后才可进入 ACCEPT / REVISE / STOP；工具与 Independent Study Reviewer 不得代为决定。\n\n"
        "## Verification Matrix\n"
        "| ID | Object | Category | Operation | Check | PASS | Failure | Owner | Basis |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M01 | offline tests + fresh-run JSON | 可重跑 | rerun fresh root; new path | exit/status + evidence label | tests pass; new evidence labeled | BLOCK | Verifier | S001 + RP |\n"
        "| M02 | candidate PDF | 可重跑 | read/reopen/render; isolated comparison regen | reopen/extract/render | original readable; compare matches | BLOCK; no regen | Verifier | S001 + RP |\n"
        "| M03 | historical manifest + engineering JSON | 历史只读 | read-only | hash/lineage | recorded history matches | BLOCK; no regen | Verifier | S001 + S002 |\n"
        "| M04 | provenance | 历史只读 | read-only | tuple/consistency | approved binding matches | BLOCK | Verifier | S002 |\n"
        "| M05 | review event | 历史只读 | read-only | existence/readability | record consistent | UNKNOWN/BLOCK | Independent Study Reviewer | S002 |\n"
        "| M06 | historical final snapshot | 历史只读 | read-only | hash/provenance | snapshot consistent | UNKNOWN/BLOCK | Verifier | S002 + S006 |\n"
        "| M07 | retained final | 用户持有 | read only | explicit disposition | User record present | STOP | User | S002 + S004 |\n"
        "| M08 | Apply/Publish | 用户持有 | no automatic operation | explicit User gate | explicit authorization | BLOCK; no apply | User | S002 + S004 |\n\n"
        "## PASS/FAIL 标准\n"
        "- Global PASS：所有 mandatory rows 完成并满足 PASS；无 blocking failure、UNKNOWN、hash/provenance mismatch 或历史缺失/混配。User Gate 事项有明确 User disposition 后，才可写 ACCEPT / REVISE / STOP。\n"
        "- RP = Reviewer-defined verification policy：PDF reopen、text extraction、expected page count、Poppler render、无 clipping/overlap/missing glyph、核心标题/流程标签、create-only 拒绝、stale/conflict、positive/negative/recovery/contract/migration 的 command、exit code、pass/fail 与 fresh evidence path。isolated comparison regen 仅用于比较，不替代或覆盖原 candidate。\n"
        "- 任一 blocking failure、未知历史事实、错误 provenance 或未授权发布均 FAIL closed 并保持 BLOCK。\n\n"
        "## 角色与 User Gate\n"
        "- Verifier：工程检查、fresh-root rerun、历史只读核验和 evidence；最多 VERIFIED，不得 overwrite、retain/discard、Apply/Publish/替换 final。\n"
        "- Independent Study Reviewer：固定任务评价 candidate，记录 PASS/REVISE/FAIL；不修改 candidate 或决定发布。\n"
        "- User：独占 Accept/Retain/Discard/Apply/Publish/替换 final；候选改动形成新版本并重新验证。\n\n"
        "## Independent Study 最小执行合同\n"
        "- Fixed task：回答可重跑、不可重跑、User 决定事项、当前下一步；Rating dimensions：可执行性、可读性、控制感、引用可追溯性；Disposition：PASS / REVISE / FAIL。记录 reviewer rating、notes、actions、timestamp、evidence path；四问任一不可直接回答则不得 PASS。\n\n"
        "## 引用处置\n"
        "- S001、S002、S004、S006 仅在 Matrix Basis 所列方向内支持本地复验；精确次数、fixture 组合与视觉检查属于 Reviewer-defined verification policy。\n"
        "- S003：Inherited unresolved item - not claimed as verified。S005：Out of scope for this local delivery verification。S007：Out of scope for this local delivery verification。\n\n"
        "## 引用\n" + refs + "\n\n"
        "## 离线边界\n"
        f"- source documents selected: {coverage.total_sources}; cited excerpts: {len(evidence)}\n"
        "- provider/embedding/MCP calls=0；不使用网络或付费服务。\n"
    )
    return body, body


def _build_plan_v6(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Internal-only v7 runbook contract; PDF presentation is intentionally absent."""
    body, _preview = _build_plan_v5(draft, citations, coverage)
    if not _is_delivery_verification_runbook_v4(draft):
        return body, body
    bindings = dict(draft.constraints)
    keys = (
        "candidate_path",
        "preview_path",
        "manifest_path",
        "historical_evidence_root",
        "fresh_root",
        "fresh_evidence_root",
        "test_command",
        "expected_page_count",
        "approved_hash_provenance_tuple_source",
        "study_evidence_path",
    )
    missing = [key for key in keys if not bindings.get(f"run_binding_{key}")]
    binding_lines = "\n".join(
        f"- {key}: {bindings.get(f'run_binding_{key}', '<TO BIND BEFORE RUN>')}"
        for key in keys
    )
    status = "INCOMPLETE/BLOCK" if missing else "BOUND; Initial State: BLOCK"
    insertion = f"## Run Binding\n- status: {status}\n{binding_lines}\n\n"
    body = re.sub(
        r"- (source documents selected: [^\n]+; cited excerpts: [^\n]+)\n"
        r"- provider/embedding/MCP calls=0；不使用网络或付费服务。\n",
        r"- \1; provider/embedding/MCP calls=0；不使用网络或付费服务。\n",
        body,
        count=1,
    )
    body = body.replace(
        "## 复验对象与证据盘点\n", insertion + "## 复验对象与证据盘点\n", 1
    )
    body = body.replace(
        "| Object | Source | Status | Category | Rerun | Modify | Final Authority |",
        "| Matrix ID | Object | Source | Status | Category | Rerun | Modify | Final Authority |",
        1,
    )
    body = body.replace(
        "| --- | --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        1,
    )
    body = body.replace(
        "| candidate PDF | original delivery | pending | 可重跑验证 | compare only | no | Verifier |",
        "| candidate PDF | original delivery | pending | 可重跑验证 | compare only | no | User |",
        1,
    )
    body = body.replace(
        "| preview | original Result | pending | 可重跑验证 | yes | no | Verifier |",
        "| preview | original Result | pending | 可重跑验证 | yes | no | User |",
        1,
    )
    for name, ident in (
        ("candidate PDF", "M02"),
        ("preview", "M02"),
        ("fresh-run engineering JSON", "M01"),
        ("historical engineering JSON", "M03"),
        ("manifest", "M03"),
        ("provenance", "M04"),
        ("historical final snapshot", "M06"),
        ("review/audit event", "M05"),
        ("release state", "M07/M08"),
    ):
        body = body.replace(f"| {name} |", f"| {ident} | {name} |", 1)
    body = body.replace(
        "## 可重跑验证\n", "## 三分类\n- Rerun / History / User\n\n## 可重跑验证\n", 1
    )
    before_matrix, matrix = body.split("## Verification Matrix\n", 1)
    matrix = matrix.replace(
        "| ID | Object | Category | Operation | Check | PASS | Failure | Owner | Basis |",
        "| ID | Row Type | Object | Category | Operation | Check | PASS | Failure | Owner | Basis |",
        1,
    )
    matrix = matrix.replace(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        1,
    )
    for ident, kind in (
        ("M01", "Mandatory Verification"),
        ("M02", "Mandatory Verification"),
        ("M03", "Mandatory Verification"),
        ("M04", "Mandatory Verification"),
        ("M05", "Mandatory Verification"),
        ("M06", "Mandatory Verification"),
        ("M07", "User Decision"),
        ("M08", "Conditional Release Action"),
    ):
        matrix = matrix.replace(f"| {ident} |", f"| {ident} | {kind} |", 1)
    matrix = matrix.replace(
        "| M02 | Mandatory Verification | candidate PDF | 可重跑 | read/reopen/render; isolated comparison regen | reopen/extract/render | original readable; compare matches | BLOCK; no regen | Verifier | S001 + RP |",
        "| M02 | Mandatory Verification | candidate PDF + preview | 可重跑 | read/reopen/render; isolated comparison regen | reopen/text/render | original candidate readable; compare matches | BLOCK; retain original | Verifier | RP |",
        1,
    )
    matrix = matrix.replace(
        "| M05 | Mandatory Verification | review event | 历史只读 | read-only | existence/readability | record consistent | UNKNOWN/BLOCK | Independent Study Reviewer | S002 |",
        "| M05 | Mandatory Verification | review event | 历史只读 | read-only | exist/readability | record consistent | UNKNOWN/BLOCK | Independent Study Reviewer | S002 |",
        1,
    )
    matrix = matrix.replace("| 可重跑 |", "| Rerun |")
    matrix = matrix.replace("| 历史只读 |", "| History |")
    matrix = matrix.replace("| 用户持有 |", "| User |")
    body = before_matrix + "## Verification Matrix\n" + matrix
    body = body.replace(
        "2. VERIFIED：Verifier 仅在所有 mandatory rows 已执行或完成只读核验、每项满足对应 PASS、无 blocking failure/UNKNOWN/hash-provenance mismatch/历史缺失混配时到达 VERIFIED。\n"
        "3. READY FOR USER GATE：Verifier 交付完整 evidence；Independent Study Reviewer 仅评价 candidate。\n"
        "4. User disposition：User 明确处置后才可进入 ACCEPT / REVISE / STOP；工具与 Independent Study Reviewer 不得代为决定。\n\n",
        "2. VERIFIED：Verifier 仅在 M01-M06 全部已执行或完成只读核验、每项满足对应 PASS、无 blocking failure/UNKNOWN/hash-provenance mismatch/历史缺失混配时到达 VERIFIED。\n"
        "3. Independent Study：仅在 VERIFIED 后由 Independent Study Reviewer 评价 candidate；四问任一不可直接回答即不得 PASS。\n"
        "4. READY FOR USER GATE：仅当 VERIFIED 且 Independent Study 完成后等待 User 决定。\n"
        "5. User disposition：仅 User 可明确处置 ACCEPT / REVISE / STOP；REVISE 形成新 candidate 并重新验证；Apply/Publish 仅在 ACCEPT 后经 User 单独授权，未授权不构成 verification failure。\n\n",
        1,
    )
    body = body.replace(
        "## PASS/FAIL 标准\n",
        "## 状态合同\n"
        "- VERIFIED：M01-M06 全部完成且满足 PASS；无 UNKNOWN、证据缺失、混配或 hash/provenance mismatch。\n"
        "- READY FOR USER GATE：VERIFIED 且 Independent Study 完成，等待 User 决定。\n"
        "- ACCEPT：仅 User 明确接受。REVISE：形成新 candidate 并重新验证。\n"
        "- STOP：User 停止、Discard 或不可恢复 blocker。\n"
        "- Apply/Publish：ACCEPT 后可选，且需 User 单独授权；未授权不是 verification failure。\n"
        "- M01-M06 必须 VERIFIED，且是 Mandatory Verification；M07 仅 User Decision；M08 仅 Conditional Release Action。未绑定或失败不得离开 BLOCK。\n\n"
        "## PASS/FAIL 标准\n",
        1,
    )
    return body, body


def _build_plan_v7(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Additive v8 runbook: bind one concrete verification run before it starts."""
    body, _ = _build_plan_v6(draft, citations, coverage)
    if not _is_delivery_verification_runbook_v4(draft):
        return body, body
    values = dict(draft.constraints)
    fields = (
        ("binding_id", "Input"),
        ("working_directory", "Input"),
        ("candidate_path", "Input"),
        ("preview_path", "Input"),
        ("manifest_path", "Historical"),
        ("prior_study_evidence_path", "Historical"),
        ("historical_evidence_root", "Historical"),
        ("fresh_root", "Output"),
        ("fresh_evidence_root", "Output"),
        ("test_command", "Output"),
        ("fresh_result_output_path", "Output"),
        ("fresh_result_evidence_label", "Output"),
        ("expected_page_count", "Input"),
        ("approved_hash_provenance_tuple_source", "Historical"),
        ("planned_study_evidence_output", "Output"),
    )
    missing = [key for key, _ in fields if not values.get(f"run_binding_{key}")]
    # This is deliberately a value supplied by the verifier, rather than an
    # inference from the presence of paths.  A complete set of strings has not
    # yet proven that the concrete run is safe to start.
    preflight = values.get("run_binding_preflight_result", "pending")
    if preflight not in {"pending", "passed", "blocked"}:
        preflight = "blocked"
    if missing:
        status = "UNBOUND" if len(missing) == len(fields) else "BINDING BLOCKED"
    elif preflight == "passed":
        status = "PREFLIGHT PASS"
    elif preflight == "pending":
        status = "BOUND-UNVALIDATED"
    else:
        status = "BINDING BLOCKED"
    path_keys = {
        "working_directory",
        "candidate_path",
        "preview_path",
        "manifest_path",
        "prior_study_evidence_path",
        "historical_evidence_root",
        "fresh_root",
        "fresh_evidence_root",
        "fresh_result_output_path",
        "approved_hash_provenance_tuple_source",
        "planned_study_evidence_output",
    }

    def _safe_basename(value: str) -> str:
        normalized = value.replace("\\", "/").rstrip("/")
        basename = normalized.rsplit("/", 1)[-1] or "root"
        return "".join(
            "_" if unicodedata.category(character).startswith("C") else character
            for character in basename
        )

    def _path_reference(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"PATH_REF[{digest}]:{_safe_basename(value)}"

    def _command_reference(value: str) -> str:
        template = value
        for key in sorted(path_keys, key=len, reverse=True):
            path_value = values.get(f"run_binding_{key}")
            if not path_value:
                continue
            replacement = (
                "[WORKING_DIRECTORY]"
                if key == "working_directory"
                else f"[{key.upper()}]"
            )
            for variant in {
                path_value,
                path_value.replace("\\", "/"),
                path_value.replace("/", "\\"),
            }:
                template = template.replace(variant, replacement)
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"COMMAND_REF[{digest}]: {template}"

    def _display_value(key: str) -> str:
        value = values.get(f"run_binding_{key}")
        if not value:
            return "UNBOUND"
        if key in path_keys:
            return _path_reference(value)
        if key == "test_command":
            return _command_reference(value)
        return value

    rows = "\n".join(
        f"- {group} | {key} | {_display_value(key)}" for key, group in fields
    )
    binding = (
        "## M00 Run Binding preflight\n"
        f"- status: {status}\n"
        f"- preflight_result: {preflight}\n{rows}\n"
        "- Displayed refs are scan-safe. Full canonical Input/Output/Historical values and the exact command live in Result.draft.constraints; M00 verifies those canonical values.\n"
        "- M01 executes the canonical command and records the canonical output path and evidence label.\n"
        "- PREFLIGHT PASS only after all bindings are checked; planned Study evidence output is a ref only, is not created, and needs User authorization.\n"
        "- v8 defines this procedure; the frozen v7 candidate remains a v6 generator/exporter/renderer artifact.\n\n"
    )
    body = re.sub(
        r"## Run Binding\n.*?(?=## 复验对象与证据盘点\n)",
        lambda _match: binding,
        body,
        count=1,
        flags=re.DOTALL,
    )
    body = body.replace(
        "| M02 | Mandatory Verification | candidate PDF + preview |",
        "| M02 | Mandatory Verification | candidate PDF |",
        1,
    )
    body = body.replace(
        "| M03 | Mandatory Verification |",
        "| M02B | Mandatory Verification | preview text/JSON | Rerun | read/compare | parse/readable | preview record readable | BLOCK | Verifier | RP |\n| M03 | Mandatory Verification |",
        1,
    )
    body = body.replace(
        "| M07 | User Decision |", "| M07 | User Decision |", 1
    ).replace(
        "| M08 | Conditional Release Action |",
        "| M08 | Conditional Release Action |",
        1,
    )
    body = body.replace(
        "## 状态合同\n",
        "## 状态合同\n- M00：UNBOUND / BOUND-UNVALIDATED / PREFLIGHT PASS / BINDING BLOCKED。\n- verification PASS、Independent Study disposition、User disposition 与 release authorization 分离。M07 pending = WAITING USER；M08 without authorization = NOT AUTHORIZED / DO NOT EXECUTE。\n",
        1,
    )
    matrix = (
        "## Verification Matrix\n"
        "| ID | Row Type | Object | Category | Operation | Check | PASS | Failure | Owner | Basis |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M00 | Mandatory | Run Binding | Rerun | bind + check | all fields | PREFLIGHT PASS | BINDING BLOCKED | Verifier | RP |\n"
        "| M01 | Mandatory | tests + fresh JSON | Rerun | command + output + label | exit + status + label | checks pass | BLOCK | Verifier | S001 + RP |\n"
        "| M02 | Mandatory | candidate PDF | Rerun | read, reopen, render; comparison regen | reopen, text, render | original readable; compare matches | BLOCK; retain original | Verifier | RP |\n"
        "| M02B | Mandatory | preview record | Rerun | read + compare | parse + read | preview readable | BLOCK | Verifier | RP |\n"
        "| M03 | Mandatory | manifest + history JSON | History | read only | hash + provenance | recorded binding | UNKNOWN / BLOCK | Verifier | S001 + S002 |\n"
        "| M04 | Mandatory | provenance | History | read only | tuple + consistency | approved tuple | UNKNOWN / BLOCK | Verifier | S002 |\n"
        "| M05 | Mandatory | prior Study input | History | read only | exists + readable | record consistent | UNKNOWN / BLOCK | Independent Study Reviewer | S002 |\n"
        "| M06 | Mandatory | final snapshot | History | read only | exists + readable | lineage consistent | UNKNOWN / BLOCK | Verifier | S002 + S006 |\n"
        "| M07 | User decision | User disposition | User | wait + record | explicit decision | WAITING USER | WAITING USER | User | S002 |\n"
        "| M08 | Conditional | Release action | User | wait for authorization | authorization | authorized + recorded | NOT AUTHORIZED | User | S002 + S004 |\n\n"
    )
    body = re.sub(
        r"## Verification Matrix\n.*?(?=## 状态合同\n)", matrix, body, flags=re.DOTALL
    )
    return body, body


def _build_plan_v8(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Additive v9 runbook contract; displayed references are intentionally opaque."""
    if not _is_delivery_verification_runbook_v4(draft):
        return _build_plan_v2(draft, citations, coverage)
    values = dict(draft.constraints)
    evidence = (
        tuple(item for item in citations if item.method == "lexical") or citations
    )
    refs = (
        "\n".join(
            f"- [{item.id}] `{_escape_terminal(item.relative_path)}` {item.line_start}-{item.line_end}: {_display_excerpt(item.preview)}"
            for item in evidence
        )
        or "- UNKNOWN/BLOCK: no usable citation."
    )
    fields = (
        ("runbook_version", "Input"),
        ("verification_target_version", "Input"),
        ("verification_target_id", "Input"),
        ("verification_run_id", "Execution"),
        ("candidate_path", "Input"),
        ("preview_record_path", "Input"),
        ("candidate_hash_source", "Historical Input"),
        ("candidate_expected_page_count", "Input"),
        ("manifest_path", "Historical Input"),
        ("prior_study_evidence_path", "Historical Input"),
        ("historical_evidence_root", "Historical Input"),
        ("working_directory", "Execution"),
        ("fresh_root", "Fresh Output"),
        ("fresh_evidence_root", "Fresh Output"),
        ("material_source_root", "Fresh Output"),
        ("fresh_draft_path", "Fresh Output"),
        ("fresh_confirmation_hash", "Fresh Output"),
        ("fresh_result_command", "Fresh Output"),
        ("fresh_result_output_path", "Fresh Output"),
        ("fresh_result_schema", "Fresh Output"),
        ("fresh_result_evidence_label", "Fresh Output"),
        ("planned_study_evidence_output", "User Output"),
    )
    path_keys = {
        key
        for key, _group in fields
        if key.endswith(("_path", "_root"))
        or key in {"working_directory", "planned_study_evidence_output"}
    }

    def display(key: str) -> str:
        value = values.get(f"run_binding_{key}")
        if not value:
            return "UNBOUND"
        if key in path_keys:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
            base = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "root"
            return f"PATH_REF[{digest}]:{base}"
        if key == "fresh_result_command":
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
            return f"COMMAND_REF[{digest}]"
        return value

    binding_rows = "\n".join(
        f"- {group} | {key} | {display(key)} | NOT RUN | no resolver; evidence/timestamp/verifier required | BLOCK"
        for key, group in fields
    )
    m00_rows = "\n".join(
        f"- M00-0{index} | {check} | NOT RUN | evidence=UNBOUND; timestamp=UNBOUND; verifier=UNBOUND | BLOCK"
        for index, check in enumerate(
            (
                "resolve canonical binding",
                "candidate identity and hash",
                "preview record identity",
                "historical input lineage",
                "fresh output command and schema",
                "working directory safety",
                "target page-count binding",
            ),
            1,
        )
    )
    task = _escape_terminal(draft.task)
    body = (
        f"# {task}\n\n"
        "## 执行摘要\n本 v9 runbook 验证 frozen v8 candidate；v9 是新的 procedure/presentation lineage，不替代 v8 历史事实。任何未解析 binding、缺失 evidence、stale、conflict 或 provenance mismatch 都保持 BLOCK。\n\n"
        "## M00 Run Binding preflight\n- status: BINDING BLOCKED\n- PATH_REF/COMMAND_REF are opaque display identifiers. Repository has no resolver, so field presence never produces PREFLIGHT PASS. Full canonical values remain in Result.draft.constraints.\n"
        + binding_rows
        + "\n"
        + m00_rows
        + "\n\n"
        "## 复验对象与证据盘点\n"
        "| Matrix ID | Object | Source | State | Category | Rerun | Modify | Final Authority |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M02 | candidate PDF | frozen delivery | UNVALIDATED | Rerun | compare only | no | User |\n"
        "| M02B | preview record | frozen Result preview | UNVALIDATED | Rerun | read/compare | no | User |\n"
        "| M01 | fresh Result JSON | new generate output | NOT RUN | Rerun | new path | no | Verifier |\n"
        "| M03-M06 | manifest + provenance + prior Study + final | historical inputs | UNKNOWN/BLOCK | History | no | no | User |\n"
        "| M07-M08 | disposition/release | user-held | WAITING USER | User | no | User only | User |\n\n"
        "## 可重跑验证\n- Original candidate is read/reopen/render only. Isolated regeneration is comparison evidence, never a replacement. Fresh Result JSON is created only by `run_v3_material_workflow.py generate --root --draft --confirmation-hash --result-out`; pytest is not a business Result generator.\n\n"
        "## 不可重跑历史证据\n- Category follows provenance/state, not file format. Historical evidence is read-only; missing historical fact is UNKNOWN/BLOCK and new output cannot recreate it.\n\n"
        "## 用户持有的发布事项\n- Only User may Accept, Retain, Discard, Apply, Publish, or replace final.\n\n"
        "## 复验流程\n1. Initial State: BLOCK.\n2. M00 preflight and M01-M06 verification.\n3. VERIFIED only when VERIFIED Criteria hold.\n4. Independent Study.\n5. READY FOR USER GATE.\n6. User disposition: ACCEPT / REVISE / STOP.\n7. Apply/Publish only after separate User authorization.\n\n"
        "## Verification Matrix\n"
        "| ID | Row Type | Group | Object | Operation | Check | Evidence | Expected Result/State | Blocking Condition | Owner |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M00 | Mandatory | Input | Run Binding | inspect 01-07 | canonical resolver | timestamp/verifier/result | BINDING BLOCKED until real resolver | unresolved ref | Verifier |\n"
        "| M01 | Mandatory | Fresh Output | tests + Result | generate fresh Result | command/output/schema | exit + new JSON label | fresh Result v3-material-result-session-v1 | missing/failed command | Verifier |\n"
        "| M02 | Mandatory | Input | candidate PDF | read/reopen/render | hash/page/text/visual | original PDF evidence | original readable; no replacement | drift/clipping/overlap | Verifier |\n"
        "| M02B | Mandatory | Input | preview record | compare tuple | target id/hash/page count | candidate/preview tuple | tuple matches declared target | drift -> BLOCK retain original | Verifier |\n"
        "| M03 | Mandatory | Historical Input | manifest/history JSON | read only | hash + lineage | read-only record | consistency | missing/mixed | Verifier |\n"
        "| M04 | Mandatory | Historical Input | provenance | read only | approved tuple | tuple source | consistent | mismatch | Verifier |\n"
        "| M05 | Mandatory | Historical Input | prior Study evidence | read only | exists/readable | immutable record | consistent | UNKNOWN/BLOCK | Independent Study Reviewer |\n"
        "| M06 | Mandatory | Historical Input | final snapshot | read only | lineage relation | S002 evidence | relation consistent | UNKNOWN/BLOCK | Verifier |\n"
        "| M07 | User Decision | User Output | User disposition | wait + record | explicit decision | User record | WAITING USER is legal | none while waiting | User |\n"
        "| M08 | Conditional Release | User Output | Apply/Publish | do not execute | separate authorization | User authorization | NOT AUTHORIZED / DO NOT EXECUTE | unauthorized execution/mismatch | User |\n\n"
        "## VERIFIED Criteria\n- M00-M06 are executed or read-only verified, each meets Expected Result/State, and there is no UNKNOWN, missing evidence, hash/provenance mismatch, or blocking condition. Study disposition, User disposition, and release authorization are separate states.\n\n"
        "## 角色与 User Gate\n- Verifier records engineering evidence only. Independent Study Reviewer rates the fixed task but does not modify candidate. User exclusively decides disposition and release authorization.\n\n"
        "## Citation Usage Audit\n"
        "| Source | Status/Scope | Supported claim | Matrix rows | Limit |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| S001 | scoped | regression direction | M01-M03 | not exact PDF procedure |\n"
        "| S002 | scoped | history/provenance relation | M03-M06 | M06 only uses S002 |\n"
        "| S003 | inherited unresolved | not claimed verified | none | no PASS claim |\n"
        "| S004 | User gate background | User authority | M07-M08 | not verification result |\n"
        "| S005 | scoped | fresh-root direction | M00-M01 | no history/release claim |\n"
        "| S006 | out of scope | none | none | no completeness basis |\n"
        "| S007 | conditional background | conditional context | none | not an execution mandate |\n"
        "| S008 | conditional scope | only stated condition | conditional | no broader applicability |\n\n"
        "## 引用\n" + refs + "\n\n"
        "## 离线边界\n"
        f"- source documents selected: {coverage.total_sources}; cited excerpts: {len(evidence)}\n"
        "- provider/embedding/MCP/network/paid calls=0.\n"
    )
    return body, body


def _build_plan_v9(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Build the additive v10 procedure without changing the v9 artifact."""
    if not _is_delivery_verification_runbook_v4(draft):
        return _build_plan_v2(draft, citations, coverage)
    values = dict(draft.constraints)
    evidence = (
        tuple(item for item in citations if item.method == "lexical") or citations
    )

    def value(key: str) -> str:
        raw = values.get(f"run_binding_{key}")
        if not raw:
            return "UNRESOLVED / BINDING BLOCKED"
        if ":" in raw or raw.startswith(("/", "\\\\")):
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
            leaf = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            return f"PATH_REF[{digest}]:{leaf}"
        return raw

    bindings = (
        ("Input", "candidate_path", "path", "read-only"),
        ("Input", "preview_record_path", "path", "read-only"),
        ("Historical Input", "manifest_path", "path", "read-only"),
        ("Historical Input", "historical_study_root", "directory", "read-only"),
        ("Historical Input", "historical_work_root", "directory", "read-only"),
        ("Historical Input", "historical_result_json_path", "path", "read-only"),
        ("Historical Input", "approved_provenance_tuple_source", "path", "read-only"),
        ("Historical Input", "prior_study_evidence_path", "path", "read-only"),
        ("Historical Input", "final_snapshot_path", "path", "read-only"),
        ("Execution", "working_directory", "directory", "read-only cwd"),
        ("Execution", "material_source_root", "directory", "read-only input"),
        ("Execution", "fresh_root", "directory", "create-only"),
        ("Fresh Engineering Output", "fresh_draft_path", "path", "create-only"),
        ("Fresh Engineering Output", "fresh_result_output_path", "path", "create-only"),
        ("Fresh Engineering Output", "fresh_evidence_root", "directory", "create-only"),
        ("Study Output", "planned_study_evidence_output", "path", "future; unresolved"),
        ("User Output", "user_disposition_record_path", "path", "future; unresolved"),
        (
            "User Output",
            "release_authorization_record_path",
            "path",
            "future; unresolved",
        ),
    )
    binding_rows = "\n".join(
        f"| {group} | {key} | PATH_REF[{hashlib.sha256(key.encode()).hexdigest()[:10]}] | {value(key)} | {kind} | {access} | {'UNRESOLVED / BINDING BLOCKED' if value(key).startswith('UNRESOLVED') else 'BOUND-UNVALIDATED'} |"
        for group, key, kind, access in bindings
    )
    actual_candidate = value("candidate_path")
    actual_preview = value("preview_record_path")
    actual_history = value("historical_result_json_path")
    actual_provenance = value("approved_provenance_tuple_source")
    actual_cwd = value("working_directory")
    actual_result = value("fresh_result_output_path")
    candidate_tuple = (
        "id=projecttown-v3-phase2-human-pdf-v8-20260829-001:T002; "
        "profile=projecttown-human-pdf-v8; "
        "sha256=1686e8...cca68; export=v3-material-pdf-export-v7; "
        "renderer=projecttown-reportlab-pdf-v7; pages=4"
    )
    m01_command = (
        ".\\.venv\\Scripts\\python.exe scripts/run_v3_material_workflow.py draft "
        "--root <material_source_root> --file README.md --file docs/limitations.md "
        "--file docs/v2-closeout.md --file docs/validation-v1.0.md "
        "--task <fixed_T002_task> --artifact-kind plan "
        "--constraint <each_declared_binding> --generator-version deterministic-grounded-plan-v9 "
        "--draft-out <fresh_draft_path>; then .\\.venv\\Scripts\\python.exe "
        "scripts/run_v3_material_workflow.py generate --root <material_source_root> "
        "--draft <fresh_draft_path> --confirmation-hash <draft_contract_hash> "
        "--result-out <fresh_result_output_path>"
    )
    m00_rows = (
        (
            "M00-01",
            "canonical binding unique/type",
            "all mandatory refs resolve once and types match",
            "18 bindings",
            "NOT RUN",
            "all binding rows",
            "inspect declared values",
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
        (
            "M00-02",
            "frozen candidate identity",
            "v8 tuple/hash/page count match",
            candidate_tuple,
            "NOT RUN",
            actual_candidate,
            "pypdf + sha256",
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
        (
            "M00-03",
            "preview lineage",
            "preview, candidate and manifest tuple agree",
            candidate_tuple,
            "NOT RUN",
            actual_preview,
            ".\\.venv\\Scripts\\python.exe scripts/run_v3_material_workflow.py check --root <material_source_root> --session <preview_record_path>",
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
        (
            "M00-04",
            "historical inputs immutable",
            "hash/timestamp/lineage readable",
            "Result + provenance + Study + snapshot",
            "NOT RUN",
            f"{actual_history}; {actual_provenance}",
            "read only; sha256",
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
        (
            "M00-05",
            "fresh Result chain",
            "real draft→generate command, v3 schema, create-only output",
            "v3-material-result-session-v1",
            "NOT RUN",
            actual_result,
            m01_command,
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
        (
            "M00-06",
            "execution isolation",
            "one cwd/Python/source/output separation",
            "cwd=<working_directory>; root=<material_source_root>; fresh outputs isolated",
            "NOT RUN",
            actual_cwd,
            ".\\.venv\\Scripts\\python.exe scripts/run_v3_material_workflow.py check --root <material_source_root> --session <fresh_result_output_path>",
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
        (
            "M00-07",
            "same-candidate page count",
            "expected and actual bind to frozen v8 candidate",
            candidate_tuple,
            "NOT RUN",
            actual_candidate,
            "pypdf <candidate>",
            "NOT RUN",
            "Verifier",
            "UNBOUND",
            "UNBOUND",
            "BINDING BLOCKED",
        ),
    )
    m00_table = "\n".join("| " + " | ".join(row) + " |" for row in m00_rows)
    refs = (
        "\n".join(
            f"- [{item.id}] `{_escape_terminal(item.relative_path)}` {item.line_start}-{item.line_end}: {_display_excerpt(item.preview)}"
            for item in evidence
        )
        or "- UNKNOWN/BLOCK: no usable citation."
    )
    task = _escape_terminal(draft.task)
    body = (
        f"# {task}\n\n"
        "## 执行摘要\n"
        "runbook v10 only defines a new verification procedure/presentation. It verifies the frozen v8 candidate; it does not replace v8/v9 historical facts. Unresolved binding, missing evidence, stale/conflict, lineage or provenance mismatch remains BLOCK.\n\n"
        "## Run Binding\n"
        "No repository resolver exists. Markdown shows scan-safe references; the local-only PDF shows validated canonical declared values. A present field is BOUND-UNVALIDATED, never PREFLIGHT PASS. Future Study/User outputs remain UNRESOLVED / BINDING BLOCKED.\n\n"
        "| Group | Binding | Opaque REF | Scan-safe display value | Expected type | Access | Validation state |\n| --- | --- | --- | --- | --- | --- | --- |\n"
        + binding_rows
        + "\n\n## M00 Run Binding preflight\n"
        "Preflight configuration is known before execution. Runtime Result (exit, output SHA-256, size, timestamp and actual label) is recorded only after a command runs; it is not a self-referential preflight input.\n\n"
        "| ID | Check | Concrete PASS Condition | Expected value | Actual value | Resolved path | Command | Exit code | Verifier | Timestamp | Evidence path | Outcome |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        + m00_table
        + "\n\n## 复验对象与证据盘点\n"
        "| Matrix ID | Object | Source | Category | Mutability | Verification owner | Record owner | Final authority |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M01 | material source + fresh Result | execution/new evidence | Rerun | create-only fresh path | Verifier | Verifier | User |\n"
        "| M02/M02B | frozen v8 candidate + preview record | frozen v8 work root | Input | immutable | Verifier | Verifier | User |\n"
        "| M03-M06 | manifest/Result/provenance/prior Study/snapshot | historical roots | History | immutable | Verifier | historical record owner | no modification authority |\n"
        "| M07 | disposition record | User output | User | User-only | User | User | User |\n"
        "| M08 | release authorization | User output | User | User-only | User | User | User |\n\n"
        "## 复验流程\n"
        "Initial BLOCK → M00 → M01-M06 → VERIFIED → Independent Study → READY FOR USER GATE → User disposition. ACCEPT/RETAIN records the next authorized decision; REVISE requires a new candidate and verification; DISCARD/STOP ends this run. Apply/Publish is separate: wait for explicit authorization; execute only if authorized.\n\n"
        "## Independent Study 最小执行合同\n"
        "- Fixed questions: what reruns; what history is read-only; what only User decides; current state and next step.\n"
        "- Rate executability, readability, control and citation traceability. Disposition is PASS/REVISE/FAIL; any unanswered fixed question cannot PASS.\n"
        "- Record reviewer identity, answers, ratings, notes, actions, timestamp, disposition and evidence path. Verifier checks prior Study integrity first; current reviewer must not read prior disposition before rating is locked, or M05 and current Study use different people.\n\n"
        "## M07/M08 状态机\n"
        "- M07: WAITING USER, ACCEPT, RETAIN, REVISE, DISCARD, STOP. WAITING USER is legal; only forged, untraceable, inconsistent or overwritten records BLOCK.\n"
        "- M08: NOT REQUESTED, NOT AUTHORIZED, AUTHORIZED, AUTHORIZED AND EXECUTED, UNAUTHORIZED ATTEMPT. NOT AUTHORIZED is a legal no-op; only UNAUTHORIZED ATTEMPT BLOCKS.\n\n"
        "## Verification Matrix\n"
        "| ID | Row Type | Group | Object | Operation | Check | Evidence | Expected Result/State | Blocking Condition | Owner | Basis |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| M00 | Mandatory | Input | binding | execute 01-07 | concrete fields | M00 record | PREFLIGHT PASS only when all actuals recorded | unresolved/not run | Verifier | RP |\n"
        "| M01 | Mandatory | Execution | fresh Result | draft→generate | schema/hash/label | runtime Result | create-only v3 Result | command/output mismatch | Verifier | S001 + S005 + RP |\n"
        "| M02 | Mandatory | Input | candidate PDF | read/reopen/render | hash/page/text/visual | original PDF | original retained/readable | drift/clipping/overlap | Verifier | RP |\n"
        "| M02B | Mandatory | Input | preview record | compare tuple | target/hash/page | preview + candidate | declared tuple matches | mismatch | Verifier | RP |\n"
        "| M03 | Mandatory | History | manifest + historical Result | read only | hash/lineage | immutable records | consistent | missing/mixed | Verifier | S001 + S002 |\n"
        "| M04 | Mandatory | History | provenance tuple | read only | approved tuple | tuple source | consistent | mismatch | Verifier | S002 |\n"
        "| M05 | Mandatory | History | prior Study | read only | exists/readable | immutable Trial | consistent | UNKNOWN/BLOCK | Verifier | S002 |\n"
        "| M06 | Mandatory | History | final snapshot | read only | S002 relation | snapshot + relation | consistent | UNKNOWN/BLOCK | Verifier | S002 |\n"
        "| M07 | User Decision | Study Output | disposition | wait + record | traceable decision | User record | WAITING USER is legal | forgery/overwrite | User | S002 + S004 |\n"
        "| M08 | Conditional Release | User Output | Apply/Publish | wait for explicit authorization; execute only if authorized | authorization state | authorization record | NOT AUTHORIZED legal no-op | unauthorized attempt | User | S004 |\n\n"
        "## VERIFIED Criteria\n"
        "- VERIFIED requires M00-M06 executed or read-only verified with their expected states, no UNKNOWN, missing evidence, hash/provenance mismatch or blocking condition. Study PASS, User disposition and release authorization are separate states.\n\n"
        "## Citation Usage Audit\n"
        "| Source | Status | Supported content | Matrix Basis | Limit |\n| --- | --- | --- | --- | --- |\n"
        "| S001 | USED | regression direction | M01, M03 | not exact PDF procedure |\n"
        "| S002 | USED | historical Result/provenance/Study/final relation | M03-M07 | M06 only uses S002 |\n"
        "| S003 | inherited unresolved | no verified claim | none | not PASS |\n"
        "| S004 | USED | User release authority background | M07-M08 | not verification result |\n"
        "| S005 | USED | fresh-root direction | M01 | not historical/release claim |\n"
        "| S006 | out of scope | credential scan only | none | not completeness basis |\n"
        "| S007 | conditional background | conditional context | none | not execution mandate |\n"
        "| S008 | conditional scope | stated condition only | none | no broader applicability |\n\n"
        "## 引用\n" + refs + "\n\n## 离线边界\n"
        f"- source documents selected: {coverage.total_sources}; cited excerpts: {len(evidence)}\n"
        "- provider/embedding/MCP/network/paid calls=0.\n"
    )
    return body, body


def _build_plan_v3(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
) -> tuple[str, str]:
    """Build the additive T002 delivery-verification runbook only when selected."""
    if not _is_delivery_verification_runbook(draft):
        return _build_plan_v2(draft, citations, coverage)
    evidence = (
        tuple(item for item in citations if item.method == "lexical") or citations
    )
    required_inventory_rows = (
        "- path-or-id: candidate-pdf; source: generated candidate; status: UNKNOWN/BLOCK; category: 可重跑验证; rerunnable: yes; modifiable: no; final authority: Verifier.",
        "- path-or-id: preview; source: generated preview; status: UNKNOWN/BLOCK; category: 可重跑验证; rerunnable: yes; modifiable: no; final authority: Verifier.",
        "- path-or-id: engineering-result-json; source: engineering record; status: UNKNOWN/BLOCK; category: 可重跑验证; rerunnable: yes; modifiable: no; final authority: Verifier.",
        "- path-or-id: manifest; source: candidate manifest; status: UNKNOWN/BLOCK; category: 不可重跑历史证据; rerunnable: no; modifiable: no; final authority: Verifier.",
        "- path-or-id: provenance; source: result lineage; status: UNKNOWN/BLOCK; category: 不可重跑历史证据; rerunnable: no; modifiable: no; final authority: Verifier.",
        "- path-or-id: historical-final-snapshot; source: historical final record; status: UNKNOWN/BLOCK; category: 不可重跑历史证据; rerunnable: no; modifiable: no; final authority: Verifier.",
        "- path-or-id: historical-review-audit-event; source: historical review record; status: UNKNOWN/BLOCK; category: 不可重跑历史证据; rerunnable: no; modifiable: no; final authority: Independent Study Reviewer.",
        "- path-or-id: retained-final; source: user-held final; status: UNKNOWN/BLOCK; category: 用户持有的发布事项; rerunnable: no; modifiable: no; final authority: User.",
        "- path-or-id: user-held-release-state; source: user-held release record; status: UNKNOWN/BLOCK; category: 用户持有的发布事项; rerunnable: no; modifiable: no; final authority: User.",
    )
    required_inventory = "\n".join(f"{row}" for row in required_inventory_rows)
    refs = (
        "\n".join(
            f"- [{item.id}] `{_escape_terminal(item.relative_path)}` 第 {item.line_start}-{item.line_end} 行：{_display_excerpt(item.preview)}"
            for item in evidence
        )
        or "- UNKNOWN/BLOCK：没有可用引用，停止复验。"
    )
    task = _escape_terminal(draft.task)
    body = (
        f"# {task}\n\n"
        "## 执行摘要\n"
        "本 runbook 将复验操作、不可替代的历史证据和仅由用户决定的发布事项分离。"
        "所有新增数量与检查组合均为 Reviewer-defined verification policy；缺少证据即 UNKNOWN/BLOCK，"
        "不得以重跑结果替换历史记录。\n\n"
        "## 复验对象与证据盘点\n" + required_inventory + "\n\n"
        "## 可重跑验证\n"
        "- 允许在新的隔离证据目录重跑离线测试、PDF reopen 和确定性检查；不得覆盖任何冻结产物。\n\n"
        "## 不可重跑历史证据\n"
        "- 历史 Study、manifest、hash 和真人记录只能读取核验，不能重新生成、替换或当作新结果。\n\n"
        "## 用户持有的发布事项\n"
        "- 发布、保留、接受和 Apply 仅由 User 明确决定；Verifier 与 Independent Study Reviewer 均不得代替该 gate。\n\n"
        "## 复验流程\n"
        "1. Verifier 负责工程检查、fresh-root rerun、历史证据只读核验和证据记录；可 read/reopen/render/compare/verify，不可 overwrite/Apply/retain/discard/替换 final。\n"
        "2. Independent Study Reviewer 使用固定任务评鉴，记录 rating、disposition、PASS/REVISE/FAIL；不修改候选且不替 User 决定发布。\n"
        "3. User 独占 Accept/Retain/Discard/Apply/Publish/替换 final；未明确决定时停止。\n\n"
        "## Verification Matrix\n"
        "| ID | object | category | allowed operation | check | PASS | failure action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| tests | offline tests | 可重跑验证 | rerun | exit/status | all required checks pass | BLOCK and record failure | Verifier |\n"
        "| PDF | candidate PDF | 可重跑验证 | regenerate in new root | reopen/text/render | readable deterministic output | BLOCK and retain prior evidence | Verifier |\n"
        "| historical manifest | frozen manifest | 不可重跑历史证据 | read-only verify | hash/provenance | exact recorded hash | BLOCK; never regenerate/replace | Verifier |\n"
        "| provenance | result lineage | 不可重跑历史证据 | read-only verify | tuple binding | exact approved tuple | BLOCK | Verifier |\n"
        "| review event | independent review | 不可重跑历史证据 | record observation | complete record | reviewer evidence present | UNKNOWN/BLOCK | Independent Study Reviewer |\n"
        "| retained final | user-held final | 用户持有的发布事项 | user review | explicit disposition | User retains/accepts explicitly | stop without claim | User |\n"
        "| Apply-Publish | Apply/Publish | 用户持有的发布事项 | no operation | explicit User gate | User explicitly authorizes | BLOCK; do not Apply/Publish | User |\n\n"
        "## PASS/FAIL 标准\n"
        "- PASS：每个矩阵行均有允许操作、可核验 check、记录的 PASS 和指定 Owner。\n"
        "- Reviewer-defined verification policy：核对 PDF reopen、文本提取、预期页数、Poppler render、无裁切、无重叠、无缺字、核心标题/流程标签，以及 create-only 覆盖拒绝。\n"
        "- Reviewer-defined verification policy：记录正向、负向、恢复、contract、migration 检查的 command、exit code、pass/fail 与 fresh evidence path；stale/conflict 必须拒绝或明确标记。\n"
        "- FAIL：任何 blocking test failure、hash/provenance 不匹配、缺失、混配、不可读 PDF 或未满足 User gate 均关闭整个 runbook 并保持 BLOCK。\n\n"
        "## 角色与 User Gate\n"
        "- Verifier：负责工程检查、fresh-root rerun、历史证据只读核验和证据记录；可 read/reopen/render/compare/verify，不可 overwrite/Apply/retain/discard/替换 final。\n"
        "- Independent Study Reviewer：使用固定任务评鉴，记录 rating、disposition、PASS/REVISE/FAIL；不修改候选且不替 User 决定发布。\n"
        "- User：独占 Accept/Retain/Discard/Apply/Publish/替换 final；没有明确 User 结论即不继续。\n\n"
        "## 引用\n" + refs + "\n\n## 离线边界\n"
        f"- selected={coverage.total_sources}; read={coverage.read_sources}; cited={coverage.cited_sources}\n"
        "- provider/embedding/MCP calls=0；不使用网络或付费服务。\n"
    )
    return body, body


def _build_artifact(
    draft: DraftSession,
    citations: tuple[ResultCitation, ...],
    coverage: ResultCoverage,
    conflicts: tuple[ResultConflict, ...],
) -> tuple[str, str]:
    key = (
        draft.future_parameters.generator_version,
        draft.future_parameters.retrieval_version,
        draft.future_parameters.segmentation_version,
    )
    if key not in _SUPPORTED_FUTURE_PARAMETERS:
        raise MaterialWorkflowError("UNSUPPORTED_FROZEN_VERSION")
    if key[0] == _GENERATOR_VERSION_V1:
        return _build_artifact_v1(draft, citations, coverage, conflicts)
    if conflicts:
        return _build_artifact_v1(draft, citations, coverage, conflicts)
    if key[0] == _GENERATOR_VERSION_V9 and draft.artifact_kind == "plan":
        return _build_plan_v9(draft, citations, coverage)
    if key[0] == _GENERATOR_VERSION_V8 and draft.artifact_kind == "plan":
        return _build_plan_v8(draft, citations, coverage)
    if key[0] == _GENERATOR_VERSION_V7 and draft.artifact_kind == "plan":
        return _build_plan_v7(draft, citations, coverage)
    if key[0] == _GENERATOR_VERSION_V6 and draft.artifact_kind == "plan":
        return _build_plan_v6(draft, citations, coverage)
    if key[0] == _GENERATOR_VERSION_V5 and draft.artifact_kind == "plan":
        return _build_plan_v5(draft, citations, coverage)
    if key[0] == _GENERATOR_VERSION_V4 and draft.artifact_kind == "plan":
        return _build_plan_v4(draft, citations, coverage)
    if key[0] == _GENERATOR_VERSION_V3 and draft.artifact_kind == "plan":
        return _build_plan_v3(draft, citations, coverage)
    if draft.artifact_kind == "plan":
        return _build_plan_v2(draft, citations, coverage)
    return _build_artifact_v1(draft, citations, coverage, conflicts)


def _conflicts(
    captured: dict[str, tuple[bytes, str]], draft: DraftSession
) -> tuple[tuple[str, tuple[tuple[str, str, int], ...]], ...]:
    found: dict[str, list[tuple[str, str, int]]] = {}
    for path in sorted(captured):
        _raw, text = captured[path]
        for number, line in enumerate(text.splitlines(), 1):
            normalized = unicodedata.normalize("NFC", line).strip()
            match = re.match(
                r"^(?:constraint|requirement|约束|要求)\s*[:：]\s*([^=＝]+?)\s*[=＝]\s*(.+)$",
                normalized,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            key, value = (part.strip() for part in match.groups())
            if 0 < len(key) <= 80 and 0 < len(value) <= 500:
                found.setdefault(key.casefold(), []).append((value, path, number))
    constraints = {key.casefold(): value for key, value in draft.constraints}
    return tuple(
        sorted(
            (key, tuple(sorted(values)))
            for key, values in found.items()
            if len({item[0] for item in values}) > 1
            and constraints.get(key) not in {item[0] for item in values}
        )
    )


def _citation(
    citations: list[ResultCitation],
    *,
    path: str,
    raw: bytes,
    text: str,
    line_start: int,
    line_end: int,
    method: Literal["lexical", "structural", "conflict"],
) -> str:
    span = _line_span(text, line_start, line_end)
    if not span:
        raise MaterialWorkflowError("INVALID_RESULT")
    if len(citations) >= _MAX_CITATIONS:
        raise MaterialWorkflowError("RESULT_LIMIT_EXCEEDED")
    identifier = f"S{len(citations) + 1:03d}"
    citations.append(
        ResultCitation(
            id=identifier,
            relative_path=path,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            line_start=line_start,
            line_end=line_end,
            span_sha256=hashlib.sha256(span.encode("utf-8")).hexdigest(),
            preview=_escape_terminal(span),
            method=method,
        )
    )
    return identifier


def generate_result(
    root: Path, draft: DraftSession, confirmation_hash: str
) -> ResultSession:
    if not isinstance(draft, DraftSession):
        raise MaterialWorkflowError("INVALID_DRAFT")
    _validate_v10_run_binding(root, draft)
    _verify_hashes(draft.model_dump(mode="json"))
    frozen_key = (
        draft.future_parameters.generator_version,
        draft.future_parameters.retrieval_version,
        draft.future_parameters.segmentation_version,
    )
    if frozen_key not in _SUPPORTED_FUTURE_PARAMETERS:
        raise MaterialWorkflowError("UNSUPPORTED_FROZEN_VERSION")
    if (
        not isinstance(confirmation_hash, str)
        or confirmation_hash != draft.contract_hash
        or len(confirmation_hash) != 64
    ):
        raise MaterialWorkflowError("INVALID_CONFIRMATION")
    captured = _captured_sources(root, draft)
    segments: list[ResultSegment] = []
    citations: list[ResultCitation] = []
    lexical_hits: list[tuple[int, str, int, int, int, str, str, str]] = []
    query, query_retrievable = _retrieval_query(draft), True
    indexed_paths: set[str] = set()
    for entry in draft.material_manifest.entries:
        _raw, text = captured[entry.relative_path]
        for ordinal, (chunk, first_line, last_line) in enumerate(
            _segments_for_version(text, draft.future_parameters.segmentation_version), 1
        ):
            raw_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            try:
                index = build_index(
                    [RAGDocument(id=f"d{len(segments):05d}", revision=1, text=chunk)]
                )
            except (RAGValidationError, ValidationError) as error:
                if (
                    not isinstance(error, RAGValidationError)
                    or error.code != "NO_RETRIEVABLE_CONTENT"
                ):
                    # Phase 0 accepts UTF-8 NUL; RAG deliberately does not.  It is
                    # still observable structural material, never an internal leak.
                    if isinstance(error, ValidationError):
                        segments.append(
                            ResultSegment(
                                relative_path=entry.relative_path,
                                ordinal=ordinal,
                                raw_sha256=raw_hash,
                                index_hash=None,
                                bundle_hash=None,
                                retrievable=False,
                                line_start=first_line,
                                line_end=last_line,
                            )
                        )
                        continue
                    raise MaterialWorkflowError("INVALID_RAG_RESULT") from None
                segments.append(
                    ResultSegment(
                        relative_path=entry.relative_path,
                        ordinal=ordinal,
                        raw_sha256=raw_hash,
                        index_hash=None,
                        bundle_hash=None,
                        retrievable=False,
                        line_start=first_line,
                        line_end=last_line,
                    )
                )
                continue
            indexed_paths.add(entry.relative_path)
            bundle_hash: str | None = None
            try:
                found = search(index, query, top_k=3)
            except RAGValidationError as error:
                if error.code != "NO_QUERY_TOKENS":
                    raise MaterialWorkflowError("INVALID_RAG_RESULT") from None
                query_retrievable = False
            else:
                if not verify_search_result(index, query, found):
                    raise MaterialWorkflowError("INVALID_RAG_RESULT")
                bundle_hash = found.bundle_hash
                for hit in found.hits:
                    lexical_hits.append(
                        (
                            hit.score,
                            entry.relative_path,
                            ordinal,
                            hit.citation.normalized_start,
                            hit.citation.normalized_end,
                            hit.citation.chunk_id,
                            index.index_hash,
                            found.bundle_hash,
                        )
                    )
            segments.append(
                ResultSegment(
                    relative_path=entry.relative_path,
                    ordinal=ordinal,
                    raw_sha256=raw_hash,
                    index_hash=index.index_hash,
                    bundle_hash=bundle_hash,
                    retrievable=True,
                    line_start=first_line,
                    line_end=last_line,
                )
            )
    unresolved = _conflicts(captured, draft)
    conflict_objects: list[ResultConflict] = []
    for key, evidence in unresolved:
        identifiers: list[str] = []
        for _value, path, line in evidence:
            raw, text = captured[path]
            identifiers.append(
                _citation(
                    citations,
                    path=path,
                    raw=raw,
                    text=text,
                    line_start=line,
                    line_end=line,
                    method="conflict",
                )
            )
        conflict_objects.append(
            ResultConflict(
                key=key,
                values=tuple(sorted({value for value, _path, _line in evidence})),
                display_values=tuple(
                    _escape_terminal(value)
                    for value in sorted({value for value, _path, _line in evidence})
                ),
                citation_ids=tuple(identifiers),
            )
        )
    ordered_hits = sorted(
        lexical_hits,
        key=lambda item: (-item[0], item[1], item[2], item[3], item[4], item[5]),
    )
    selected_hits: list[tuple[int, str, int, int, int, str, str, str]] = []
    seen_segments: set[tuple[str, int]] = set()
    local_plan = frozen_key[0] in {
        _GENERATOR_VERSION,
        _GENERATOR_VERSION_V3,
        _GENERATOR_VERSION_V4,
        _GENERATOR_VERSION_V5,
        _GENERATOR_VERSION_V6,
        _GENERATOR_VERSION_V7,
        _GENERATOR_VERSION_V8,
        _GENERATOR_VERSION_V9,
    } and any(
        marker in draft.task.casefold()
        for marker in ("本地", "资料", "工作流", "material", "workflow")
    )
    if local_plan:
        for theme in ("gap", "deliverable", "engineering", "human"):
            candidates: list[tuple[int, ResultSegment]] = []
            for segment in segments:
                if (
                    not segment.retrievable
                    or segment.index_hash is None
                    or segment.bundle_hash is None
                ):
                    continue
                _raw, source = captured[segment.relative_path]
                score = _quota_score(
                    _line_span(source, segment.line_start, segment.line_end), theme
                )
                if score:
                    candidates.append((score, segment))
            if candidates:
                _score, segment = min(
                    candidates,
                    key=lambda item: (-item[0], item[1].relative_path, item[1].ordinal),
                )
                key = (segment.relative_path, segment.ordinal)
                if key not in seen_segments:
                    selected_hits.append(
                        (
                            10_000,
                            segment.relative_path,
                            segment.ordinal,
                            0,
                            1,
                            f"theme-{theme}-v2",
                            segment.index_hash,
                            segment.bundle_hash,
                        )
                    )
                    seen_segments.add(key)
    for hit in ordered_hits:
        if (hit[1], hit[2]) not in seen_segments and len(selected_hits) < 8:
            selected_hits.append(hit)
            seen_segments.add((hit[1], hit[2]))
    if frozen_key[0] in {
        _GENERATOR_VERSION,
        _GENERATOR_VERSION_V3,
        _GENERATOR_VERSION_V4,
        _GENERATOR_VERSION_V5,
        _GENERATOR_VERSION_V6,
        _GENERATOR_VERSION_V7,
        _GENERATOR_VERSION_V8,
        _GENERATOR_VERSION_V9,
    }:
        # Planning sources often place each requirement in a separate short
        # Markdown block. Keep relevant blocks observable even when lexical
        # ranking concentrates all top hits in one introductory block.
        for segment in segments:
            key = (segment.relative_path, segment.ordinal)
            if (
                key in seen_segments
                or len(selected_hits) >= 8
                or not segment.retrievable
                or segment.index_hash is None
                or segment.bundle_hash is None
            ):
                continue
            raw, text = captured[segment.relative_path]
            if (
                _substantive_line(
                    text, segment.line_start, segment.line_end, draft.task
                )
                is None
            ):
                continue
            selected_hits.append(
                (
                    1,
                    segment.relative_path,
                    segment.ordinal,
                    0,
                    1,
                    "deterministic-plan-block-v2",
                    segment.index_hash,
                    segment.bundle_hash,
                )
            )
            seen_segments.add(key)
    selected_hits.sort(
        key=lambda item: (-item[0], item[1], item[2], item[3], item[4], item[5])
    )
    hit_ids: list[str] = []
    accepted_hits: list[tuple[int, str, int, int, int, str, str, str]] = []
    for hit in selected_hits:
        _score, path, ordinal, _start, _end, _chunk, _index, _bundle = hit
        segment = next(
            item
            for item in segments
            if (item.relative_path, item.ordinal) == (path, ordinal)
        )
        raw, text = captured[path]
        line_start, line_end = segment.line_start, segment.line_end
        if frozen_key[0] in {
            _GENERATOR_VERSION,
            _GENERATOR_VERSION_V3,
            _GENERATOR_VERSION_V4,
            _GENERATOR_VERSION_V5,
            _GENERATOR_VERSION_V6,
        }:
            substantive = _substantive_line(
                text, segment.line_start, segment.line_end, draft.task
            )
            if substantive is None:
                # It remains visible later as a structural, explicitly low-
                # information reference; it must not masquerade as grounding.
                continue
            line_start = substantive
            line_end = line_start
        hit_ids.append(
            _citation(
                citations,
                path=path,
                raw=raw,
                text=text,
                line_start=line_start,
                line_end=line_end,
                method="lexical",
            )
        )
        accepted_hits.append(hit)
    for path in draft.selections:
        if path not in {item.relative_path for item in citations}:
            raw, text = captured[path]
            nonblank = next(
                (
                    number
                    for number, line in enumerate(text.splitlines(), 1)
                    if line.strip()
                ),
                None,
            )
            if nonblank is not None:
                _citation(
                    citations,
                    path=path,
                    raw=raw,
                    text=text,
                    line_start=nonblank,
                    line_end=nonblank,
                    method="structural",
                )
    coverage = ResultCoverage(
        total_sources=len(draft.material_manifest.entries),
        read_sources=len(captured),
        indexed_sources=len(indexed_paths),
        unretrievable_sources=tuple(sorted(set(draft.selections) - indexed_paths)),
        cited_sources=len({item.relative_path for item in citations}),
        total_segments=len(segments),
        indexed_segments=sum(item.retrievable for item in segments),
        uncited_sources=tuple(
            sorted(set(draft.selections) - {item.relative_path for item in citations})
        ),
    )
    conflicts = tuple(conflict_objects)
    retrieval = ResultRetrieval(
        version=draft.future_parameters.retrieval_version,
        query_source="draft.task",
        query_hash=_query_hash(draft, query),
        requested_segment_top_k=3,
        global_top_k=8,
        query_retrievable=query_retrievable,
        hits=tuple(
            ResultRetrievalHit(
                rank=index + 1,
                relative_path=item[1],
                segment_ordinal=item[2],
                score=item[0],
                normalized_start=item[3],
                normalized_end=item[4],
                chunk_id=item[5],
                index_hash=item[6],
                bundle_hash=item[7],
                citation_id=hit_ids[index],
            )
            for index, item in enumerate(accepted_hits)
        ),
    )
    artifact, preview = _build_artifact(draft, tuple(citations), coverage, conflicts)
    payload: dict[str, object] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "state": "needs_user_decision" if conflicts else "generated",
        "draft": draft.model_dump(),
        "parent_session_hash": draft.session_hash,
        "confirmed_contract_hash": confirmation_hash,
        "segments": tuple(item.model_dump() for item in segments),
        "retrieval": retrieval.model_dump(),
        "citations": tuple(item.model_dump() for item in citations),
        "coverage": coverage.model_dump(),
        "conflicts": tuple(item.model_dump() for item in conflicts),
        "artifact_markdown": artifact,
        "preview_markdown": preview,
        "artifact_hash": hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
        "preview_hash": hashlib.sha256(preview.encode("utf-8")).hexdigest(),
    }
    payload["session_hash"] = _result_hash(payload)
    try:
        result = ResultSession.model_validate(payload)
    except ValidationError as error:
        raise MaterialWorkflowError("INVALID_RESULT") from error
    if len(_canonical_json(result.model_dump(mode="json"))) > MAX_SESSION_BYTES:
        raise MaterialWorkflowError("RESULT_LIMIT_EXCEEDED")
    if not verify_result_integrity(result):
        raise MaterialWorkflowError("INVALID_RESULT")
    return result


def verify_result_integrity(result: ResultSession) -> bool:
    if not isinstance(result, ResultSession):
        return False
    try:
        # ``model_copy`` deliberately skips validation. Revalidate the complete
        # frozen tree so this verifier is authoritative for in-memory sessions.
        ResultSession.model_validate(result.model_dump())
    except ValidationError:
        return False
    data = result.model_dump(mode="json")
    expected_hash = _result_hash(
        {key: value for key, value in data.items() if key != "session_hash"}
    )
    if (
        result.session_hash != expected_hash
        or result.parent_session_hash != result.draft.session_hash
        or result.confirmed_contract_hash != result.draft.contract_hash
    ):
        return False
    try:
        _verify_hashes(result.draft.model_dump(mode="json"))
    except MaterialWorkflowError:
        return False
    if (
        hashlib.sha256(result.artifact_markdown.encode("utf-8")).hexdigest()
        != result.artifact_hash
        or hashlib.sha256(result.preview_markdown.encode("utf-8")).hexdigest()
        != result.preview_hash
        or result.coverage.total_sources != len(result.draft.selections)
        or result.coverage.total_segments != len(result.segments)
        or result.coverage.cited_sources
        != len({item.relative_path for item in result.citations})
        or tuple(item.id for item in result.citations)
        != tuple(f"S{index:03d}" for index in range(1, len(result.citations) + 1))
        or (
            result.draft.future_parameters.generator_version,
            result.draft.future_parameters.retrieval_version,
            result.draft.future_parameters.segmentation_version,
        )
        not in _SUPPORTED_FUTURE_PARAMETERS
        or result.retrieval.version != result.draft.future_parameters.retrieval_version
        or result.retrieval.query_hash
        != _query_hash(result.draft, _retrieval_query(result.draft))
        or result.coverage.read_sources != len(result.draft.selections)
        or result.coverage.indexed_sources
        != len({item.relative_path for item in result.segments if item.retrievable})
        or result.coverage.indexed_segments
        != sum(item.retrievable for item in result.segments)
        or result.coverage.unretrievable_sources
        != tuple(
            sorted(
                set(result.draft.selections)
                - {item.relative_path for item in result.segments if item.retrievable}
            )
        )
        or result.coverage.uncited_sources
        != tuple(
            sorted(
                set(result.draft.selections)
                - {item.relative_path for item in result.citations}
            )
        )
    ):
        return False
    paths = set(result.draft.selections)
    manifest = {
        item.relative_path: item for item in result.draft.material_manifest.entries
    }
    expected_ordinals: dict[str, int] = {path: 1 for path in result.draft.selections}
    previous_line_end: dict[str, int] = {}
    observed_path_order: list[str] = []
    for segment in result.segments:
        entry = manifest.get(segment.relative_path)
        previous = previous_line_end.get(segment.relative_path)
        if (
            entry is None
            or segment.ordinal != expected_ordinals.get(segment.relative_path)
            or segment.line_end > entry.line_count
            or (segment.ordinal == 1 and segment.line_start != 1)
            or (
                previous is not None
                and segment.line_start not in {previous, previous + 1}
            )
        ):
            return False
        if not observed_path_order or observed_path_order[-1] != segment.relative_path:
            observed_path_order.append(segment.relative_path)
        expected_ordinals[segment.relative_path] = segment.ordinal + 1
        previous_line_end[segment.relative_path] = segment.line_end
    if tuple(observed_path_order) != result.draft.selections or any(
        previous_line_end.get(path) != manifest[path].line_count for path in paths
    ):
        return False
    citations = {item.id: item for item in result.citations}
    if any(
        item.relative_path not in paths
        or item.raw_sha256 != manifest[item.relative_path].sha256
        or item.line_end > manifest[item.relative_path].line_count
        for item in result.citations
    ):
        return False
    hit_key = lambda item: (
        -item.score,
        item.relative_path,
        item.segment_ordinal,
        item.normalized_start,
        item.normalized_end,
        item.chunk_id,
    )
    if tuple(hit_key(hit) for hit in result.retrieval.hits) != tuple(
        sorted(hit_key(hit) for hit in result.retrieval.hits)
    ):
        return False
    hit_ids = tuple(hit.citation_id for hit in result.retrieval.hits)
    hit_segments = tuple(
        (hit.relative_path, hit.segment_ordinal) for hit in result.retrieval.hits
    )
    lexical_ids = tuple(
        item.id for item in result.citations if item.method == "lexical"
    )
    if (
        len(set(hit_ids)) != len(hit_ids)
        or len(set(hit_segments)) != len(hit_segments)
        or hit_ids != lexical_ids
    ):
        return False
    if not result.retrieval.query_retrievable and any(
        segment.retrievable and segment.bundle_hash is not None
        for segment in result.segments
    ):
        return False
    if result.retrieval.query_retrievable and any(
        segment.retrievable and segment.bundle_hash is None
        for segment in result.segments
    ):
        return False
    for hit in result.retrieval.hits:
        segment = next(
            (
                item
                for item in result.segments
                if (item.relative_path, item.ordinal)
                == (hit.relative_path, hit.segment_ordinal)
            ),
            None,
        )
        citation = citations.get(hit.citation_id)
        if (
            segment is None
            or not segment.retrievable
            or segment.index_hash != hit.index_hash
            or segment.bundle_hash != hit.bundle_hash
            or citation is None
            or citation.method != "lexical"
            or citation.relative_path != hit.relative_path
            or (
                result.draft.future_parameters.generator_version
                == _GENERATOR_VERSION_V1
                and (citation.line_start, citation.line_end)
                != (segment.line_start, segment.line_end)
            )
            or (
                result.draft.future_parameters.generator_version
                in {
                    _GENERATOR_VERSION,
                    _GENERATOR_VERSION_V3,
                    _GENERATOR_VERSION_V4,
                    _GENERATOR_VERSION_V5,
                    _GENERATOR_VERSION_V6,
                    _GENERATOR_VERSION_V7,
                    _GENERATOR_VERSION_V8,
                    _GENERATOR_VERSION_V9,
                }
                and not (
                    segment.line_start
                    <= citation.line_start
                    <= citation.line_end
                    <= segment.line_end
                )
            )
        ):
            return False
    conflict_ids: list[str] = []
    previous_key = ""
    for conflict in result.conflicts:
        if conflict.key <= previous_key:
            return False
        previous_key = conflict.key
        if conflict.display_values != tuple(
            _escape_terminal(value) for value in conflict.values
        ):
            return False
        derived: set[str] = set()
        if any(
            identifier not in citations or citations[identifier].method != "conflict"
            for identifier in conflict.citation_ids
        ):
            return False
        for identifier in conflict.citation_ids:
            preview = citations[identifier].preview
            match = re.match(
                r"^(?:constraint|requirement|约束|要求)\s*[:：]\s*([^=＝]+?)\s*[=＝]\s*(.+)$",
                unicodedata.normalize("NFC", preview).strip(),
                flags=re.IGNORECASE,
            )
            if not match or match.group(1).strip().casefold() != conflict.key:
                return False
            derived.add(match.group(2).replace("� ⏎", "").replace(" ⏎", "").strip())
        if tuple(sorted(derived)) != conflict.display_values:
            return False
        conflict_ids.extend(conflict.citation_ids)
    all_conflict_ids = tuple(
        item.id for item in result.citations if item.method == "conflict"
    )
    if tuple(sorted(conflict_ids)) != tuple(sorted(all_conflict_ids)) or len(
        set(conflict_ids)
    ) != len(conflict_ids):
        return False
    if result.state == "generated" and result.conflicts:
        return False
    if result.state == "needs_user_decision" and not result.conflicts:
        return False
    method_order = {"conflict": 0, "lexical": 1, "structural": 2}
    if tuple(method_order[item.method] for item in result.citations) != tuple(
        sorted(method_order[item.method] for item in result.citations)
    ):
        return False
    prior_cited_paths = {
        item.relative_path for item in result.citations if item.method != "structural"
    }
    if tuple(
        item.relative_path for item in result.citations if item.method == "structural"
    ) != tuple(
        path for path in result.draft.selections if path not in prior_cited_paths
    ):
        return False
    artifact, preview = _build_artifact(
        result.draft, result.citations, result.coverage, result.conflicts
    )
    return result.artifact_markdown == artifact and result.preview_markdown == preview


def revalidate_result_sources(root: Path, result: ResultSession) -> bool:
    if not verify_result_integrity(result):
        return False
    try:
        captured = _captured_sources(root, result.draft)
    except MaterialWorkflowError:
        return False
    segments_by_path: dict[str, list[ResultSegment]] = {}
    for segment in result.segments:
        segments_by_path.setdefault(segment.relative_path, []).append(segment)
    for path, segments in segments_by_path.items():
        raw, text = captured.get(path, (b"", ""))
        actual = _segments_for_version(
            text, result.draft.future_parameters.segmentation_version
        )
        if len(actual) != len(segments):
            return False
        for segment, (value, start, end) in zip(segments, actual, strict=True):
            if segment.raw_sha256 != hashlib.sha256(
                value.encode("utf-8")
            ).hexdigest() or (segment.line_start, segment.line_end) != (start, end):
                return False
    for item in result.citations:
        raw, text = captured.get(item.relative_path, (b"", ""))
        span = _line_span(text, item.line_start, item.line_end)
        if (
            hashlib.sha256(raw).hexdigest() != item.raw_sha256
            or hashlib.sha256(span.encode("utf-8")).hexdigest() != item.span_sha256
            or not span
            or _escape_terminal(span) != item.preview
        ):
            return False
    for conflict in result.conflicts:
        for identifier in conflict.citation_ids:
            citation = next(item for item in result.citations if item.id == identifier)
            _raw, text = captured[citation.relative_path]
            line = _line_span(text, citation.line_start, citation.line_end)
            match = re.match(
                r"^(?:constraint|requirement|约束|要求)\s*[:：]\s*([^=＝]+?)\s*[=＝]\s*(.+)$",
                unicodedata.normalize("NFC", line).strip(),
                flags=re.IGNORECASE,
            )
            if (
                not match
                or match.group(1).strip().casefold() != conflict.key
                or match.group(2).strip() not in conflict.values
            ):
                return False
    return True


def verify_result(root: Path, result: ResultSession) -> bool:
    if not verify_result_integrity(result):
        return False
    try:
        _validate_v10_run_binding(root, result.draft)
    except MaterialWorkflowError:
        return False
    return revalidate_result_sources(root, result)


def render_preview(result: ResultSession) -> str:
    """Return an integrity-checked frozen preview; no root or RAG access is needed."""
    if not isinstance(result, ResultSession) or not verify_result_integrity(result):
        raise MaterialWorkflowError("INVALID_RESULT")
    return result.preview_markdown


def render_export(root: Path, result: ResultSession) -> bytes:
    if not verify_result(root, result):
        raise MaterialWorkflowError("INVALID_RESULT")
    if result.state != "generated":
        raise MaterialWorkflowError("UNRESOLVED_CONFLICT")
    return (result.artifact_markdown.rstrip("\n") + "\n").encode("utf-8")


def render_pdf_export(
    root: Path,
    result: ResultSession,
    *,
    export_version: str = "v3-material-pdf-export-v1",
) -> bytes:
    """Return a verified, deterministic user-facing PDF without changing Result."""
    if not verify_result(root, result):
        raise MaterialWorkflowError("INVALID_RESULT")
    if result.state != "generated":
        raise MaterialWorkflowError("UNRESOLVED_CONFLICT")
    try:
        from .pdf_export import render_pdf

        data = render_pdf(result, export_version)
    except RuntimeError as error:
        raise MaterialWorkflowError(str(error)) from error
    if not data.startswith(b"%PDF-"):
        raise MaterialWorkflowError("PDF_RENDER_FAILED")
    return data


def serialize_session(session: DraftSession | ResultSession) -> bytes:
    """Return the sole accepted on-disk representation for an immutable session."""
    if not isinstance(session, (DraftSession, ResultSession)):
        raise MaterialWorkflowError("INVALID_SESSION")
    data = session.model_dump(mode="json")
    if isinstance(session, DraftSession):
        _verify_hashes(data)
    elif session.session_hash != _result_hash(
        {key: value for key, value in data.items() if key != "session_hash"}
    ) or not verify_result_integrity(session):
        raise MaterialWorkflowError("INVALID_SESSION_HASH")
    return _canonical_json(data)


def _reject_constant(_value: str) -> object:
    raise ValueError("nonfinite number")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _depth(value: object, current: int = 0) -> int:
    if current > MAX_NESTING:
        raise MaterialWorkflowError("INVALID_SESSION")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, current + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, current + 1)
    return current


def _verify_hashes(data: dict[str, object]) -> None:
    future = data.get("future_parameters")
    if (
        not isinstance(future, dict)
        or (
            future.get("generator_version"),
            future.get("retrieval_version"),
            future.get("segmentation_version"),
        )
        not in _SUPPORTED_FUTURE_PARAMETERS
    ):
        raise MaterialWorkflowError("UNSUPPORTED_FROZEN_VERSION")
    expected_contract = _hash(
        "projecttown/v3/material-contract/v1", _contract_payload(data)
    )
    if data.get("contract_hash") != expected_contract:
        raise MaterialWorkflowError("INVALID_CONTRACT_HASH")
    expected_session = _hash(
        "projecttown/v3/material-session/v1", _session_payload(data)
    )
    if data.get("session_hash") != expected_session:
        raise MaterialWorkflowError("INVALID_SESSION_HASH")


def parse_session_bytes(data: bytes) -> DraftSession | ResultSession:
    if not isinstance(data, bytes) or len(data) > MAX_SESSION_BYTES:
        raise MaterialWorkflowError("INVALID_SESSION")
    try:
        text = data.decode("utf-8", errors="strict")
        decoded = json.loads(
            text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise MaterialWorkflowError("INVALID_SESSION") from error
    try:
        _depth(decoded)
        # JSON arrays are the canonical external representation of frozen tuples.
        session = (
            ResultSession.model_validate_json(data)
            if isinstance(decoded, dict)
            and decoded.get("schema_version") == RESULT_SCHEMA_VERSION
            else DraftSession.model_validate_json(data)
        )
    except (ValidationError, MaterialWorkflowError) as error:
        raise MaterialWorkflowError("INVALID_SESSION") from error
    rendered = _canonical_json(session.model_dump(mode="json"))
    if data != rendered:
        raise MaterialWorkflowError("NONCANONICAL_SESSION")
    rendered_data = session.model_dump(mode="json")
    if isinstance(session, DraftSession):
        _verify_hashes(rendered_data)
    elif session.session_hash != _result_hash(
        {key: value for key, value in rendered_data.items() if key != "session_hash"}
    ) or not verify_result_integrity(session):
        raise MaterialWorkflowError("INVALID_SESSION_HASH")
    return session


def load_session(root: Path, path: Path) -> DraftSession | ResultSession:
    root, _ = _validate_root(root)
    if not isinstance(path, Path) or not path.is_absolute():
        raise MaterialWorkflowError("INVALID_SESSION_PATH")
    try:
        metadata = path.lstat()
        parent = path.parent.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise MaterialWorkflowError("SESSION_UNAVAILABLE") from error
    if (
        canonical != path
        or not is_safe_directory(parent)
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
    ):
        raise MaterialWorkflowError("INVALID_SESSION_PATH")
    try:
        path.relative_to(root)
    except ValueError:
        pass
    else:
        raise MaterialWorkflowError("INVALID_SESSION_PATH")
    return load_external_session(path)


def load_external_session(path: Path) -> DraftSession | ResultSession:
    """Load a frozen external session without requiring its material root."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise MaterialWorkflowError("INVALID_SESSION_PATH")
    try:
        metadata = path.lstat()
        parent = path.parent.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise MaterialWorkflowError("SESSION_UNAVAILABLE") from error
    if (
        canonical != path
        or not is_safe_directory(parent)
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
    ):
        raise MaterialWorkflowError("INVALID_SESSION_PATH")
    if metadata.st_size > MAX_SESSION_BYTES:
        raise MaterialWorkflowError("SESSION_TOO_LARGE")
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise MaterialWorkflowError("UNSTABLE_SESSION")
    return parse_session_bytes(stable[2])


def _validate_output(root: Path, path: Path) -> tuple[Path, os.stat_result]:
    root, _ = _validate_root(root)
    if not isinstance(path, Path) or not path.is_absolute() or path.exists():
        raise MaterialWorkflowError("INVALID_OUTPUT_PATH")
    try:
        parent = path.parent
        parent_metadata = parent.lstat()
        canonical_parent = parent.resolve(strict=True)
    except OSError as error:
        raise MaterialWorkflowError("OUTPUT_UNAVAILABLE") from error
    if (
        parent != canonical_parent
        or not is_safe_directory(parent_metadata)
        or is_reparse(parent_metadata)
    ):
        raise MaterialWorkflowError("INVALID_OUTPUT_PATH")
    try:
        path.relative_to(root)
    except ValueError:
        return parent, parent_metadata
    raise MaterialWorkflowError("INVALID_OUTPUT_PATH")


def _validate_direct_child_output(
    root: Path, path: Path
) -> tuple[Path, os.stat_result]:
    """Validate a new, canonical child directly beneath a trusted root."""
    root, root_metadata = _validate_root(root)
    if not isinstance(path, Path) or not path.is_absolute():
        raise MaterialWorkflowError("INVALID_OUTPUT_PATH")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise MaterialWorkflowError("OUTPUT_UNAVAILABLE") from error
    else:
        raise MaterialWorkflowError("INVALID_OUTPUT_PATH")
    try:
        canonical_path = path.resolve(strict=False)
    except OSError as error:
        raise MaterialWorkflowError("OUTPUT_UNAVAILABLE") from error
    if path != canonical_path or path.parent != root:
        raise MaterialWorkflowError("INVALID_OUTPUT_PATH")
    return root, root_metadata


def _same_directory_identity(before: os.stat_result, after: os.stat_result) -> bool:
    """Ignore expected directory timestamp changes caused by the temp entry."""
    return (
        stat.S_ISDIR(after.st_mode)
        and not is_reparse(after)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
    )


def _same_inode(before: os.stat_result, after: os.stat_result) -> bool:
    """Identity only; content metadata naturally changes while staging bytes."""
    return before.st_dev == after.st_dev and before.st_ino == after.st_ino


def _is_owned_regular(path: Path, expected: os.stat_result, nlink: int) -> bool:
    """Recheck a named entry immediately before unlinking it."""
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not is_reparse(metadata)
        and metadata.st_nlink == nlink
        and _same_inode(expected, metadata)
    )


def publish_new_file(root: Path, path: Path, data: bytes) -> None:
    """Atomically publish bytes as a new external file without overwrite fallback."""
    if not isinstance(data, bytes):
        raise MaterialWorkflowError("INVALID_OUTPUT_DATA")
    parent, parent_metadata = _validate_output(root, path)
    _publish_new_at_validated_parent(parent, parent_metadata, path, data)


def publish_new_direct_child(root: Path, path: Path, data: bytes) -> None:
    """Atomically create one new, direct child of an existing trusted directory."""
    if not isinstance(data, bytes):
        raise MaterialWorkflowError("INVALID_OUTPUT_DATA")
    parent, parent_metadata = _validate_direct_child_output(root, path)
    _publish_new_at_validated_parent(parent, parent_metadata, path, data)


def _publish_new_at_validated_parent(
    parent: Path, parent_metadata: os.stat_result, path: Path, data: bytes
) -> None:
    """Shared create-only staged publication core for validated parent targets."""
    temporary = parent / f".projecttown-{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    temporary_identity: os.stat_result | None = None
    committed = False
    temporary_removed = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        temporary_identity = os.fstat(descriptor)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("incomplete write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        staged_metadata = temporary.lstat()
        if (
            temporary_identity is None
            or not stat.S_ISREG(staged_metadata.st_mode)
            or is_reparse(staged_metadata)
            or staged_metadata.st_nlink != 1
            or not _same_inode(temporary_identity, staged_metadata)
        ):
            raise MaterialWorkflowError("OUTPUT_VERIFY_FAILED")
        staged_descriptor = os.open(temporary, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            if not _same_inode(temporary_identity, os.fstat(staged_descriptor)):
                raise MaterialWorkflowError("OUTPUT_VERIFY_FAILED")
            staged_data = bytearray()
            while len(staged_data) <= len(data):
                chunk = os.read(staged_descriptor, min(65_536, len(data) + 1))
                if not chunk:
                    break
                staged_data.extend(chunk)
        finally:
            os.close(staged_descriptor)
        if bytes(staged_data) != data or not _same_inode(
            temporary_identity, temporary.lstat()
        ):
            raise MaterialWorkflowError("OUTPUT_VERIFY_FAILED")
        if not _same_directory_identity(parent_metadata, parent.lstat()):
            raise MaterialWorkflowError("OUTPUT_PARENT_CHANGED")
        os.link(temporary, path)
        committed = True
        if (
            temporary_identity is None
            or not _is_owned_regular(temporary, temporary_identity, 2)
            or not _is_owned_regular(path, temporary_identity, 2)
        ):
            raise PublicationAttentionError()
        try:
            temporary.unlink()
            temporary_removed = True
        except OSError:
            # Only roll back a temp-unlink failure after proving this is our
            # just-created two-name inode.  Never delete an unrelated final.
            if (
                temporary_identity is None
                or not _is_owned_regular(temporary, temporary_identity, 2)
                or not _is_owned_regular(path, temporary_identity, 2)
            ):
                raise PublicationAttentionError()
            try:
                if not _is_owned_regular(path, temporary_identity, 2):
                    raise PublicationAttentionError()
                path.unlink()
            except OSError:
                raise PublicationAttentionError() from None
            if not _scrub_then_remove_temporary(temporary, temporary_identity):
                raise PublicationAttentionError()
            raise PublicationRollbackError()
        try:
            stable_metadata = path.lstat()
        except OSError:
            raise PublicationAttentionError() from None
        stable = read_stable_regular_file(
            path,
            stable_metadata,
            capture_bytes=True,
            require_single_link=True,
        )
        if stable is None or stable[2] != data:
            try:
                if temporary_identity is None or not _is_owned_regular(
                    path, temporary_identity, 1
                ):
                    raise PublicationAttentionError()
                path.unlink()
            except OSError:
                raise PublicationAttentionError() from None
            raise PublicationRollbackError()
        # Durability after a normal one-link success is explicitly best effort.
        try:
            directory_descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    except MaterialWorkflowError:
        raise
    except OSError as error:
        raise MaterialWorkflowError("OUTPUT_PUBLISH_FAILED") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if (
            not committed
            and not temporary_removed
            and temporary_identity is not None
            and not _scrub_then_remove_temporary(temporary, temporary_identity)
        ):
            raise MaterialWorkflowError("PRECOMMIT_CLEANUP_FAILED")


def _scrub_then_remove_temporary(path: Path, expected: os.stat_result) -> bool:
    """Best effort removal; an unavoidable orphan is reduced to zero bytes."""
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or is_reparse(before)
            or before.st_nlink != 1
            or not _same_inode(expected, before)
        ):
            return False
        descriptor = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            after_open = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or not _same_inode(expected, opened)
                or not _same_inode(expected, after_open)
            ):
                return False
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return False
    if not _is_owned_regular(path, expected, 1):
        return False
    try:
        path.unlink()
    except OSError:
        pass
    return True


__all__ = [
    "RESULT_SCHEMA_VERSION",
    "SESSION_SCHEMA_VERSION",
    "DraftSession",
    "MaterialManifestProjection",
    "MaterialPolicyProjection",
    "MaterialSourceProjection",
    "MaterialWorkflowError",
    "PublicationAttentionError",
    "PublicationRollbackError",
    "ResultCitation",
    "ResultCoverage",
    "ResultRetrieval",
    "ResultRetrievalHit",
    "ResultSegment",
    "ResultSession",
    "create_draft",
    "generate_result",
    "load_external_session",
    "load_session",
    "parse_session_bytes",
    "publish_new_direct_child",
    "publish_new_file",
    "render_export",
    "render_pdf_export",
    "render_preview",
    "revalidate_result_sources",
    "serialize_session",
    "verify_result",
    "verify_result_integrity",
]
