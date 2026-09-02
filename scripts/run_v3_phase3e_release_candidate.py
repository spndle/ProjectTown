"""Create and verify additive Phase 3E release-candidate records.

This command records evidence only.  It never dispatches an Apply, restore,
release, or human study.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
)
from backend.app.phase3e_release_candidate import (
    ControlledOperationBindingV3,
    EngineeringAcceptanceV4,
    P0Values,
    ParticipantEvidence,
    ParticipantEvidenceV4,
    Phase3EError,
    Phase3ERound,
    Phase3ERoundV2,
    Phase3ERoundV3,
    Phase3ERoundV4,
    Phase3EStudy,
    Phase3EStudyV2,
    Phase3EStudyV3,
    Phase3EStudyV4,
    Phase3ESummary,
    Phase3ESummaryV2,
    Phase3ESummaryV3,
    Phase3ESummaryV4,
    PredecessorEvidenceV4,
    ReviewerEvidence,
    Round1ContractV4,
    UserRCDecision,
    UserRCDecisionV2,
    UserRCDecisionV3,
    UserRCDecisionV4,
    create_engineering_acceptance_v4,
    create_round,
    create_round1_contract_v3,
    create_source_set_manifest,
    create_study,
    create_summary,
    create_user_rc_decision,
    load_record,
    publish_record,
    status_projection,
    verify_record,
    verify_round_for_study,
    verify_summary_for_study,
    verify_user_decision_for_study,
)
from scripts._v3_cli_common import CliError, CliParser

_STATUS_SCHEMA = "v3-phase3e-cli-status-v1"
_CALLS = {
    "provider": 0,
    "embedding": 0,
    "mcp": 0,
    "network": 0,
    "egress": 0,
    "paid": 0,
    "image": 0,
}
_NAMES = {
    "study": "study.json",
    "R1-CONTROLLED-APPLY": "R1-CONTROLLED-APPLY.json",
    "R2-REPORT-EXPORT": "R2-REPORT-EXPORT.json",
    "summary": "summary.json",
    "decision": "user-rc-decision.json",
}
_R1_KINDS = (
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
    "restore_observation",
)
_R2_KINDS = ("material_manifest", "result", "preview", "citation", "pdf_export")
_R1_V3_KINDS = (
    "result",
    "apply_plan",
    "executable_proposal",
    "user_authorization",
    "restore_authorization",
    "apply_receipt",
    "restore_receipt",
)
_R1_V4_KINDS = (*_R1_V3_KINDS, "backup", "restore_backup")
_CANONICAL = {
    "result": ("backend.app.material_workflow", "parse_session_bytes", "session_hash"),
    "apply_plan": (
        "backend.app.controlled_apply",
        "parse_apply_plan_bytes",
        "plan_hash",
    ),
    "executable_proposal": (
        "backend.app.executable_proposal",
        "parse_executable_proposal_bytes",
        "proposal_hash",
    ),
    "user_authorization": (
        "backend.app.controlled_write",
        "parse_authorization_bytes",
        "authorization_hash",
    ),
    "restore_authorization": (
        "backend.app.controlled_write",
        "parse_restore_authorization_bytes",
        "authorization_hash",
    ),
    "apply_receipt": (
        "backend.app.controlled_write",
        "parse_receipt_bytes",
        "event_hash",
    ),
    "restore_receipt": (
        "backend.app.controlled_write",
        "parse_receipt_bytes",
        "event_hash",
    ),
}


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise CliError("INVALID_ARGUMENTS")
    return path


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _round1_constraint(value: str) -> tuple[str, str]:
    key, separator, item = value.partition("=")
    key = unicodedata.normalize("NFC", key.replace("\r\n", "\n")).strip()
    item = unicodedata.normalize("NFC", item.replace("\r\n", "\n")).strip()
    if (
        separator != "="
        or not key
        or not item
        or "\x00" in key
        or "\x00" in item
        or len(key.encode("utf-8")) > 80
        or len(item.encode("utf-8")) > 500
    ):
        raise CliError("INVALID_R1_CONSTRAINT")
    return key, item


def _status(
    command: str, outcome: str, code: str, **fields: object
) -> dict[str, object]:
    return {
        "schema_version": _STATUS_SCHEMA,
        "command": command,
        "outcome": outcome,
        "code": code,
        "integrity": "not_checked",
        "publication_state": "not_applicable",
        "offline_calls": _CALLS,
        "paths_disclosed": False,
        "write_performed": False,
        **fields,
    }


def _canonical(kind: str, path: Path) -> tuple[str, Path] | tuple[str, Path, str, str]:
    if kind not in _CANONICAL:
        return kind, path
    module_name, parser_name, hash_name = _CANONICAL[kind]
    module = __import__(module_name, fromlist=[parser_name])
    parsed = getattr(module, parser_name)(path.read_bytes())
    return kind, path, parsed.schema_version, getattr(parsed, hash_name)


def _evidence_sha256(path: Path) -> str:
    """Derive a submitted digest; the core repeats the safe-file verification."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(
    root: Path, name: str, expected: type[object] | tuple[type[object], ...]
) -> object:
    record = load_record(root / name)
    if not isinstance(record, expected):
        raise CliError("RECORD_KIND_MISMATCH")
    return record


def _study_fields(
    study: Phase3EStudy | Phase3EStudyV2 | Phase3EStudyV3 | Phase3EStudyV4,
) -> dict[str, object]:
    return {
        "record_kind": "study",
        "record_schema_version": study.schema_version,
        "study_id": study.study_id,
        "study_hash": study.study_hash,
        "candidate_profile": study.lineage.candidate_profile,
        "p0_present": True,
    }


def _round_fields(
    round_: Phase3ERound | Phase3ERoundV2 | Phase3ERoundV3 | Phase3ERoundV4,
) -> dict[str, object]:
    fields = {
        "record_kind": "round",
        "record_schema_version": round_.schema_version,
        "study_id": round_.study_id,
        "round_id": round_.round_id,
        "round_hash": round_.round_hash,
        "binding_status": round_.binding_status,
        "participant_present": round_.participant is not None,
    }
    if isinstance(round_, Phase3ERoundV4):
        fields["engineering_acceptance_present"] = (
            round_.engineering_acceptance is not None
        )
        fields["human_reviewer_required"] = False
    else:
        fields["reviewer_present"] = round_.reviewer is not None
    return fields


def _summary_fields(
    summary: Phase3ESummary | Phase3ESummaryV2 | Phase3ESummaryV3 | Phase3ESummaryV4,
) -> dict[str, object]:
    return {
        "record_kind": "summary",
        "record_schema_version": summary.schema_version,
        "study_id": summary.study_id,
        "summary_hash": summary.summary_hash,
        "gate_state": summary.gate_state,
        "blocker_count": len(summary.blockers),
    }


def _parser() -> CliParser:
    parser = CliParser(description=__doc__)
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=CliParser
    )
    study = commands.add_parser("study-create")
    study.add_argument("--study-root", type=_path, required=True)
    study.add_argument("--work-root", type=_path, required=True)
    study.add_argument("--study-id", required=True)
    study.add_argument("--manifest", type=_path, required=True)
    study.add_argument("--control-rating-threshold", type=int, required=True)
    study.add_argument("--participant-arrangement", required=True)
    study.add_argument("--participant-count", type=int, required=True)
    study.add_argument("--backup-retention", required=True)
    study.add_argument("--release-evidence-format", required=True)
    study.add_argument("--material-source-root", type=_path)
    study.add_argument("--expected-participant-identity")
    study.add_argument("--expected-reviewer-identity")
    study.add_argument("--protocol-v3", action="store_true")
    study.add_argument("--protocol-v4", action="store_true")
    study.add_argument("--round1-material-root", type=_path)
    study.add_argument("--round1-source-entry", action="append")
    study.add_argument("--round1-no-external-sources", action="store_true")
    study.add_argument("--round1-exact-task")
    study.add_argument("--round1-constraint", action="append")
    study.add_argument("--round1-target", type=_path)
    study.add_argument("--round1-expected-post-image", type=_path)
    study.add_argument("--restore-executor-label")
    for role in (
        "v3-study",
        "result",
        "apply-plan",
        "executable-proposal",
        "apply-authorization",
        "restore-authorization",
        "apply-receipt",
        "restore-receipt",
        "apply-backup",
        "restore-backup",
    ):
        study.add_argument("--predecessor-" + role, type=_path)

    round_ = commands.add_parser("round-create")
    round_.add_argument("--study-root", type=_path, required=True)
    round_.add_argument("--round-id", choices=tuple(_NAMES)[1:3], required=True)
    round_.add_argument(
        "--binding-status",
        choices=("verified", "stale", "conflict", "missing"),
        required=True,
    )
    round_.add_argument(
        "--target-is-disposable-external-fixture", type=_boolean, required=True
    )
    for kind in dict.fromkeys((*_R1_KINDS, *_R2_KINDS)):
        round_.add_argument("--" + kind.replace("_", "-"), type=_path)
    for prefix in ("apply", "restore"):
        round_.add_argument(f"--{prefix}-ledger-root", type=_path)
        round_.add_argument(f"--{prefix}-operation-id")
        round_.add_argument(f"--{prefix}-backup-manifest", type=_path)
        round_.add_argument(f"--{prefix}-post-observation", type=_path)
    round_.add_argument("--engineering-only", action="store_true")
    round_.add_argument("--participant-disposition", choices=("retained", "not_kept"))
    round_.add_argument("--participant-identity")
    round_.add_argument("--participant-elapsed-seconds", type=int)
    round_.add_argument("--participant-action", action="append", choices=("open_task",))
    round_.add_argument("--participant-notes")
    round_.add_argument("--participant-timestamp")
    round_.add_argument("--participant-evidence-path", type=_path)
    round_.add_argument("--participant-control-rating", type=int)
    round_.add_argument("--participant-citation-usable", type=_boolean)
    round_.add_argument("--participant-structural-rewrite", type=_boolean)
    round_.add_argument("--engineering-outcome", choices=("PASS", "FAIL"))
    round_.add_argument("--engineering-verifier-identity")
    round_.add_argument("--engineering-check", action="append")
    round_.add_argument("--engineering-notes")
    round_.add_argument("--engineering-action", action="append")
    round_.add_argument("--engineering-timestamp")
    round_.add_argument("--engineering-evidence-path", type=_path)
    round_.add_argument("--engineering-citation-traceable", type=_boolean)
    round_.add_argument("--engineering-citation-usable", type=_boolean)
    round_.add_argument("--engineering-blocking-defect", type=_boolean)
    round_.add_argument("--reviewer-identity")
    round_.add_argument("--reviewer-disposition", choices=("PASS", "REVISE", "FAIL"))
    for item in ("executability", "readability", "control", "citation-traceability"):
        round_.add_argument("--reviewer-" + item + "-rating", type=int)
    round_.add_argument("--reviewer-fixed-answer", action="append")
    round_.add_argument("--reviewer-notes")
    round_.add_argument("--reviewer-action", action="append")
    round_.add_argument("--reviewer-timestamp")
    round_.add_argument("--reviewer-evidence-path", type=_path)
    round_.add_argument("--citation-usable", type=_boolean)
    round_.add_argument("--structural-rewrite", type=_boolean)
    round_.add_argument("--blocking-defect", type=_boolean, default=False)

    summary = commands.add_parser("summary-create")
    summary.add_argument("--study-root", type=_path, required=True)
    decision = commands.add_parser("user-rc-decision-create")
    decision.add_argument("--study-root", type=_path, required=True)
    decision.add_argument(
        "--decision",
        choices=("ACCEPT", "RETAIN", "REVISE", "DISCARD", "STOP"),
        required=True,
    )
    decision.add_argument("--user-timestamp", required=True)
    decision.add_argument("--evidence-path", type=_path, required=True)
    decision.add_argument("--notes", required=True)
    check = commands.add_parser("check")
    check.add_argument("--study-root", type=_path, required=True)
    check.add_argument(
        "--record",
        choices=("study", "round", "summary", "user-rc-decision"),
        required=True,
    )
    check.add_argument("--round-id", choices=tuple(_NAMES)[1:3])
    status = commands.add_parser("status")
    status.add_argument("--study-root", type=_path, required=True)
    return parser


def _human(
    args: argparse.Namespace,
) -> tuple[ParticipantEvidence | None, ReviewerEvidence | None]:
    values = (
        args.participant_disposition,
        args.participant_identity,
        args.participant_elapsed_seconds,
        args.participant_action,
        args.participant_notes,
        args.participant_timestamp,
        args.participant_evidence_path,
        args.reviewer_identity,
        args.reviewer_disposition,
        args.reviewer_executability_rating,
        args.reviewer_readability_rating,
        args.reviewer_control_rating,
        args.reviewer_citation_traceability_rating,
        args.reviewer_fixed_answer,
        args.reviewer_notes,
        args.reviewer_timestamp,
        args.reviewer_evidence_path,
        args.citation_usable,
        args.structural_rewrite,
    )
    if args.engineering_only:
        if any(value is not None for value in values):
            raise CliError("ENGINEERING_ONLY_HUMAN_FIELDS_FORBIDDEN")
        return None, None
    if any(value is None for value in values):
        raise CliError("MISSING_HUMAN_EVIDENCE")
    participant = ParticipantEvidence(
        participant_identity=args.participant_identity,
        disposition=args.participant_disposition,
        elapsed_seconds=args.participant_elapsed_seconds,
        actions=tuple(args.participant_action),
        notes=args.participant_notes,
        timestamp=args.participant_timestamp,
        evidence_path=str(args.participant_evidence_path),
        evidence_sha256=_evidence_sha256(args.participant_evidence_path),
    )
    reviewer = ReviewerEvidence(
        reviewer_identity=args.reviewer_identity,
        disposition=args.reviewer_disposition,
        executability_rating=args.reviewer_executability_rating,
        readability_rating=args.reviewer_readability_rating,
        control_rating=args.reviewer_control_rating,
        citation_traceability_rating=args.reviewer_citation_traceability_rating,
        fixed_question_answers=tuple(args.reviewer_fixed_answer),
        notes=args.reviewer_notes,
        actions=tuple(args.reviewer_action or ()),
        timestamp=args.reviewer_timestamp,
        evidence_path=str(args.reviewer_evidence_path),
        evidence_sha256=_evidence_sha256(args.reviewer_evidence_path),
    )
    return participant, reviewer


def _human_v4(
    args: argparse.Namespace,
) -> tuple[ParticipantEvidenceV4 | None, EngineeringAcceptanceV4 | None]:
    """Parse only participant and non-human engineering fields for v4."""
    reviewer_values = (
        args.reviewer_identity,
        args.reviewer_disposition,
        args.reviewer_executability_rating,
        args.reviewer_readability_rating,
        args.reviewer_control_rating,
        args.reviewer_citation_traceability_rating,
        args.reviewer_fixed_answer,
        args.reviewer_notes,
        args.reviewer_timestamp,
        args.reviewer_evidence_path,
    )
    if any(value is not None for value in reviewer_values):
        raise CliError("V4_REVIEWER_FIELDS_FORBIDDEN")
    values = (
        args.participant_disposition,
        args.participant_identity,
        args.participant_elapsed_seconds,
        args.participant_action,
        args.participant_notes,
        args.participant_timestamp,
        args.participant_evidence_path,
        args.participant_control_rating,
        args.participant_citation_usable,
        args.participant_structural_rewrite,
        args.engineering_outcome,
        args.engineering_verifier_identity,
        args.engineering_check,
        args.engineering_notes,
        args.engineering_timestamp,
        args.engineering_evidence_path,
        args.engineering_citation_traceable,
        args.engineering_citation_usable,
        args.engineering_blocking_defect,
    )
    if args.engineering_only:
        raise CliError("V4_INSTANCE_EVIDENCE_REQUIRED")
    if any(value is None for value in values):
        raise CliError("MISSING_V4_INSTANCE_EVIDENCE")
    participant = ParticipantEvidenceV4(
        participant_identity=args.participant_identity,
        disposition=args.participant_disposition,
        elapsed_seconds=args.participant_elapsed_seconds,
        actions=tuple(args.participant_action),
        notes=args.participant_notes,
        timestamp=args.participant_timestamp,
        evidence_path=str(args.participant_evidence_path),
        evidence_sha256=_evidence_sha256(args.participant_evidence_path),
        control_rating=args.participant_control_rating,
        citation_usable=args.participant_citation_usable,
        structural_rewrite=args.participant_structural_rewrite,
    )
    engineering = create_engineering_acceptance_v4(
        outcome=args.engineering_outcome,
        verifier_identity=args.engineering_verifier_identity,
        checks=tuple(args.engineering_check),
        notes=args.engineering_notes,
        actions=tuple(args.engineering_action or ()),
        timestamp=args.engineering_timestamp,
        evidence_path=str(args.engineering_evidence_path),
        evidence_sha256=_evidence_sha256(args.engineering_evidence_path),
        citation_traceable=args.engineering_citation_traceable,
        citation_usable=args.engineering_citation_usable,
        blocking_defect=args.engineering_blocking_defect,
    )
    return participant, engineering


def _round(
    args: argparse.Namespace,
    study: Phase3EStudy | Phase3EStudyV2 | Phase3EStudyV3 | Phase3EStudyV4,
) -> Phase3ERound:
    if isinstance(study, Phase3EStudyV3):
        raise CliError("PROTOCOL_HOLD")
    expected = (
        _R1_V4_KINDS
        if isinstance(study, Phase3EStudyV4) and args.round_id == "R1-CONTROLLED-APPLY"
        else _R1_V3_KINDS
        if isinstance(study, Phase3EStudyV3) and args.round_id == "R1-CONTROLLED-APPLY"
        else _R1_KINDS
        if args.round_id == "R1-CONTROLLED-APPLY"
        else _R2_KINDS
    )
    forbidden = (
        set(_R2_KINDS) - set(_R1_KINDS)
        if args.round_id == "R1-CONTROLLED-APPLY"
        else set(_R1_KINDS) - set(_R2_KINDS)
    )
    if (
        isinstance(study, (Phase3EStudyV3, Phase3EStudyV4))
        and args.round_id == "R1-CONTROLLED-APPLY"
    ):
        allowed = _R1_V4_KINDS if isinstance(study, Phase3EStudyV4) else _R1_V3_KINDS
        forbidden = forbidden | (set(_R1_KINDS) - set(allowed))
    if any(getattr(args, item) is not None for item in forbidden):
        raise CliError("WRONG_ROUND_EVIDENCE")
    if any(getattr(args, item) is None for item in expected):
        raise CliError("MISSING_REQUIRED_EVIDENCE")
    if isinstance(study, Phase3EStudyV4):
        participant, engineering = _human_v4(args)
        reviewer = None
    else:
        participant, reviewer = _human(args)
        engineering = None
    evidence = tuple(_canonical(kind, getattr(args, kind)) for kind in expected)
    if (
        isinstance(study, (Phase3EStudyV3, Phase3EStudyV4))
        and args.round_id == "R2-REPORT-EXPORT"
    ):
        evidence += (
            ("source_set_manifest", Path(study.round2_source.source_set_manifest_path)),
        )
    if isinstance(study, Phase3EStudyV2) and args.round_id == "R2-REPORT-EXPORT":
        evidence += (
            ("source_set_manifest", Path(study.round2_source.source_set_manifest_path)),
        )
    operations: dict[str, ControlledOperationBindingV3 | None] = {}
    if isinstance(study, Phase3EStudyV3) and args.round_id == "R1-CONTROLLED-APPLY":
        for prefix, receipt_kind, authorization_kind in (
            ("apply", "apply_receipt", "user_authorization"),
            ("restore", "restore_receipt", "restore_authorization"),
        ):
            values = (
                getattr(args, f"{prefix}_ledger_root"),
                getattr(args, f"{prefix}_operation_id"),
                getattr(args, f"{prefix}_backup_manifest"),
                getattr(args, f"{prefix}_post_observation"),
            )
            if any(value is None for value in values):
                raise CliError("MISSING_REQUIRED_EVIDENCE")
            operations[f"{prefix}_operation"] = ControlledOperationBindingV3(
                authorization_path=str(getattr(args, authorization_kind)),
                ledger_root=str(values[0]),
                operation_id=values[1],
                backup_manifest_path=str(values[2]),
                post_observation_path=str(values[3]),
                receipt_path=str(getattr(args, receipt_kind)),
            )
    return create_round(
        study,
        round_id=args.round_id,
        binding_status=args.binding_status,
        target_is_disposable_external_fixture=args.target_is_disposable_external_fixture,
        evidence_paths=evidence,
        participant=participant,
        reviewer=reviewer,
        engineering_acceptance=engineering,
        citation_usable=args.citation_usable,
        structural_rewrite=args.structural_rewrite,
        blocking_defect=args.blocking_defect,
        **operations,
    )


def _rounds(
    root: Path,
) -> tuple[Phase3ERound | Phase3ERoundV2 | Phase3ERoundV3 | Phase3ERoundV4, ...]:
    found: list[Phase3ERound | Phase3ERoundV2 | Phase3ERoundV3 | Phase3ERoundV4] = []
    for round_id in tuple(_NAMES)[1:3]:
        path = root / _NAMES[round_id]
        if path.exists():
            found.append(
                _load(
                    root,
                    _NAMES[round_id],
                    (Phase3ERound, Phase3ERoundV2, Phase3ERoundV3, Phase3ERoundV4),
                )
            )  # type: ignore[arg-type]
    return tuple(found)


def _run(args: argparse.Namespace) -> dict[str, object]:
    root: Path = args.study_root
    if args.command == "study-create":
        v2_requested = args.material_source_root is not None
        if args.protocol_v3 and args.protocol_v4:
            raise CliError("INVALID_PROTOCOL_SELECTION")
        if (args.protocol_v3 or args.protocol_v4) and not v2_requested:
            raise CliError("MISSING_V3_SOURCE_BINDING")
        if v2_requested and (
            args.expected_participant_identity is None
            or (not args.protocol_v4 and args.expected_reviewer_identity is None)
        ):
            raise CliError("MISSING_V2_SOURCE_BINDING")
        if v2_requested:
            source_set = create_source_set_manifest(args.material_source_root)
            source_set_path = args.work_root / "source-set-manifest.json"
            if source_set_path.exists():
                existing_source_set = load_record(source_set_path)
                if existing_source_set != source_set:
                    raise CliError("SOURCE_SET_MANIFEST_CONFLICT")
            else:
                publish_record(args.work_root, "source-set-manifest.json", source_set)
        round1_contract = None
        if args.protocol_v3:
            required = (
                args.round1_material_root,
                args.round1_exact_task,
                args.round1_target,
                args.round1_expected_post_image,
                args.restore_executor_label,
            )
            if any(value is None for value in required) or (
                args.round1_no_external_sources == bool(args.round1_source_entry)
            ):
                raise CliError("MISSING_V3_ROUND1_CONTRACT")
            constraints = tuple(
                sorted(
                    _round1_constraint(item) for item in args.round1_constraint or ()
                )
            )
            if len({key.casefold() for key, _ in constraints}) != len(constraints):
                raise CliError("INVALID_R1_CONSTRAINT")
            round1_contract = create_round1_contract_v3(
                material_root=args.round1_material_root,
                source_paths=tuple(
                    Path(item) for item in args.round1_source_entry or ()
                ),
                no_external_sources=args.round1_no_external_sources,
                exact_task=args.round1_exact_task,
                constraints=constraints,
                target_path=args.round1_target,
                expected_post_image_path=args.round1_expected_post_image,
                restore_executor_label=args.restore_executor_label,
                expected_participant_identity=args.expected_participant_identity,
                expected_reviewer_identity=args.expected_reviewer_identity,
            )
        if args.protocol_v4:
            required = (
                args.round1_material_root,
                args.round1_exact_task,
                args.round1_target,
                args.expected_participant_identity,
            )
            if any(value is None for value in required):
                raise CliError("MISSING_V4_ROUND1_CONTRACT")
            predecessor_roles = (
                ("v3_study", args.predecessor_v3_study),
                ("result", args.predecessor_result),
                ("apply_plan", args.predecessor_apply_plan),
                ("executable_proposal", args.predecessor_executable_proposal),
                ("apply_authorization", args.predecessor_apply_authorization),
                ("restore_authorization", args.predecessor_restore_authorization),
                ("apply_receipt", args.predecessor_apply_receipt),
                ("restore_receipt", args.predecessor_restore_receipt),
                ("apply_backup", args.predecessor_apply_backup),
                ("restore_backup", args.predecessor_restore_backup),
            )
            if any(path is None for _, path in predecessor_roles):
                raise CliError("MISSING_V4_PREDECESSOR_EVIDENCE")
            prior = load_record(args.predecessor_v3_study)
            if not isinstance(prior, Phase3EStudyV3) or not verify_record(prior):
                raise CliError("INVALID_V4_PREDECESSOR_STUDY")
            round1_contract = Round1ContractV4(
                material_root=str(args.round1_material_root),
                exact_task=args.round1_exact_task,
                target_path=str(args.round1_target),
                expected_participant_identity=args.expected_participant_identity,
                predecessor_study_hash=prior.study_hash,
                predecessor_evidence=tuple(
                    PredecessorEvidenceV4(
                        path=str(path),
                        bytes_sha256=_evidence_sha256(path),
                        role=role,
                        inherited_historical_evidence=True,
                    )
                    for role, path in predecessor_roles
                ),
            )
        study = create_study(
            args.study_id,
            root,
            args.work_root,
            args.manifest,
            p0=P0Values(
                control_rating_threshold=args.control_rating_threshold,
                participant_arrangement=args.participant_arrangement,
                participant_count=args.participant_count,
                backup_retention=args.backup_retention,
                release_evidence_format=args.release_evidence_format,
            ),
            material_source_root=args.material_source_root,
            source_set_manifest_path=(
                args.work_root / "source-set-manifest.json" if v2_requested else None
            ),
            expected_participant_identity=args.expected_participant_identity,
            expected_reviewer_identity=args.expected_reviewer_identity,
            round1_contract=round1_contract,
            expected_post_image_path=(
                args.round1_expected_post_image if args.protocol_v3 else None
            ),
            protocol_v4=args.protocol_v4,
        )
        publish_record(root, _NAMES["study"], study)
        return _status(
            args.command,
            "ok",
            "STUDY_CREATED",
            integrity="self_consistent",
            publication_state="committed",
            **_study_fields(study),
        )
    study = _load(
        root,
        _NAMES["study"],
        (Phase3EStudy, Phase3EStudyV2, Phase3EStudyV3, Phase3EStudyV4),
    )
    if not verify_record(study):
        raise CliError("INVALID_STUDY")
    if args.command == "round-create":
        round_ = _round(args, study)
        publish_record(root, _NAMES[round_.round_id], round_)
        return _status(
            args.command,
            "ok",
            "ROUND_RECORDED",
            integrity="self_consistent",
            publication_state="committed",
            **_round_fields(round_),
        )
    rounds = _rounds(root)
    if args.command == "summary-create":
        if len(rounds) != 2:
            raise CliError("MISSING_ROUNDS")
        summary = create_summary(study, (rounds[0], rounds[1]))
        publish_record(root, _NAMES["summary"], summary)
        return _status(
            args.command,
            "ok",
            "SUMMARY_CREATED",
            integrity="self_consistent",
            publication_state="committed",
            **_summary_fields(summary),
        )
    summary_path = root / _NAMES["summary"]
    summary = (
        _load(
            root,
            _NAMES["summary"],
            (Phase3ESummary, Phase3ESummaryV2, Phase3ESummaryV3, Phase3ESummaryV4),
        )
        if summary_path.exists()
        else None
    )
    decision_path = root / _NAMES["decision"]
    decision = (
        _load(
            root,
            _NAMES["decision"],
            (UserRCDecision, UserRCDecisionV2, UserRCDecisionV3, UserRCDecisionV4),
        )
        if decision_path.exists()
        else None
    )
    projection = status_projection(study, rounds, summary, decision)
    if args.command == "user-rc-decision-create":
        if summary is None:
            raise CliError("MISSING_SUMMARY")
        item = create_user_rc_decision(
            study,
            summary,
            decision=args.decision,
            user_timestamp=args.user_timestamp,
            evidence_path=args.evidence_path,
            notes=args.notes,
        )
        publish_record(root, _NAMES["decision"], item)
        return _status(
            args.command,
            "ok",
            "USER_RC_DECISION_RECORDED",
            integrity="self_consistent",
            publication_state="committed",
            decision=item.decision,
            outcome_gate=item.outcome,
        )
    if args.command == "check":
        if args.record == "study":
            if args.round_id is not None or not verify_record(study):
                raise CliError("INVALID_ARGUMENTS")
            fields = _study_fields(study)
        elif args.record == "round":
            if args.round_id is None:
                raise CliError("INVALID_ARGUMENTS")
            round_ = _load(
                root,
                _NAMES[args.round_id],
                (Phase3ERound, Phase3ERoundV2, Phase3ERoundV3, Phase3ERoundV4),
            )
            if not verify_round_for_study(study, round_):
                raise CliError("ROUND_CROSS_BINDING_MISMATCH")
            fields = _round_fields(round_)
        elif args.record == "summary":
            if len(rounds) != 2:
                raise CliError("SUMMARY_CROSS_BINDING_MISMATCH")
            if (
                args.round_id is not None
                or summary is None
                or not verify_summary_for_study(
                    study,
                    (rounds[0], rounds[1]),
                    summary,
                )
            ):
                raise CliError("SUMMARY_CROSS_BINDING_MISMATCH")
            fields = _summary_fields(summary)
        else:
            if (
                args.round_id is not None
                or decision is None
                or summary is None
                or not verify_user_decision_for_study(study, summary, decision)
            ):
                raise CliError("USER_RC_DECISION_MISMATCH")
            fields = {"record_kind": "user_rc_decision", "decision_present": True}
        return _status(
            args.command, "ok", "CHECKED", integrity="self_consistent", **fields
        )
    if args.command == "status":
        return _status(
            args.command,
            "ok",
            "STATUS",
            integrity="self_consistent",
            gate_state=projection["gate_state"],
            blocker_count=len(projection["blockers"]),
            next_action=projection["next_action"],
            records_present=projection["present_records"],
        )
    raise CliError()


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    command = (
        raw[0]
        if raw
        and raw[0]
        in {
            "study-create",
            "round-create",
            "summary-create",
            "user-rc-decision-create",
            "check",
            "status",
        }
        else "unknown"
    )
    try:
        args = _parser().parse_args(raw)
        command = args.command
        status, code = _run(args), 0
    except PublicationAttentionError as error:
        status, code = (
            _status(
                command,
                "attention_required",
                error.code,
                publication_state="committed_needs_attention",
            ),
            3,
        )
    except PublicationRollbackError as error:
        status, code = (
            _status(command, "rejected", error.code, publication_state="rolled_back"),
            2,
        )
    except (Phase3EError, CliError, ValueError, OSError) as error:
        error_code = getattr(error, "code", "REJECTED")
        attention = error_code == "COMMITTED_NEEDS_ATTENTION"
        rolled_back = error_code == "PUBLICATION_ROLLED_BACK"
        status, code = (
            _status(
                command,
                "attention_required" if attention else "rejected",
                error_code,
                publication_state=(
                    "committed_needs_attention"
                    if attention
                    else "rolled_back"
                    if rolled_back
                    else "not_applicable"
                ),
            ),
            3 if attention else 2,
        )
    print(json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
