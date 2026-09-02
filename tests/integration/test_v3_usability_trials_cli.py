from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import scripts.run_v3_usability_trials as cli
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
    create_draft,
    generate_result,
    publish_new_file,
    render_export,
    render_pdf_export,
    serialize_session,
)
from backend.app.usability_trials import create_trial, load_study, serialize_trial

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_v3_usability_trials.py"
ENGINEERING_MANIFEST = (
    Path(__file__).parents[2] / "examples" / "v3-phase-2" / "engineering-manifest.json"
)
KINDS = (
    "plan",
    "plan",
    "plan",
    "report",
    "report",
    "report",
    "readme",
    "readme",
    "readme",
    "plan",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _status(
    completed: subprocess.CompletedProcess[str], expected: int = 0
) -> dict[str, object]:
    assert completed.returncode == expected
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    status = json.loads(completed.stdout)
    assert status["schema_version"] == "v3-usability-cli-status-v1"
    assert status["offline_calls"] == {"embedding": 0, "mcp": 0, "provider": 0}
    assert status["evidence_provenance"] == "external_self_consistent_unanchored"
    assert status["product_value_conclusion"] == "not_accepted"
    return status


@pytest.mark.parametrize(
    "candidate_profile",
    [
        pytest.param("projecttown-human-pdf-v4", id="v4"),
        pytest.param("projecttown-human-pdf-v5", id="v5"),
        pytest.param("projecttown-human-pdf-v6", id="v6"),
        pytest.param("projecttown-human-pdf-v7", id="v7"),
        pytest.param("projecttown-human-pdf-v8", id="v8"),
        pytest.param("projecttown-human-pdf-v9", id="v9"),
        pytest.param("projecttown-human-pdf-v10", id="v10"),
    ],
)
def test_pdf_profile_uses_only_matching_manifest(candidate_profile: str) -> None:
    kinds, manifest_hash = cli._candidate_manifest_for(
        "human_usability", candidate_profile
    )
    assert kinds == KINDS
    assert len(manifest_hash) == 64


def test_v6_cli_creates_trial_v3_failed_record_and_rejects_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v6-study"
    root.mkdir()
    study = _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            "v6-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            "projecttown-human-pdf-v6",
        )
    )
    assert study["candidate_profile"] == "projecttown-human-pdf-v6"
    command = _failed_args(root, "T001") + [
        "--participant-notes",
        "暂无",
        "--participant-timestamp",
        "2026-08-28T08:00:00Z",
    ]
    created = _status(_run(*command))
    assert created["trial_schema_version"] == "v3-usability-trial-v3"
    assert created["participant_notes_present"] is True
    assert created["participant_timestamp_present"] is True
    assert created["participant_evidence_path_present"] is False
    assert (
        _status(
            _run(
                "check",
                "--study-root",
                str(root),
                "--record",
                "trial",
                "--task-id",
                "T001",
            )
        )["code"]
        == "CHECKED"
    )
    assert _status(_run(*command), 2)["outcome"] == "rejected"


def test_v7_cli_creates_trial_v3_failed_record_and_rejects_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v7-study"
    root.mkdir()
    study = _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            "v7-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            "projecttown-human-pdf-v7",
        )
    )
    assert study["candidate_profile"] == "projecttown-human-pdf-v7"
    command = _failed_args(root, "T001") + [
        "--participant-notes",
        "暂无",
        "--participant-timestamp",
        "2026-08-28T08:00:00Z",
    ]
    created = _status(_run(*command))
    assert created["trial_schema_version"] == "v3-usability-trial-v3"
    assert (
        _status(
            _run(
                "check",
                "--study-root",
                str(root),
                "--record",
                "trial",
                "--task-id",
                "T001",
            )
        )["code"]
        == "CHECKED"
    )
    assert _status(_run(*command), 2)["outcome"] == "rejected"


@pytest.mark.parametrize(
    ("candidate_profile", "generator_version", "export_version", "renderer_version"),
    [
        (
            "projecttown-human-pdf-v7",
            "deterministic-grounded-plan-v6",
            "v3-material-pdf-export-v6",
            "projecttown-reportlab-pdf-v6",
        ),
        (
            "projecttown-human-pdf-v8",
            "deterministic-grounded-plan-v7",
            "v3-material-pdf-export-v7",
            "projecttown-reportlab-pdf-v7",
        ),
        (
            "projecttown-human-pdf-v9",
            "deterministic-grounded-plan-v8",
            "v3-material-pdf-export-v8",
            "projecttown-reportlab-pdf-v8",
        ),
        (
            "projecttown-human-pdf-v10",
            "deterministic-grounded-plan-v9",
            "v3-material-pdf-export-v9",
            "projecttown-reportlab-pdf-v9",
        ),
    ],
)
def test_v7_to_v9_cli_completed_trial_binds_pdf_and_rejects_drift_or_path_mismatch(
    tmp_path: Path,
    candidate_profile: str,
    generator_version: str,
    export_version: str,
    renderer_version: str,
) -> None:
    root = tmp_path / f"{candidate_profile}-completed-study"
    root.mkdir()
    _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            f"{candidate_profile}-completed-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            candidate_profile,
        )
    )
    material, result, pdf = _bound_pdf_result(
        tmp_path,
        "T001",
        "plan",
        generator_version=generator_version,
        export_version=export_version,
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
    )
    command = [
        "trial-create",
        "--study-root",
        str(root),
        "--task-id",
        "T001",
        "--state",
        "completed",
        "--action",
        "open_task",
        "--elapsed-seconds",
        "20",
        "--manual-baseline-seconds",
        "30",
        "--control-rating",
        "4",
        "--structural-rewrite",
        "false",
        "--citation-usable",
        "true",
        "--disposition",
        "retained",
        "--improvement-reason",
        "none",
        "--material-root",
        str(material),
        "--result",
        str(result),
        "--pdf-export",
        str(pdf),
        "--participant-notes",
        "private participant note",
        "--participant-timestamp",
        "2026-08-29T08:00:00Z",
        "--participant-evidence-path",
        str(pdf),
    ]
    created = _status(_run(*command))
    assert created["trial_schema_version"] == "v3-usability-trial-v3"
    assert created["participant_evidence_path_present"] is True
    assert "private participant note" not in json.dumps(created)
    payload = json.loads((root / "T001.json").read_text(encoding="utf-8"))
    assert (
        payload["presentation"]["pdf_export_version"],
        payload["presentation"]["pdf_renderer_version"],
    ) == (export_version, renderer_version)
    assert (
        _status(
            _run(
                "check",
                "--study-root",
                str(root),
                "--record",
                "trial",
                "--task-id",
                "T001",
            )
        )["code"]
        == "CHECKED"
    )
    assert _status(_run(*command), 2)["outcome"] == "rejected"

    alternate = pdf.with_name("v7-same-bytes-other-path.pdf")
    alternate.write_bytes(pdf.read_bytes())
    mismatch = list(command)
    mismatch[mismatch.index("--task-id") + 1] = "T002"
    mismatch[mismatch.index("--participant-evidence-path") + 1] = str(alternate)
    assert _status(_run(*mismatch), 2)["code"] == "PARTICIPANT_EVIDENCE_PATH_MISMATCH"

    pdf.write_bytes(b"v7-pdf-drift-before-create")
    drift = list(command)
    drift[drift.index("--task-id") + 1] = "T003"
    assert _status(_run(*drift), 2)["code"] == "INVALID_PDF_EXPORT"


def _study_command(root: Path, *, study_id: str = "phase2-study") -> list[str]:
    command = [
        "study-create",
        "--study-root",
        str(root),
        "--study-id",
        study_id,
        "--evaluation-kind",
        "synthetic_engineering_fixture",
    ]
    return command


def _create_study(
    root: Path, *, evaluation: str = "synthetic_engineering_fixture"
) -> dict[str, object]:
    command = _study_command(root)
    command[command.index("--evaluation-kind") + 1] = evaluation
    return _status(_run(*command))


def _failed_args(
    root: Path, task_id: str, *, failure_code: str = "invalid_input"
) -> list[str]:
    return [
        "trial-create",
        "--study-root",
        str(root),
        "--task-id",
        task_id,
        "--state",
        "workflow_failed",
        "--action",
        "open_task",
        "--action",
        "stop",
        "--elapsed-seconds",
        "10",
        "--manual-baseline-seconds",
        "20",
        "--control-rating",
        "3",
        "--disposition",
        "not_kept",
        "--improvement-reason",
        "workflow",
        "--failure-stage",
        "confirmation",
        "--failure-code",
        failure_code,
    ]


def _bound_result(tmp_path: Path, task_id: str, kind: str) -> tuple[Path, Path, Path]:
    material = tmp_path / "materials" / task_id
    outside = tmp_path / "outside" / task_id
    material.mkdir(parents=True)
    outside.mkdir(parents=True)
    source = "README.md" if kind == "readme" else "source.md"
    (material / source).write_text(
        f"# {task_id}\nunique material for {task_id}\n", encoding="utf-8"
    )
    draft = create_draft(
        material,
        [source],
        task=f"Phase 2 task {task_id}",
        artifact_kind=kind,  # type: ignore[arg-type]
        readme_target="README.md" if kind == "readme" else None,
        constraints={"trial_id": task_id},
    )
    result = generate_result(material, draft, draft.contract_hash)
    result_path, export_path = outside / "result.json", outside / "artifact.md"
    publish_new_file(material, result_path, serialize_session(result))
    publish_new_file(material, export_path, render_export(material, result))
    return material, result_path, export_path


def _bound_pdf_result(
    tmp_path: Path,
    task_id: str,
    kind: str,
    *,
    generator_version: str = "deterministic-grounded-plan-v2",
    export_version: str = "v3-material-pdf-export-v1",
    task: str | None = None,
) -> tuple[Path, Path, Path]:
    material = tmp_path / "materials" / task_id
    outside = (
        Path(tempfile.mkdtemp(prefix="projecttown-v10-pdf-trial-evidence-"))
        if generator_version == "deterministic-grounded-plan-v9"
        else tmp_path / "outside" / task_id
    )
    material.mkdir(parents=True)
    if not outside.exists():
        outside.mkdir(parents=True)
    source = "README.md" if kind == "readme" else "source.md"
    (material / source).write_text(
        f"# {task_id}\nunique material for {task_id}\n", encoding="utf-8"
    )
    constraints = {"trial_id": task_id}
    if generator_version == "deterministic-grounded-plan-v9":
        study_root = tmp_path / "v10-history-study"
        work_root = tmp_path / "v10-history-work"
        study_root.mkdir()
        work_root.mkdir()
        candidate = work_root / "v8-candidate.pdf"
        preview = work_root / "v8-preview.json"
        historical_result = work_root / "v8-result.json"
        final_snapshot = work_root / "v8-final.json"
        provenance = study_root / "approved-provenance.json"
        prior_study = study_root / "prior-study.json"
        for path in (
            candidate,
            preview,
            historical_result,
            final_snapshot,
            provenance,
            prior_study,
        ):
            path.write_text("synthetic v10 binding input\n", encoding="utf-8")
        manifest = material / "projecttown-trial-manifest-v10.json"
        manifest.write_text("{}\n", encoding="utf-8")
        evidence_root = outside / "evidence"
        evidence_root.mkdir()
        bindings: dict[str, str | Path] = {
            "candidate_path": candidate,
            "preview_record_path": preview,
            "manifest_path": manifest,
            "historical_result_json_path": historical_result,
            "approved_provenance_tuple_source": provenance,
            "prior_study_evidence_path": prior_study,
            "final_snapshot_path": final_snapshot,
            "working_directory": material,
            "material_source_root": material,
            "historical_study_root": study_root,
            "historical_work_root": work_root,
            "fresh_root": outside,
            "fresh_draft_path": outside / "fresh-draft.json",
            "fresh_result_output_path": outside / "result.json",
            "fresh_evidence_root": evidence_root,
            "runbook_version": "projecttown-human-pdf-v10",
            "verification_target_version": "projecttown-human-pdf-v8",
            "verification_target_id": "projecttown-v3-phase2-human-pdf-v8-20260829-001:T002",
            "verification_run_id": "v10-test-run",
            "candidate_profile": "projecttown-human-pdf-v8",
            "candidate_sha256": "1686e8e33ba39e0d25a554c8750e03781a68cea8a2205f911777b53eb3ecca68",
            "candidate_pdf_export_version": "v3-material-pdf-export-v7",
            "candidate_pdf_renderer_version": "projecttown-reportlab-pdf-v7",
            "candidate_expected_page_count": "4",
            "fresh_result_schema": "v3-material-result-session-v1",
            "planned_study_evidence_output": "<TO BIND BEFORE RUN>",
            "user_disposition_record_path": "<TO BIND BEFORE RUN>",
            "release_authorization_record_path": "<TO BIND BEFORE RUN>",
        }
        constraints = {
            "execution": "offline",
            "preserve_v1_v2_contracts": "true",
            **{
                f"run_binding_{key}": str(value.resolve())
                if isinstance(value, Path)
                else value
                for key, value in bindings.items()
            },
        }
    draft = create_draft(
        material,
        [source],
        task=task or f"Phase 2 task {task_id}",
        artifact_kind=kind,  # type: ignore[arg-type]
        readme_target="README.md" if kind == "readme" else None,
        constraints=constraints,
        generator_version=generator_version,
    )
    rendered = generate_result(material, draft, draft.contract_hash)
    result_path = outside / "result.json"
    publish_new_file(material, result_path, serialize_session(rendered))
    from backend.app.material_workflow import load_session

    pdf_path = Path(tempfile.mkdtemp(prefix="projecttown-v2-pdf-")) / "artifact.pdf"
    publish_new_file(
        material,
        pdf_path,
        render_pdf_export(
            material, load_session(material, result_path), export_version=export_version
        ),
    )
    return material, result_path, pdf_path


def _conflict_result(tmp_path: Path, task_id: str) -> tuple[Path, Path]:
    material = tmp_path / "conflicts" / task_id
    outside = tmp_path / "conflict-outside" / task_id
    material.mkdir(parents=True)
    outside.mkdir(parents=True)
    (material / "conflict.txt").write_text(
        "约束：mode=fast\n要求：mode=safe\n", encoding="utf-8"
    )
    draft = create_draft(
        material,
        ["conflict.txt"],
        task=f"Phase 2 conflict {task_id}",
        artifact_kind="plan",
        constraints={},
    )
    result = generate_result(material, draft, draft.contract_hash)
    assert result.state == "needs_user_decision"
    result_path = outside / "result.json"
    publish_new_file(material, result_path, serialize_session(result))
    return material, result_path


def _completed_args(
    root: Path,
    task_id: str,
    binding: tuple[Path, Path, Path],
    *,
    disposition: str,
    improvement: str,
) -> list[str]:
    material, result, export = binding
    command = [
        "trial-create",
        "--study-root",
        str(root),
        "--task-id",
        task_id,
        "--state",
        "completed",
        "--action",
        "open_task",
        "--action",
        "select_materials",
        "--action",
        "confirm_and_generate",
        "--action",
        "preview",
        "--action",
        "export_or_retain",
        "--elapsed-seconds",
        "20",
        "--manual-baseline-seconds",
        "30",
        "--control-rating",
        "4",
        "--structural-rewrite",
        "false",
        "--citation-usable",
        "true",
        "--disposition",
        disposition,
        "--improvement-reason",
        improvement,
        "--material-root",
        str(material),
        "--result",
        str(result),
    ]
    if disposition == "exported":
        command += ["--export", str(export)]
    return command


def _record_all_failed(root: Path) -> None:
    for index in range(1, 11):
        assert (
            _status(_run(*_failed_args(root, f"T{index:03d}")))["code"]
            == "TRIAL_RECORDED"
        )


def test_study_create_check_and_create_only(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    created = _create_study(root)
    assert created["code"] == "STUDY_CREATED"
    assert created["publication_state"] == "committed"
    assert (root / "study.json").is_file()
    assert (
        _status(_run("check", "--study-root", str(root), "--record", "study"))[
            "integrity"
        ]
        == "self_consistent"
    )
    before = (root / "study.json").read_bytes()
    assert _status(_run(*_study_command(root)), 2)["outcome"] == "rejected"
    assert (root / "study.json").read_bytes() == before


@pytest.mark.parametrize(
    "variant",
    [
        "bad_study_id",
        "bad_evaluation",
        "unexpected_artifact_kind",
    ],
)
def test_study_input_rejections_create_no_record(tmp_path: Path, variant: str) -> None:
    root = tmp_path / "study"
    root.mkdir()
    command = _study_command(root)
    if variant == "bad_study_id":
        command[command.index("--study-id") + 1] = "invalid id"
    elif variant == "bad_evaluation":
        command[command.index("--evaluation-kind") + 1] = "invalid"
    else:
        command += ["--artifact-kind", "plan"]
    assert _status(_run(*command), 2)["outcome"] == "rejected"
    assert not (root / "study.json").exists()


@pytest.mark.parametrize(
    "suffix",
    ["\\.", "\\nested\\..", ""],
    ids=["noncanonical_dot", "noncanonical_parent", "relative"],
)
def test_study_root_rejections_do_not_write(tmp_path: Path, suffix: str) -> None:
    root = tmp_path / "study"
    root.mkdir()
    raw_root = "study" if suffix == "" else str(root) + suffix
    command = _study_command(root)
    command[command.index("--study-root") + 1] = raw_root
    assert _status(_run(*command), 2)["code"] == "INVALID_STUDY_ROOT"
    assert not (root / "study.json").exists()


def test_study_bytes_are_deterministic_in_two_fresh_roots(tmp_path: Path) -> None:
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
        _status(_run(*_study_command(root, study_id="same-study")))
    assert (roots[0] / "study.json").read_bytes() == (
        roots[1] / "study.json"
    ).read_bytes()


def test_study_binds_exact_evaluation_manifest_without_exposing_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    status = _create_study(root, evaluation="human_usability")
    manifest = (
        Path(__file__).parents[2]
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest.json"
    )
    assert (
        status["candidate_manifest_hash"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert (
        "manifest"
        not in "".join(
            str(value)
            for key, value in status.items()
            if key != "candidate_manifest_hash"
        ).casefold()
    )


def test_pdf_candidate_profile_creates_v2_and_rejects_tampered_pdf(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pdf-study"
    root.mkdir()
    created = _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            "pdf-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            "projecttown-human-pdf-v2",
        )
    )
    assert created["candidate_profile"] == "projecttown-human-pdf-v2"
    material, result, pdf = _bound_pdf_result(tmp_path, "T001", "plan")
    command = [
        "trial-create",
        "--study-root",
        str(root),
        "--task-id",
        "T001",
        "--state",
        "completed",
        "--action",
        "open_task",
        "--action",
        "preview",
        "--elapsed-seconds",
        "20",
        "--manual-baseline-seconds",
        "30",
        "--control-rating",
        "4",
        "--structural-rewrite",
        "false",
        "--citation-usable",
        "true",
        "--disposition",
        "exported",
        "--improvement-reason",
        "none",
        "--material-root",
        str(material),
        "--result",
        str(result),
        "--pdf-export",
        str(pdf),
    ]
    assert _status(_run(*command))["code"] == "TRIAL_RECORDED"
    payload = json.loads((root / "T001.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "v3-usability-trial-v2"
    assert "pdf_bytes_hash" in payload["presentation"]
    command[command.index("--task-id") + 1] = "T002"
    command.extend(["--export", str(result.parent / "artifact.md")])
    assert _status(_run(*command), 2)["outcome"] == "rejected"
    assert not (root / "T002.json").exists()
    command = command[: command.index("--export")]
    pdf.write_bytes(b"wrong")
    command[command.index("--task-id") + 1] = "T003"
    assert _status(_run(*command), 2)["outcome"] == "rejected"
    assert not (root / "T003.json").exists()


def test_v5_cli_requires_and_redacts_participant_evidence(tmp_path: Path) -> None:
    root = tmp_path / "v5-study"
    root.mkdir()
    _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            "v5-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            "projecttown-human-pdf-v5",
        )
    )
    material, result, pdf = _bound_pdf_result(
        tmp_path,
        "T001",
        "plan",
        generator_version="deterministic-grounded-plan-v4",
        export_version="v3-material-pdf-export-v4",
    )
    command = [
        "trial-create",
        "--study-root",
        str(root),
        "--task-id",
        "T001",
        "--state",
        "completed",
        "--action",
        "open_task",
        "--elapsed-seconds",
        "20",
        "--manual-baseline-seconds",
        "30",
        "--control-rating",
        "4",
        "--structural-rewrite",
        "false",
        "--citation-usable",
        "true",
        "--disposition",
        "retained",
        "--improvement-reason",
        "none",
        "--material-root",
        str(material),
        "--result",
        str(result),
        "--pdf-export",
        str(pdf),
        "--participant-notes",
        "private note",
        "--participant-timestamp",
        "2026-08-27T08:00:00Z",
        "--participant-evidence-path",
        str(pdf),
    ]
    status = _status(_run(*command))
    assert status["trial_schema_version"] == "v3-usability-trial-v3"
    assert status["participant_evidence_path_present"] is True
    assert "private note" not in json.dumps(status)
    assert (
        _status(
            _run(
                "check",
                "--study-root",
                str(root),
                "--record",
                "trial",
                "--task-id",
                "T001",
            )
        )["code"]
        == "CHECKED"
    )
    for flag in (
        "--participant-notes",
        "--participant-timestamp",
        "--participant-evidence-path",
    ):
        rejected = list(command)
        rejected[rejected.index("--task-id") + 1] = "T002"
        index = rejected.index(flag)
        del rejected[index : index + 2]
        assert _status(_run(*rejected), 2)["code"] == "MISSING_PARTICIPANT_EVIDENCE"
    invalid_timestamp = list(command)
    invalid_timestamp[invalid_timestamp.index("--task-id") + 1] = "T002"
    invalid_timestamp[invalid_timestamp.index("--participant-timestamp") + 1] = (
        "2026-08-27T08:00:00"
    )
    assert (
        _status(_run(*invalid_timestamp), 2)["code"] == "INVALID_PARTICIPANT_TIMESTAMP"
    )
    long_notes = list(command)
    long_notes[long_notes.index("--task-id") + 1] = "T002"
    long_notes[long_notes.index("--participant-notes") + 1] = "x" * 2001
    assert _status(_run(*long_notes), 2)["code"] == "INVALID_PARTICIPANT_NOTES"
    alternate = (
        Path(tempfile.mkdtemp(prefix="projecttown-v5-cli-evidence-")) / "copy.pdf"
    )
    alternate.write_bytes(pdf.read_bytes())
    mismatch = list(command)
    mismatch[mismatch.index("--task-id") + 1] = "T002"
    mismatch[mismatch.index("--participant-evidence-path") + 1] = str(alternate)
    assert _status(_run(*mismatch), 2)["code"] == "PARTICIPANT_EVIDENCE_PATH_MISMATCH"
    assert _status(_run(*command), 2)["outcome"] == "rejected"


def test_v5_cli_summary_reloads_all_participant_evidence_without_leaking_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v5-summary-study"
    root.mkdir()
    _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            "v5-summary-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            "projecttown-human-pdf-v5",
        )
    )
    notes = "participant-private-notes"
    evidence_paths: list[str] = []
    for index, kind in enumerate(KINDS, start=1):
        task_id = f"T{index:03d}"
        material, result, pdf = _bound_pdf_result(
            tmp_path / "v5-summary",
            task_id,
            kind,
            generator_version="deterministic-grounded-plan-v4",
            export_version="v3-material-pdf-export-v4",
        )
        command = _completed_args(
            root,
            task_id,
            (material, result, pdf),
            disposition="retained" if index <= 7 else "not_kept",
            improvement="none" if index <= 7 else "artifact_quality",
        )
        command += [
            "--pdf-export",
            str(pdf),
            "--participant-notes",
            notes,
            "--participant-timestamp",
            "2026-08-27T08:00:00Z",
            "--participant-evidence-path",
            str(pdf),
        ]
        status = _status(_run(*command))
        assert status["trial_schema_version"] == "v3-usability-trial-v3"
        evidence_paths.append(str(pdf))
    summary = _status(_run("summary-create", "--study-root", str(root)))
    summary_record = (root / "summary.json").read_text(encoding="utf-8")
    assert json.loads(summary_record)["schema_version"] == "v3-usability-summary-v3"
    assert (
        summary["summary_gate_state"]
        == "criteria_met_unanchored_awaiting_user_acceptance"
    )
    assert (
        _status(_run("check", "--study-root", str(root), "--record", "summary"))["code"]
        == "CHECKED"
    )
    preview = _status(_run("preview", "--study-root", str(root)))
    assert (
        preview["summary_gate_state"]
        == "criteria_met_unanchored_awaiting_user_acceptance"
    )
    rendered = json.dumps(
        {"summary": summary, "preview": preview, "record": summary_record}
    )
    assert notes not in rendered
    assert all(path not in rendered for path in evidence_paths)


def test_visual_pdf_profile_uses_a_distinct_v3_manifest(tmp_path: Path) -> None:
    root = tmp_path / "pdf-visual-study"
    root.mkdir()
    status = _status(
        _run(
            "study-create",
            "--study-root",
            str(root),
            "--study-id",
            "pdf-visual-study",
            "--evaluation-kind",
            "human_usability",
            "--candidate-profile",
            "projecttown-human-pdf-v3",
        )
    )
    manifest = (
        Path(__file__).parents[2]
        / "examples"
        / "v3-phase-2"
        / "projecttown-trial-manifest-v3.json"
    )
    assert status["candidate_profile"] == "projecttown-human-pdf-v3"
    assert (
        status["candidate_manifest_hash"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )


def test_invalid_committed_manifest_rejects_without_study_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    invalid = tmp_path / "invalid-manifest.json"
    invalid.write_bytes(b'{"entries":[],"schema_version":"wrong"}\n')
    monkeypatch.setitem(cli._MANIFESTS, "synthetic_engineering_fixture", invalid)
    assert cli.main(_study_command(root)) == 2
    status = json.loads(capsys.readouterr().out)
    assert status["code"] == "INVALID_CANDIDATE_MANIFEST"
    assert not (root / "study.json").exists()


def test_reparse_candidate_manifest_rejects_without_study_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    linked = tmp_path / "manifest-link.json"
    try:
        linked.symlink_to(ENGINEERING_MANIFEST)
    except OSError as error:
        pytest.skip(f"symlink unavailable: {error}")
    monkeypatch.setitem(cli._MANIFESTS, "synthetic_engineering_fixture", linked)
    assert cli.main(_study_command(root)) == 2
    status = json.loads(capsys.readouterr().out)
    assert status["code"] == "CANDIDATE_MANIFEST_UNAVAILABLE"
    assert not (root / "study.json").exists()


def test_noncanonical_candidate_manifest_rejects_without_study_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    noncanonical = Path(
        str(ENGINEERING_MANIFEST.parent) + "\\..\\v3-phase-2\\engineering-manifest.json"
    )
    assert noncanonical.resolve(strict=True) != noncanonical
    monkeypatch.setitem(cli._MANIFESTS, "synthetic_engineering_fixture", noncanonical)
    assert cli.main(_study_command(root)) == 2
    status = json.loads(capsys.readouterr().out)
    assert status["code"] == "CANDIDATE_MANIFEST_UNAVAILABLE"
    assert not (root / "study.json").exists()


def test_candidate_manifest_drift_blocks_loaded_study_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root)
    changed = tmp_path / "changed-manifest.json"
    changed.write_bytes(ENGINEERING_MANIFEST.read_bytes() + b" ")
    monkeypatch.setitem(cli._MANIFESTS, "synthetic_engineering_fixture", changed)
    assert cli.main(["check", "--study-root", str(root), "--record", "study"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CANDIDATE_MANIFEST_MISMATCH"
    assert cli.main(_failed_args(root, "T001")) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CANDIDATE_MANIFEST_MISMATCH"
    assert not (root / "T001.json").exists()


def test_completed_exported_retained_and_trial_check(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root, evaluation="human_usability")
    first = _bound_result(tmp_path, "T001", "plan")
    second = _bound_result(tmp_path, "T002", "plan")
    exported = _status(
        _run(
            *_completed_args(
                root, "T001", first, disposition="exported", improvement="none"
            )
        )
    )
    retained = _status(
        _run(
            *_completed_args(
                root, "T002", second, disposition="retained", improvement="none"
            )
        )
    )
    assert (
        exported["call_observation"] == retained["call_observation"] == "observed_zero"
    )
    assert (root / "T001.json").is_file() and (root / "T002.json").is_file()
    checked = _status(
        _run(
            "check", "--study-root", str(root), "--record", "trial", "--task-id", "T001"
        )
    )
    assert checked["record_hash"] == exported["record_hash"]


@pytest.mark.parametrize(
    "state,code",
    [
        ("workflow_failed", "invalid_input"),
        ("abandoned", "user_stopped"),
        ("workflow_failed", "needs_user_decision"),
    ],
    ids=["failed", "abandoned", "manifest_conflict_mapping"],
)
def test_noncompleted_trials_and_manifest_conflict_mapping(
    tmp_path: Path, state: str, code: str
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root)
    args = _failed_args(root, "T001", failure_code=code)
    args[args.index("--state") + 1] = state
    status = _status(_run(*args))
    assert status["code"] == "TRIAL_RECORDED"
    payload = json.loads((root / "T001.json").read_text(encoding="utf-8"))
    assert payload["failure_code"] == (
        "unresolved_conflict" if code == "needs_user_decision" else code
    )


@pytest.mark.parametrize(
    "mutate",
    [
        ("--state", "completed"),
        ("--disposition", "retained"),
        ("--failure-code", "not-a-code"),
        ("--elapsed-seconds", "0"),
        ("--structural-rewrite", "yes"),
    ],
    ids=[
        "completed_without_result",
        "failed_retained",
        "invalid_enum",
        "invalid_integer",
        "invalid_boolean",
    ],
)
def test_trial_rejections_do_not_publish(
    tmp_path: Path, mutate: tuple[str, str]
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root)
    args = _failed_args(root, "T001")
    if mutate[0] in args:
        args[args.index(mutate[0]) + 1] = mutate[1]
    else:
        args += [mutate[0], mutate[1]]
    assert _status(_run(*args), 2)["outcome"] == "rejected"
    assert not (root / "T001.json").exists()


def test_stale_binding_export_mismatch_and_duplicate_trial_are_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root, evaluation="human_usability")
    binding = _bound_result(tmp_path, "T001", "plan")
    binding[0].joinpath("source.md").write_text("changed\n", encoding="utf-8")
    assert (
        _status(
            _run(
                *_completed_args(
                    root, "T001", binding, disposition="exported", improvement="none"
                )
            ),
            2,
        )["outcome"]
        == "rejected"
    )


def test_export_byte_mismatch_is_rejected_without_trial_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root, evaluation="human_usability")
    binding = _bound_result(tmp_path, "T001", "plan")
    binding[2].write_text("not the frozen export\n", encoding="utf-8")
    status = _status(
        _run(
            *_completed_args(
                root, "T001", binding, disposition="exported", improvement="none"
            )
        ),
        2,
    )
    assert status["outcome"] == "rejected"
    assert not (root / "T001.json").exists()


@pytest.mark.parametrize("missing", ["material", "result"])
def test_half_result_binding_is_rejected_without_trial_publication(
    tmp_path: Path, missing: str
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root, evaluation="human_usability")
    binding = _bound_result(tmp_path, "T001", "plan")
    args = _completed_args(
        root, "T001", binding, disposition="retained", improvement="none"
    )
    option = "--material-root" if missing == "material" else "--result"
    index = args.index(option)
    del args[index : index + 2]
    assert _status(_run(*args), 2)["outcome"] == "rejected"
    assert not (root / "T001.json").exists()
    assert not (root / "T001.json").exists()
    valid = _bound_result(tmp_path, "T001-valid", "plan")
    _status(
        _run(
            *_completed_args(
                root, "T001", valid, disposition="exported", improvement="none"
            )
        )
    )
    assert (
        _status(
            _run(
                *_completed_args(
                    root, "T001", valid, disposition="exported", improvement="none"
                )
            ),
            2,
        )["outcome"]
        == "rejected"
    )


def test_summary_missing_then_synthetic_engineering_only_and_preview(
    tmp_path: Path,
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root)
    assert (
        _status(_run("summary-create", "--study-root", str(root)), 2)["outcome"]
        == "rejected"
    )
    _record_all_failed(root)
    summary = _status(_run("summary-create", "--study-root", str(root)))
    assert summary["summary_gate_state"] == "engineering_only"
    checked = _status(_run("check", "--study-root", str(root), "--record", "summary"))
    preview = _status(_run("preview", "--study-root", str(root)))
    assert checked["summary_hash"] == summary["summary_hash"]
    assert "accepted" not in str(preview["preview_markdown"]).casefold()


def test_synthetic_summary_is_deterministic_in_two_fresh_roots(tmp_path: Path) -> None:
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
        _create_study(root)
        _record_all_failed(root)
        _status(_run("summary-create", "--study-root", str(root)))
    assert (roots[0] / "summary.json").read_bytes() == (
        roots[1] / "summary.json"
    ).read_bytes()


def test_engineering_manifest_mixed_outcomes_bind_to_engineering_only_summary(
    tmp_path: Path,
) -> None:
    manifest = json.loads(ENGINEERING_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    assert [entry["id"] for entry in entries] == [f"T{i:03d}" for i in range(1, 11)]
    assert (
        sum(
            entry["synthetic_rating"].get("adoptable_without_structural_rewrite")
            is True
            for entry in entries
        )
        == 7
    )
    assert (
        sum(
            entry["synthetic_rating"].get("disposition")
            == "structural_rewrite_not_kept"
            for entry in entries
        )
        == 1
    )
    assert (
        sum(
            entry["synthetic_rating"].get("outcome") == "workflow_failed"
            for entry in entries
        )
        == 1
    )
    assert (
        sum(
            entry["synthetic_rating"].get("outcome") == "abandoned" for entry in entries
        )
        == 1
    )

    root = tmp_path / "study"
    root.mkdir()
    _create_study(root)
    for entry in entries:
        task_id = entry["id"]
        profile = entry["synthetic_rating"]
        outcome = profile["outcome"]
        if outcome == "completed":
            binding = _bound_result(tmp_path, task_id, entry["artifact_kind"])
            adopted = profile.get("adoptable_without_structural_rewrite") is True
            args = _completed_args(
                root,
                task_id,
                binding,
                disposition="retained" if adopted else "not_kept",
                improvement="none" if adopted else "artifact_quality",
            )
            if not adopted:
                args[args.index("--structural-rewrite") + 1] = "true"
            _status(_run(*args))
        elif outcome == "workflow_failed":
            material, result = _conflict_result(tmp_path, task_id)
            args = _failed_args(root, task_id, failure_code="needs_user_decision")
            args += ["--material-root", str(material), "--result", str(result)]
            _status(_run(*args))
        else:
            args = _failed_args(root, task_id, failure_code="user_stopped")
            args[args.index("--state") + 1] = "abandoned"
            _status(_run(*args))
    summary = _status(_run("summary-create", "--study-root", str(root)))
    assert summary["summary_gate_state"] == "engineering_only"
    metrics = summary["metrics"]
    assert metrics["completed"] == 8
    assert metrics["adoptable"] == 7
    assert metrics["calls_observed_zero"] == 9
    assert metrics["citations_complete"] == 9
    assert metrics["blockers"] == [
        ["confirmation:unresolved_conflict", 1],
        ["confirmation:user_stopped", 1],
    ]
    projections = json.loads((root / "summary.json").read_text(encoding="utf-8"))[
        "projections"
    ]
    by_task = {projection["task_id"]: projection for projection in projections}
    assert by_task["T003"]["failure_code"] == "unresolved_conflict"
    assert by_task["T007"]["structural_rewrite"] is True
    assert by_task["T010"]["state"] == "abandoned"


@pytest.mark.parametrize(
    "adoptable", [7, 6], ids=["human_7_of_10_awaiting", "human_6_of_10_not_met"]
)
def test_human_summary_gate_is_unanchored_and_never_accepted(
    tmp_path: Path, adoptable: int
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root, evaluation="human_usability")
    for index, kind in enumerate(KINDS, start=1):
        task_id = f"T{index:03d}"
        binding = _bound_result(tmp_path, task_id, kind)
        good = index <= adoptable
        status = _status(
            _run(
                *_completed_args(
                    root,
                    task_id,
                    binding,
                    disposition="retained" if good else "not_kept",
                    improvement="none" if good else "artifact_quality",
                )
            )
        )
        assert status["product_value_conclusion"] == "not_accepted"
    summary = _status(_run("summary-create", "--study-root", str(root)))
    expected = (
        "criteria_met_unanchored_awaiting_user_acceptance"
        if adoptable == 7
        else "criteria_not_met"
    )
    assert summary["summary_gate_state"] == expected
    assert summary["product_value_conclusion"] == "not_accepted"


def test_summary_check_detects_current_trial_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    _create_study(root)
    _record_all_failed(root)
    _status(_run("summary-create", "--study-root", str(root)))
    study = load_study(root)
    replacement = create_trial(
        study,
        "T001",
        state="workflow_failed",
        failure_stage="generation",
        failure_code="unexpected_error",
        disposition="not_kept",
        improvement_reason="workflow",
    )
    (root / "T001.json").write_bytes(serialize_trial(replacement))
    assert (
        _status(_run("check", "--study-root", str(root), "--record", "summary"), 2)[
            "code"
        ]
        == "SUMMARY_MISMATCH"
    )


def test_status_does_not_expose_local_root_or_acceptance_claim(tmp_path: Path) -> None:
    root = tmp_path / "study-root"
    root.mkdir()
    completed = _run(*_study_command(root))
    status = _status(completed)
    assert str(root).casefold() not in completed.stdout.casefold()
    assert status["product_value_conclusion"] == "not_accepted"


@pytest.mark.parametrize(
    ("error", "exit_code", "outcome", "publication_state"),
    [
        (PublicationRollbackError(), 2, "rejected", "rolled_back"),
        (
            PublicationAttentionError(),
            3,
            "attention_required",
            "committed_needs_attention",
        ),
    ],
)
def test_publication_states_are_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    exit_code: int,
    outcome: str,
    publication_state: str,
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    monkeypatch.setattr(
        cli, "publish_study", lambda *_args: (_ for _ in ()).throw(error)
    )
    assert cli.main(_study_command(root)) == exit_code
    status = json.loads(capsys.readouterr().out)
    assert status["outcome"] == outcome
    assert status["publication_state"] == publication_state
