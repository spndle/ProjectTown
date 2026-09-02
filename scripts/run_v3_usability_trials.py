"""Offline CLI for immutable Phase 2 Study, Trial, and Summary records."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
)
from backend.app.safe_files import (
    is_reparse,
    is_safe_directory,
    read_stable_regular_file,
)
from backend.app.usability_trials import (
    TASK_IDS,
    Study,
    Summary,
    Trial,
    UsabilityTrialError,
    aggregate_summary,
    create_study,
    create_trial,
    load_study,
    load_summary,
    load_trial,
    pdf_presentation_pair_for_profile,
    publish_study,
    publish_summary,
    publish_trial,
    render_summary,
    verify_summary,
    verify_trial,
)
from scripts._v3_cli_common import CliError as _CliError
from scripts._v3_cli_common import CliParser as _Parser
from scripts._v3_cli_common import canonical_absolute_path as _path

_SCHEMA = "v3-usability-cli-status-v1"
_OFFLINE_CALLS = {"provider": 0, "embedding": 0, "mcp": 0}
_EVALUATIONS = ("human_usability", "synthetic_engineering_fixture")
_STATES = ("completed", "workflow_failed", "abandoned")
_ACTIONS = (
    "open_task",
    "select_materials",
    "confirm_and_generate",
    "preview",
    "export_or_retain",
    "resolve_conflict",
    "regenerate",
    "manual_rewrite",
    "stop",
)
_DISPOSITIONS = ("exported", "retained", "not_kept")
_FAILURE_STAGES = (
    "material_selection",
    "draft",
    "confirmation",
    "generation",
    "preview",
    "export",
)
_FAILURE_CODES = (
    "invalid_input",
    "source_changed",
    "unresolved_conflict",
    "publication_rolled_back",
    "publication_needs_attention",
    "unexpected_error",
    "user_stopped",
    "needs_user_decision",
)
_IMPROVEMENTS = (
    "none",
    "clarity",
    "citation",
    "workflow",
    "artifact_quality",
    "other_structured",
)
_MANIFESTS = {
    "human_usability": REPOSITORY_ROOT
    / "examples"
    / "v3-phase-2"
    / "projecttown-trial-manifest.json",
    "synthetic_engineering_fixture": REPOSITORY_ROOT
    / "examples"
    / "v3-phase-2"
    / "engineering-manifest.json",
}
_PDF_CANDIDATE_PROFILES = {
    "projecttown-human-pdf-v2": (
        "v3-phase-2-projecttown-trial-candidates-v2",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v2.json",
    ),
    "projecttown-human-pdf-v3": (
        "v3-phase-2-projecttown-trial-candidates-v3",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v3.json",
    ),
    "projecttown-human-pdf-v4": (
        "v3-phase-2-projecttown-trial-candidates-v4",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v4.json",
    ),
    "projecttown-human-pdf-v5": (
        "v3-phase-2-projecttown-trial-candidates-v5",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v5.json",
    ),
    "projecttown-human-pdf-v6": (
        "v3-phase-2-projecttown-trial-candidates-v6",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v6.json",
    ),
    "projecttown-human-pdf-v7": (
        "v3-phase-2-projecttown-trial-candidates-v7",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v7.json",
    ),
    "projecttown-human-pdf-v8": (
        "v3-phase-2-projecttown-trial-candidates-v8",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v8.json",
    ),
    "projecttown-human-pdf-v9": (
        "v3-phase-2-projecttown-trial-candidates-v9",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v9.json",
    ),
    "projecttown-human-pdf-v10": (
        "v3-phase-2-projecttown-trial-candidates-v10",
        REPOSITORY_ROOT
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v10.json",
    ),
}


def _boolean(raw: str) -> bool:
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _status(
    command: str, outcome: str, code: str, **fields: object
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA,
        "command": command,
        "outcome": outcome,
        "code": code,
        "integrity": "not_checked",
        "publication_state": "not_applicable",
        "offline_calls": _OFFLINE_CALLS,
        "evidence_provenance": "external_self_consistent_unanchored",
        "product_value_conclusion": "not_accepted",
        **fields,
    }


def _study_fields(study: Study) -> dict[str, object]:
    fields: dict[str, object] = {
        "record_kind": "study",
        "study_id": study.study_id,
        "study_hash": study.study_hash,
        "candidate_manifest_hash": study.candidate_manifest_hash,
        "evaluation_kind": study.evaluation_kind,
        "task_count": len(study.tasks),
    }
    if hasattr(study, "candidate_profile"):
        fields["candidate_profile"] = study.candidate_profile  # type: ignore[attr-defined]
    return fields


def _trial_fields(trial: Trial) -> dict[str, object]:
    fields: dict[str, object] = {
        "record_kind": "trial",
        "study_id": trial.study_id,
        "study_hash": trial.study_hash,
        "evaluation_kind": trial.evaluation_kind,
        "task_id": trial.task_id,
        "artifact_kind": trial.artifact_kind,
        "state": trial.state,
        "record_hash": trial.record_hash,
        "call_observation": trial.call_observation,
    }
    if trial.schema_version == "v3-usability-trial-v3":
        fields.update(
            trial_schema_version=trial.schema_version,
            participant_notes_present=True,
            participant_timestamp_present=True,
            participant_evidence_path_present=trial.participant_evidence_path
            is not None,  # type: ignore[attr-defined]
        )
    return fields


def _summary_fields(summary: Summary) -> dict[str, object]:
    return {
        "record_kind": "summary",
        "study_id": summary.study.study_id,
        "study_hash": summary.study.study_hash,
        "evaluation_kind": summary.study.evaluation_kind,
        "summary_hash": summary.summary_hash,
        "summary_gate_state": summary.gate_state,
        "metrics": summary.metrics.model_dump(mode="json"),
    }


def _strict_manifest_json(data: bytes) -> dict[str, object]:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate key")
            parsed[key] = value
        return parsed

    try:
        parsed = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise _CliError("INVALID_CANDIDATE_MANIFEST") from error
    if not isinstance(parsed, dict):
        raise _CliError("INVALID_CANDIDATE_MANIFEST")
    return parsed


def _candidate_manifest_for(
    evaluation_kind: str, candidate_profile: str | None
) -> tuple[tuple[str, ...], str]:
    if (
        candidate_profile is not None
        and candidate_profile not in _PDF_CANDIDATE_PROFILES
    ):
        raise _CliError("INVALID_CANDIDATE_PROFILE")
    if candidate_profile is not None and evaluation_kind != "human_usability":
        raise _CliError("INVALID_CANDIDATE_PROFILE")
    path = (
        _PDF_CANDIDATE_PROFILES[candidate_profile][1]
        if candidate_profile is not None
        else _MANIFESTS[evaluation_kind]
    )
    try:
        metadata = path.lstat()
        parent_before = path.parent.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise _CliError("CANDIDATE_MANIFEST_UNAVAILABLE") from error
    if (
        canonical != path
        or not stat.S_ISREG(metadata.st_mode)
        or is_reparse(metadata)
        or not is_safe_directory(parent_before)
        or is_reparse(parent_before)
        or metadata.st_size > 256 * 1024
    ):
        raise _CliError("CANDIDATE_MANIFEST_UNAVAILABLE")
    stable = read_stable_regular_file(
        path, metadata, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise _CliError("CANDIDATE_MANIFEST_UNAVAILABLE")
    try:
        parent_after = path.parent.lstat()
    except OSError as error:
        raise _CliError("CANDIDATE_MANIFEST_UNAVAILABLE") from error
    if (
        not is_safe_directory(parent_after)
        or is_reparse(parent_after)
        or parent_before.st_dev != parent_after.st_dev
        or parent_before.st_ino != parent_after.st_ino
    ):
        raise _CliError("CANDIDATE_MANIFEST_UNAVAILABLE")
    manifest = _strict_manifest_json(stable[2])
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != len(TASK_IDS):
        raise _CliError("INVALID_CANDIDATE_MANIFEST")
    expected_schema = (
        _PDF_CANDIDATE_PROFILES[candidate_profile][0]
        if candidate_profile is not None
        else "v3-phase-2-projecttown-trial-candidates-v1"
        if evaluation_kind == "human_usability"
        else "v3-phase-2-engineering-fixtures-v1"
    )
    if manifest.get("schema_version") != expected_schema:
        raise _CliError("INVALID_CANDIDATE_MANIFEST")
    if candidate_profile and manifest.get("candidate_profile") != candidate_profile:
        raise _CliError("INVALID_CANDIDATE_MANIFEST")
    kinds: list[str] = []
    for task_id, entry in zip(TASK_IDS, entries, strict=True):
        if (
            not isinstance(entry, dict)
            or entry.get("id") != task_id
            or entry.get("label") != evaluation_kind
            or entry.get("artifact_kind") not in {"plan", "report", "readme"}
        ):
            raise _CliError("INVALID_CANDIDATE_MANIFEST")
        kinds.append(str(entry["artifact_kind"]))
    if Counter(kinds) != {
        "plan": 4,
        "report": 3,
        "readme": 3,
    }:
        raise _CliError("INVALID_CANDIDATE_MANIFEST")
    return tuple(kinds), hashlib.sha256(stable[2]).hexdigest()


def _failure_code(raw: str | None) -> str | None:
    return "unresolved_conflict" if raw == "needs_user_decision" else raw


def _load_trials(root: Path) -> tuple[Trial, ...]:
    return tuple(load_trial(root, task_id) for task_id in TASK_IDS)


def _require_current_candidate_manifest(study: Study) -> None:
    profile = getattr(study, "candidate_profile", None)
    kinds, manifest_hash = _candidate_manifest_for(study.evaluation_kind, profile)
    if (
        manifest_hash != study.candidate_manifest_hash
        or tuple(task.artifact_kind for task in study.tasks) != kinds
    ):
        raise _CliError("CANDIDATE_MANIFEST_MISMATCH")


def _matches_study(study: Study, trial: Trial) -> bool:
    task = next((item for item in study.tasks if item.task_id == trial.task_id), None)
    if task is None or (
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
        return False
    if not hasattr(study, "candidate_profile"):
        return True
    participant_evidence_profile = study.candidate_profile in {  # type: ignore[attr-defined]
        "projecttown-human-pdf-v5",
        "projecttown-human-pdf-v6",
        "projecttown-human-pdf-v7",
        "projecttown-human-pdf-v8",
        "projecttown-human-pdf-v9",
        "projecttown-human-pdf-v10",
    }
    if participant_evidence_profile != (
        trial.schema_version == "v3-usability-trial-v3"
    ):
        return False
    presentation = getattr(trial, "presentation", None)
    expected = pdf_presentation_pair_for_profile(study.candidate_profile)
    return expected is not None and (
        presentation is None
        or (presentation.pdf_export_version, presentation.pdf_renderer_version)
        == expected
    )


def _build_parser() -> _Parser:
    parser = _Parser(add_help=True)
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_Parser
    )
    study = commands.add_parser("study-create", add_help=True)
    study.add_argument("--study-root", required=True)
    study.add_argument("--study-id", required=True)
    study.add_argument("--evaluation-kind", required=True, choices=_EVALUATIONS)
    study.add_argument("--candidate-profile")

    trial = commands.add_parser("trial-create", add_help=True)
    trial.add_argument("--study-root", required=True)
    trial.add_argument("--task-id", required=True, choices=TASK_IDS)
    trial.add_argument("--state", required=True, choices=_STATES)
    trial.add_argument("--action", action="append", required=True, choices=_ACTIONS)
    trial.add_argument("--elapsed-seconds", required=True, type=int)
    trial.add_argument("--manual-baseline-seconds", required=True, type=int)
    trial.add_argument("--control-rating", required=True, type=int)
    trial.add_argument("--disposition", required=True, choices=_DISPOSITIONS)
    trial.add_argument("--improvement-reason", required=True, choices=_IMPROVEMENTS)
    trial.add_argument("--structural-rewrite", type=_boolean)
    trial.add_argument("--citation-usable", type=_boolean)
    trial.add_argument("--failure-stage", choices=_FAILURE_STAGES)
    trial.add_argument("--failure-code", choices=_FAILURE_CODES)
    trial.add_argument("--material-root")
    trial.add_argument("--result")
    trial.add_argument("--export")
    trial.add_argument("--pdf-export")
    trial.add_argument("--participant-notes")
    trial.add_argument("--participant-timestamp")
    trial.add_argument("--participant-evidence-path")

    summary = commands.add_parser("summary-create", add_help=True)
    summary.add_argument("--study-root", required=True)

    check = commands.add_parser("check", add_help=True)
    check.add_argument("--study-root", required=True)
    check.add_argument("--record", required=True, choices=("study", "trial", "summary"))
    check.add_argument("--task-id", choices=TASK_IDS)

    preview = commands.add_parser("preview", add_help=True)
    preview.add_argument("--study-root", required=True)
    return parser


def _run(args: argparse.Namespace) -> dict[str, object]:
    command = args.command
    root = _path(args.study_root, "INVALID_STUDY_ROOT")
    if command == "study-create":
        profile = args.candidate_profile
        kinds, manifest_hash = _candidate_manifest_for(args.evaluation_kind, profile)
        study = create_study(
            args.study_id,
            args.evaluation_kind,
            kinds,
            manifest_hash,
            candidate_profile=profile,
        )
        publish_study(root, study)
        return _status(
            command,
            "ok",
            "STUDY_CREATED",
            **_study_fields(study),
            integrity="self_consistent",
            publication_state="committed",
        )
    if command == "trial-create":
        study = load_study(root)
        _require_current_candidate_manifest(study)
        material_root = (
            None
            if args.material_root is None
            else _path(args.material_root, "INVALID_MATERIAL_ROOT")
        )
        result = (
            None if args.result is None else _path(args.result, "INVALID_RESULT_PATH")
        )
        export = (
            None if args.export is None else _path(args.export, "INVALID_EXPORT_PATH")
        )
        pdf_export = (
            None
            if args.pdf_export is None
            else _path(args.pdf_export, "INVALID_PDF_EXPORT_PATH")
        )
        participant_evidence_path = (
            None
            if args.participant_evidence_path is None
            else _path(
                args.participant_evidence_path, "INVALID_PARTICIPANT_EVIDENCE_PATH"
            )
        )
        trial = create_trial(
            study,
            args.task_id,
            state=args.state,
            actions=args.action,
            elapsed_seconds=args.elapsed_seconds,
            manual_baseline_seconds=args.manual_baseline_seconds,
            control_rating=args.control_rating,
            structural_rewrite=args.structural_rewrite,
            citation_usable=args.citation_usable,
            disposition=args.disposition,
            failure_stage=args.failure_stage,
            failure_code=_failure_code(args.failure_code),
            improvement_reason=args.improvement_reason,
            material_root=material_root,
            result_path=result,
            export_path=export,
            pdf_export_path=pdf_export,
            participant_notes=args.participant_notes,
            participant_timestamp=args.participant_timestamp,
            participant_evidence_path=participant_evidence_path,
        )
        if (
            trial.result is not None
            and trial.result.call_observation != "observed_zero"
        ):
            raise _CliError("NONZERO_OFFLINE_CALLS")
        publish_trial(root, trial)
        return _status(
            command,
            "ok",
            "TRIAL_RECORDED",
            **_trial_fields(trial),
            integrity="self_consistent",
            publication_state="committed",
        )
    if command == "summary-create":
        study = load_study(root)
        _require_current_candidate_manifest(study)
        summary = aggregate_summary(study, _load_trials(root))
        publish_summary(root, summary)
        return _status(
            command,
            "ok",
            "SUMMARY_CREATED",
            **_summary_fields(summary),
            integrity="self_consistent",
            publication_state="committed",
        )
    if command == "check":
        if args.record == "study":
            if args.task_id is not None:
                raise _CliError("INVALID_ARGUMENTS")
            study = load_study(root)
            _require_current_candidate_manifest(study)
            return _status(
                command,
                "ok",
                "CHECKED",
                **_study_fields(study),
                integrity="self_consistent",
            )
        if args.record == "trial":
            if args.task_id is None:
                raise _CliError("INVALID_ARGUMENTS")
            study, trial = load_study(root), load_trial(root, args.task_id)
            _require_current_candidate_manifest(study)
            if not _matches_study(study, trial):
                raise _CliError("STUDY_MISMATCH")
            if not verify_trial(trial):
                raise _CliError("PARTICIPANT_EVIDENCE_DRIFT")
            return _status(
                command,
                "ok",
                "CHECKED",
                **_trial_fields(trial),
                integrity="self_consistent",
            )
        if args.task_id is not None:
            raise _CliError("INVALID_ARGUMENTS")
        study, trials, summary = (
            load_study(root),
            _load_trials(root),
            load_summary(root),
        )
        _require_current_candidate_manifest(study)
        if not verify_summary(study, trials, summary):
            raise _CliError("SUMMARY_MISMATCH")
        return _status(
            command,
            "ok",
            "CHECKED",
            **_summary_fields(summary),
            integrity="self_consistent",
        )
    if command == "preview":
        study, trials, summary = (
            load_study(root),
            _load_trials(root),
            load_summary(root),
        )
        _require_current_candidate_manifest(study)
        if not verify_summary(study, trials, summary):
            raise _CliError("SUMMARY_MISMATCH")
        return _status(
            command,
            "ok",
            "PREVIEW_READY",
            **_summary_fields(summary),
            integrity="self_consistent",
            preview_markdown=render_summary(summary),
        )
    raise _CliError()


def main(argv: list[str] | None = None) -> int:
    raw_args = sys.argv[1:] if argv is None else argv
    known = {"study-create", "trial-create", "summary-create", "check", "preview"}
    command = raw_args[0] if raw_args and raw_args[0] in known else "unknown"
    try:
        args = _build_parser().parse_args(raw_args)
        command = args.command
        status, exit_code = _run(args), 0
    except PublicationAttentionError as error:
        status = _status(
            command,
            "attention_required",
            error.code,
            publication_state="committed_needs_attention",
        )
        exit_code = 3
    except PublicationRollbackError as error:
        status = _status(
            command,
            "rejected",
            error.code,
            publication_state="rolled_back",
        )
        exit_code = 2
    except (UsabilityTrialError, _CliError) as error:
        status = _status(command, "rejected", getattr(error, "code", "REJECTED"))
        exit_code = 2
    except (OSError, TypeError, ValueError):
        status = _status(command, "rejected", "REJECTED")
        exit_code = 2
    sys.stdout.write(
        json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
