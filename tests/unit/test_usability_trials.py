"""Synthetic, offline tests for the Phase 2 study/trial protocol."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

import backend.app.usability_trials as trials_module
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    publish_new_file,
    render_pdf_export,
    serialize_session,
)
from backend.app.usability_trials import (
    MAX_RECORD_BYTES,
    UsabilityTrialError,
    aggregate_summary,
    create_study,
    create_trial,
    load_study,
    load_summary,
    load_trial,
    parse_study_bytes,
    parse_trial_bytes,
    publish_study,
    publish_summary,
    publish_trial,
    serialize_study,
    serialize_trial,
    verify_summary,
    verify_trial,
)

KINDS = (
    "plan",
    "plan",
    "report",
    "report",
    "readme",
    "readme",
    "plan",
    "report",
    "readme",
    "plan",
)


def _v10_binding_constraints(
    tmp_path: Path, material: Path, fresh_root: Path
) -> dict[str, str]:
    """Create only synthetic local binding inputs required by the v10 validator."""
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
    evidence_root = fresh_root / "evidence"
    evidence_root.mkdir()
    bindings = {
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
        "fresh_root": fresh_root,
        "fresh_draft_path": fresh_root / "fresh-draft.json",
        "fresh_result_output_path": fresh_root / "result.json",
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
    return {
        "execution": "offline",
        "preserve_v1_v2_contracts": "true",
        **{
            f"run_binding_{key}": str(value.resolve())
            if isinstance(value, Path)
            else value
            for key, value in bindings.items()
        },
    }


def test_v9_profile_is_additively_bound_to_only_its_v8_presentation() -> None:
    assert trials_module.pdf_presentation_pair_for_profile(
        "projecttown-human-pdf-v9"
    ) == ("v3-material-pdf-export-v8", "projecttown-reportlab-pdf-v8")
    assert trials_module.pdf_presentation_pair_for_profile(
        "projecttown-human-pdf-v8"
    ) == ("v3-material-pdf-export-v7", "projecttown-reportlab-pdf-v7")


def test_v10_profile_is_additively_bound_to_only_its_v9_presentation() -> None:
    assert trials_module.pdf_presentation_pair_for_profile(
        "projecttown-human-pdf-v10"
    ) == ("v3-material-pdf-export-v9", "projecttown-reportlab-pdf-v9")
    assert trials_module.pdf_presentation_pair_for_profile(
        "projecttown-human-pdf-v9"
    ) == ("v3-material-pdf-export-v8", "projecttown-reportlab-pdf-v8")


def _study(kind: str = "synthetic_engineering_fixture"):
    return create_study("synthetic-study-1", kind, KINDS, "a" * 64)  # type: ignore[arg-type]


def _failed(study, task_id: str):
    return create_trial(
        study,
        task_id,
        state="workflow_failed",
        failure_stage="draft",
        failure_code="invalid_input",
        disposition="not_kept",
        improvement_reason="workflow",
    )


def _result(
    tmp_path: Path, *, generator_version: str = "deterministic-grounded-plan-v2"
):
    root = tmp_path / "material"
    root.mkdir(parents=True)
    marker = tmp_path.name
    (root / "source.md").write_text(
        f"# synthetic\nverified {marker}\n", encoding="utf-8"
    )
    draft = create_draft(
        root,
        ["source.md"],
        task=f"synthetic {marker}",
        artifact_kind="plan",
        generator_version=generator_version,
    )
    result = generate_result(root, draft, draft.contract_hash)
    output = tmp_path / "external"
    output.mkdir(parents=True)
    path = output / "result.json"
    publish_new_file(root, path, serialize_session(result))
    export = output / "artifact.md"
    publish_new_file(
        root, export, result.artifact_markdown.rstrip("\n").encode() + b"\n"
    )
    return root, path, export


def _pdf_result(
    tmp_path: Path,
    artifact_kind: str = "plan",
    *,
    export_version: str = "v3-material-pdf-export-v1",
    generator_version: str = "deterministic-grounded-plan-v2",
):
    if artifact_kind == "plan":
        root, result, export = _result(tmp_path, generator_version=generator_version)
    else:
        root = tmp_path / "material"
        root.mkdir(parents=True)
        source = "README.md" if artifact_kind == "readme" else "source.md"
        marker = tmp_path.name
        (root / source).write_text(
            f"# synthetic\nverified evidence {marker}\n", encoding="utf-8"
        )
        draft = create_draft(
            root,
            [source],
            task=f"synthetic {artifact_kind} {marker}",
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            readme_target="README.md" if artifact_kind == "readme" else None,
            generator_version=generator_version,
        )
        rendered = generate_result(root, draft, draft.contract_hash)
        external = tmp_path / "external"
        external.mkdir()
        result = external / "result.json"
        export = external / "artifact.md"
        publish_new_file(root, result, serialize_session(rendered))
        publish_new_file(root, export, rendered.artifact_markdown.encode("utf-8"))
    session = trials_module.load_session(root, result)
    pdf = Path(tempfile.mkdtemp(prefix="projecttown-v2-pdf-")) / "artifact.pdf"
    publish_new_file(
        root, pdf, render_pdf_export(root, session, export_version=export_version)
    )
    return root, result, export, pdf


def _completed(study, task_id: str, root: Path, result: Path, export: Path, **updates):
    defaults = {
        "state": "completed",
        "actions": (
            "open_task",
            "select_materials",
            "confirm_and_generate",
            "preview",
            "export_or_retain",
        ),
        "elapsed_seconds": 20,
        "manual_baseline_seconds": 30,
        "control_rating": 4,
        "structural_rewrite": False,
        "citation_usable": True,
        "disposition": "exported",
        "material_root": root,
        "result_path": result,
        "export_path": export,
    }
    defaults.update(updates)
    if defaults["disposition"] != "exported":
        defaults["export_path"] = None
    return create_trial(study, task_id, **defaults)


def test_valid_study_distribution_and_direct_child_roundtrip(tmp_path: Path) -> None:
    study = _study()
    root = tmp_path / "study"
    root.mkdir()
    publish_study(root, study)
    assert load_study(root) == study
    assert b"source" not in serialize_study(study)
    with pytest.raises(UsabilityTrialError):
        create_study(
            "bad id with space", "synthetic_engineering_fixture", KINDS, "a" * 64
        )
    with pytest.raises(UsabilityTrialError):
        create_study(
            "only-plan", "synthetic_engineering_fixture", ("plan",) * 10, "a" * 64
        )


def test_completed_exported_and_retained_bind_real_phase1_result(
    tmp_path: Path,
) -> None:
    study = _study()
    material, result, export = _result(tmp_path)
    exported = _completed(study, "T001", material, result, export)
    retained = _completed(
        study, "T002", material, result, export, disposition="retained"
    )
    assert (
        exported.result is not None
        and exported.result.call_observation == "observed_zero"
    )
    assert retained.retained_result_bytes_hash == retained.result.result_bytes_hash


def test_v2_pdf_trial_requires_exact_user_pdf_and_v2_summary(tmp_path: Path) -> None:
    study = create_study(
        "human-pdf-study",
        "human_usability",
        KINDS,
        "b" * 64,
        candidate_profile="projecttown-human-pdf-v2",
    )
    assert study.schema_version == "v3-usability-study-v2"
    bindings = [
        _pdf_result(tmp_path / f"pdf-{index}", KINDS[index - 1])
        for index in range(1, 11)
    ]
    records = [
        create_trial(
            study,
            f"T{index:03d}",
            state="completed",
            actions=("open_task", "preview", "export_or_retain"),
            elapsed_seconds=20,
            manual_baseline_seconds=30,
            control_rating=4,
            structural_rewrite=False,
            citation_usable=True,
            disposition="retained",
            improvement_reason="none",
            material_root=material,
            result_path=result,
            pdf_export_path=pdf,
        )
        for index, (material, result, _export, pdf) in enumerate(bindings, start=1)
    ]
    assert all(record.schema_version == "v3-usability-trial-v2" for record in records)
    assert all(record.presentation is not None for record in records)
    summary = aggregate_summary(study, records)
    assert summary.schema_version == "v3-usability-summary-v2"
    assert summary.gate_state == "criteria_met_unanchored_awaiting_user_acceptance"
    assert (
        trials_module.parse_summary_bytes(trials_module.serialize_summary(summary))
        == summary
    )
    tampered = bindings[0][3]
    tampered.write_bytes(b"%PDF-not-the-frozen-render")
    with pytest.raises(UsabilityTrialError):
        create_trial(
            study,
            "T001",
            state="completed",
            actions=("open_task",),
            elapsed_seconds=20,
            manual_baseline_seconds=30,
            control_rating=4,
            structural_rewrite=False,
            citation_usable=True,
            disposition="not_kept",
            improvement_reason="artifact_quality",
            material_root=bindings[0][0],
            result_path=bindings[0][1],
            pdf_export_path=tampered,
        )


def test_v5_trial_binds_canonical_participant_evidence_and_hash(tmp_path: Path) -> None:
    study = create_study(
        "human-pdf-v5-study",
        "human_usability",
        KINDS,
        "e" * 64,
        candidate_profile="projecttown-human-pdf-v5",
    )
    material, result, _export, pdf = _pdf_result(
        tmp_path / "v5",
        export_version="v3-material-pdf-export-v4",
        generator_version="deterministic-grounded-plan-v4",
    )
    trial = create_trial(
        study,
        "T001",
        state="completed",
        actions=("open_task",),
        elapsed_seconds=20,
        manual_baseline_seconds=30,
        control_rating=4,
        structural_rewrite=False,
        citation_usable=True,
        disposition="retained",
        improvement_reason="none",
        material_root=material,
        result_path=result,
        pdf_export_path=pdf,
        participant_notes="\u6682\u65e0\r\n\u5df2\u9605\u8bfb",
        participant_timestamp="2026-08-27T08:00:00+00:00",
        participant_evidence_path=pdf,
    )
    assert trial.schema_version == "v3-usability-trial-v3"
    assert trial.participant_notes == "\u6682\u65e0\n\u5df2\u9605\u8bfb"
    assert trial.participant_timestamp.endswith("Z")
    assert verify_trial(trial)
    assert parse_trial_bytes(serialize_trial(trial)) == trial
    changed = trial.model_copy(update={"participant_notes": "different"})
    assert not verify_trial(changed)
    pdf.write_bytes(b"participant-evidence-drift")
    assert not verify_trial(trial)
    with pytest.raises(UsabilityTrialError):
        create_trial(
            study,
            "T002",
            state="completed",
            actions=("open_task",),
            elapsed_seconds=20,
            manual_baseline_seconds=30,
            control_rating=4,
            structural_rewrite=False,
            citation_usable=True,
            disposition="not_kept",
            improvement_reason="artifact_quality",
            material_root=material,
            result_path=result,
            pdf_export_path=pdf,
            participant_notes="\u0000",
            participant_timestamp="2026-08-27T08:00:00",
            participant_evidence_path=pdf,
        )


def test_v5_summary_projects_evidence_presence_without_private_values(
    tmp_path: Path,
) -> None:
    study = create_study(
        "human-pdf-v5-summary",
        "human_usability",
        KINDS,
        "f" * 64,
        candidate_profile="projecttown-human-pdf-v5",
    )
    records = []
    for index, kind in enumerate(KINDS, start=1):
        material, result, _export, pdf = _pdf_result(
            tmp_path / f"v5-{index}",
            kind,
            export_version="v3-material-pdf-export-v4",
            generator_version="deterministic-grounded-plan-v4",
        )
        records.append(
            create_trial(
                study,
                f"T{index:03d}",
                state="completed",
                actions=("open_task",),
                elapsed_seconds=20,
                manual_baseline_seconds=30,
                control_rating=4,
                structural_rewrite=False,
                citation_usable=True,
                disposition="retained",
                improvement_reason="none",
                material_root=material,
                result_path=result,
                pdf_export_path=pdf,
                participant_notes="\u6682\u65e0",
                participant_timestamp="2026-08-27T08:00:00Z",
                participant_evidence_path=pdf,
            )
        )
    summary = aggregate_summary(study, records)
    assert summary.schema_version == "v3-usability-summary-v3"
    assert summary.gate_state == "criteria_met_unanchored_awaiting_user_acceptance"
    assert all(item.participant_evidence_path_present for item in summary.projections)
    assert "\u6682\u65e0" not in trials_module.serialize_summary(summary).decode(
        "utf-8"
    )
    raw = summary.model_dump(mode="json")
    raw["projections"][0]["participant_notes_present"] = False
    raw["summary_hash"] = trials_module._hash(
        "projecttown/v3/usability-summary/v3",
        {key: value for key, value in raw.items() if key != "summary_hash"},
    )
    with pytest.raises(UsabilityTrialError) as error:
        trials_module.parse_summary_bytes(trials_module._canonical_json(raw))
    assert error.value.code == "INVALID_SUMMARY"
    v2_mixed = summary.model_dump(mode="json")
    v2_mixed["schema_version"] = "v3-usability-summary-v2"
    for projection in v2_mixed["projections"]:
        projection.pop("participant_notes_present")
        projection.pop("participant_timestamp_present")
        projection.pop("participant_evidence_path_present")
    v2_mixed["summary_hash"] = trials_module._hash(
        "projecttown/v3/usability-summary/v2",
        {key: value for key, value in v2_mixed.items() if key != "summary_hash"},
    )
    with pytest.raises(UsabilityTrialError) as error:
        trials_module.parse_summary_bytes(trials_module._canonical_json(v2_mixed))
    assert error.value.code == "STUDY_MISMATCH"
    v4_study = create_study(
        "human-pdf-v4-summary-mix",
        "human_usability",
        KINDS,
        "1" * 64,
        candidate_profile="projecttown-human-pdf-v4",
    )
    v3_mixed = summary.model_dump(mode="json")
    v3_mixed["study"] = v4_study.model_dump(mode="json")
    v3_mixed["summary_hash"] = trials_module._hash(
        "projecttown/v3/usability-summary/v3",
        {key: value for key, value in v3_mixed.items() if key != "summary_hash"},
    )
    with pytest.raises(UsabilityTrialError) as error:
        trials_module.parse_summary_bytes(trials_module._canonical_json(v3_mixed))
    assert error.value.code == "STUDY_MISMATCH"


def test_v5_participant_evidence_tamper_state_and_path_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise C--F without relying on a CLI status-only check."""
    study = create_study(
        "human-pdf-v5-contract",
        "human_usability",
        KINDS,
        "c" * 64,
        candidate_profile="projecttown-human-pdf-v5",
    )
    material, result, _export, pdf = _pdf_result(
        tmp_path / "bound",
        export_version="v3-material-pdf-export-v4",
        generator_version="deterministic-grounded-plan-v4",
    )
    common = {
        "actions": ("open_task",),
        "elapsed_seconds": 20,
        "manual_baseline_seconds": 30,
        "control_rating": 4,
        "structural_rewrite": False,
        "citation_usable": True,
        "disposition": "retained",
        "improvement_reason": "none",
        "participant_notes": "\u6682\u65e0",
        "participant_timestamp": "2026-08-27T08:00:00Z",
    }
    completed = create_trial(
        study,
        "T001",
        state="completed",
        material_root=material,
        result_path=result,
        pdf_export_path=pdf,
        participant_evidence_path=pdf,
        **common,
    )
    assert completed.schema_version == "v3-usability-trial-v3"
    raw = completed.model_dump(mode="json")
    for field, value in (
        ("participant_notes", "changed"),
        ("participant_timestamp", "2026-08-27T09:00:00Z"),
        ("participant_evidence_path", str(tmp_path / "other.pdf")),
    ):
        tampered = dict(raw)
        tampered[field] = value
        with pytest.raises(UsabilityTrialError) as error:
            parse_trial_bytes(trials_module._canonical_json(tampered))
        assert error.value.code == "INVALID_RECORD_HASH"
    rehashed = dict(raw)
    rehashed["participant_timestamp"] = "2026-08-27T08:00:00"
    rehashed["record_hash"] = trials_module._hash(
        "projecttown/v3/usability-trial/v3",
        {key: value for key, value in rehashed.items() if key != "record_hash"},
    )
    with pytest.raises(UsabilityTrialError) as error:
        parse_trial_bytes(trials_module._canonical_json(rehashed))
    assert error.value.code == "INVALID_RECORD"
    for state, task_id in (("workflow_failed", "T002"), ("abandoned", "T003")):
        trial = create_trial(
            study,
            task_id,
            state=state,
            failure_stage="draft",
            failure_code="invalid_input",
            **{
                **common,
                "disposition": "not_kept",
                "improvement_reason": "workflow",
                "structural_rewrite": None,
                "citation_usable": None,
            },
        )
        assert trial.presentation is None
        assert trial.participant_evidence_path is None
        assert verify_trial(trial)
    with pytest.raises(UsabilityTrialError) as error:
        create_trial(
            study,
            "T002",
            state="completed",
            material_root=material,
            result_path=result,
            pdf_export_path=pdf,
            **common,
        )
    assert error.value.code == "MISSING_PARTICIPANT_EVIDENCE"
    with pytest.raises(UsabilityTrialError) as error:
        create_trial(
            study,
            "T002",
            state="completed",
            material_root=material,
            result_path=result,
            pdf_export_path=pdf,
            participant_evidence_path=Path("relative.pdf"),
            **common,
        )
    assert error.value.code == "INVALID_PARTICIPANT_EVIDENCE_PATH"
    copy = Path(tempfile.mkdtemp(prefix="projecttown-v5-evidence-")) / "copy.pdf"
    copy.write_bytes(pdf.read_bytes())
    with pytest.raises(UsabilityTrialError) as error:
        create_trial(
            study,
            "T002",
            state="completed",
            material_root=material,
            result_path=result,
            pdf_export_path=pdf,
            participant_evidence_path=copy,
            **common,
        )
    assert error.value.code == "PARTICIPANT_EVIDENCE_PATH_MISMATCH"
    for path in (
        tmp_path / "missing.pdf",
        tmp_path,
        Path(__file__).resolve(),
        material / "inside.pdf",
    ):
        with pytest.raises(UsabilityTrialError) as error:
            create_trial(
                study,
                "T002",
                state="completed",
                material_root=material,
                result_path=result,
                pdf_export_path=pdf,
                participant_evidence_path=path,
                **common,
            )
        assert error.value.code == "INVALID_PARTICIPANT_EVIDENCE_PATH"
    monkeypatch.setattr(trials_module, "is_reparse", lambda _metadata: True)
    with pytest.raises(UsabilityTrialError) as error:
        trials_module._verify_participant_evidence(completed)
    assert error.value.code == "INVALID_PARTICIPANT_EVIDENCE_PATH"
    pdf.unlink()
    assert not verify_trial(completed)


def test_v5_summary_and_profile_protocol_fail_closed(tmp_path: Path) -> None:
    """A rehashed semantic forgery must not cross the v5 profile boundary."""
    v5 = create_study(
        "v5-mix",
        "human_usability",
        KINDS,
        "d" * 64,
        candidate_profile="projecttown-human-pdf-v5",
    )
    old = create_study(
        "v4-mix",
        "human_usability",
        KINDS,
        "e" * 64,
        candidate_profile="projecttown-human-pdf-v4",
    )
    material, result, _export, pdf = _pdf_result(
        tmp_path / "mix",
        export_version="v3-material-pdf-export-v4",
        generator_version="deterministic-grounded-plan-v4",
    )
    kwargs = {
        "state": "completed",
        "actions": ("open_task",),
        "elapsed_seconds": 20,
        "manual_baseline_seconds": 30,
        "control_rating": 4,
        "structural_rewrite": False,
        "citation_usable": True,
        "disposition": "retained",
        "improvement_reason": "none",
        "material_root": material,
        "result_path": result,
        "pdf_export_path": pdf,
    }
    v5_trial = create_trial(
        v5,
        "T001",
        participant_notes="\u6682\u65e0",
        participant_timestamp="2026-08-27T08:00:00Z",
        participant_evidence_path=pdf,
        **kwargs,
    )
    assert v5_trial.schema_version == "v3-usability-trial-v3"
    old_material, old_result, _old_export, old_pdf = _pdf_result(
        tmp_path / "old",
        export_version="v3-material-pdf-export-v3",
        generator_version="deterministic-grounded-plan-v3",
    )
    old_trial = create_trial(
        old,
        "T001",
        **{
            **kwargs,
            "material_root": old_material,
            "result_path": old_result,
            "pdf_export_path": old_pdf,
        },
    )
    assert old_trial.schema_version == "v3-usability-trial-v2"
    with pytest.raises(UsabilityTrialError) as error:
        create_trial(
            old,
            "T002",
            participant_notes="\u6682\u65e0",
            participant_timestamp="2026-08-27T08:00:00Z",
            **kwargs,
        )
    assert error.value.code == "PARTICIPANT_EVIDENCE_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\u6682\u65e0", "\u6682\u65e0"),
        ("a\r\nb", "a\nb"),
        ("e\u0301", "\u00e9"),
        ("x" * 2000, "x" * 2000),
    ],
)
def test_participant_notes_canonical_boundaries(raw: str, expected: str) -> None:
    assert trials_module._normalise_participant_notes(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "x" * 2001, "a\x00", "a\x01", "a\u202e"])
def test_participant_notes_reject_unsafe_content(raw: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        trials_module._normalise_participant_notes(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-27T08:00:00Z", "2026-08-27T08:00:00Z"),
        ("2026-08-27T08:00:00+00:00", "2026-08-27T08:00:00Z"),
        ("2026-08-27T08:00:00+08:00", "2026-08-27T08:00:00+08:00"),
    ],
)
def test_participant_timestamp_canonical_forms(raw: str, expected: str) -> None:
    assert trials_module._normalise_participant_timestamp(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-27T08:00:00",
        "2026-08-27 08:00:00Z",
        "2026-08-27T08:00:00z",
        "2026-08-27T08:00:00.1Z",
        "2026-08-27T08:00:00-00:00",
        "2026-02-30T08:00:00Z",
        "2026-08-27T08:00:00+14:01",
    ],
)
def test_participant_timestamp_rejects_ambiguous_forms(raw: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        trials_module._normalise_participant_timestamp(raw)


def test_trial_v2_canonical_bytes_and_hash_golden(tmp_path: Path) -> None:
    """V3 additions must not change this fixed V2 canonical record."""
    study = create_study(
        "v2-golden",
        "human_usability",
        KINDS,
        "a" * 64,
        candidate_profile="projecttown-human-pdf-v2",
    )
    material, result, _export, pdf = _pdf_result(tmp_path / "golden")
    trial = create_trial(
        study,
        "T001",
        state="completed",
        actions=("open_task",),
        elapsed_seconds=20,
        manual_baseline_seconds=30,
        control_rating=4,
        structural_rewrite=False,
        citation_usable=True,
        disposition="retained",
        improvement_reason="none",
        material_root=material,
        result_path=result,
        pdf_export_path=pdf,
    )
    payload = serialize_trial(trial)
    assert (
        hashlib.sha256(payload).hexdigest()
        == "0768c064892fea41f79ad6cb5a135776cf39afc41ddef0eb251d627066a6ecb7"
    )
    assert (
        trial.record_hash
        == "b1133d742165690e5062cf156b81e01a44b999be65f603ea89f54f902a61fcfb"
    )


def test_trial_v1_canonical_bytes_and_hash_golden(tmp_path: Path) -> None:
    """The additive V3 protocol must not alter a fixed V1 record."""
    study = _study()
    material, result, export = _result(tmp_path / "v1-golden")
    trial = _completed(study, "T001", material, result, export)
    payload = serialize_trial(trial)
    assert (
        hashlib.sha256(payload).hexdigest()
        == "a02bd28376c1afa902f313da2a975e669781c76a13a358fa6a8ca12f6ad534a4"
    )
    assert (
        trial.record_hash
        == "63ba486c1408af7757b6ff26e34f994e895fa2e03d328ad328411cf95fc37de7"
    )
    assert parse_trial_bytes(payload) == trial


def test_v3_pdf_profile_binds_only_visual_v2_presentation(tmp_path: Path) -> None:
    study = create_study(
        "human-pdf-visual-study",
        "human_usability",
        KINDS,
        "d" * 64,
        candidate_profile="projecttown-human-pdf-v3",
    )
    material, result, _markdown, v2_pdf = _pdf_result(
        tmp_path / "v3", export_version="v3-material-pdf-export-v2"
    )
    trial = create_trial(
        study,
        "T001",
        state="completed",
        actions=("open_task", "preview", "export_or_retain"),
        elapsed_seconds=20,
        manual_baseline_seconds=30,
        control_rating=4,
        structural_rewrite=False,
        citation_usable=True,
        disposition="exported",
        improvement_reason="none",
        material_root=material,
        result_path=result,
        pdf_export_path=v2_pdf,
    )
    assert trial.presentation is not None
    assert (
        trial.presentation.pdf_export_version,
        trial.presentation.pdf_renderer_version,
    ) == ("v3-material-pdf-export-v2", "projecttown-reportlab-pdf-v2")

    material, result, _markdown, v1_pdf = _pdf_result(tmp_path / "wrong")
    with pytest.raises(UsabilityTrialError):
        create_trial(
            study,
            "T002",
            state="completed",
            actions=("open_task",),
            elapsed_seconds=20,
            manual_baseline_seconds=30,
            control_rating=4,
            structural_rewrite=False,
            citation_usable=True,
            disposition="not_kept",
            improvement_reason="artifact_quality",
            material_root=material,
            result_path=result,
            pdf_export_path=v1_pdf,
        )


def test_unknown_pdf_profile_fails_closed() -> None:
    with pytest.raises(UsabilityTrialError):
        create_study(
            "unknown-pdf-profile",
            "human_usability",
            KINDS,
            "e" * 64,
            candidate_profile="projecttown-human-pdf-v99",  # type: ignore[arg-type]
        )


def test_mixed_pdf_presentation_pair_fails_closed() -> None:
    with pytest.raises(ValueError):
        trials_module.PresentationBinding(
            presentation_format="pdf",
            pdf_bytes_hash="a" * 64,
            pdf_export_version="v3-material-pdf-export-v1",
            pdf_renderer_version="projecttown-reportlab-pdf-v2",
            pdf_source_artifact_hash="b" * 64,
        )


def test_v2_pdf_study_unknown_version_fails_closed() -> None:
    with pytest.raises(UsabilityTrialError) as error:
        parse_study_bytes(b'{"schema_version":"v3-usability-study-v99"}\n')
    assert error.value.code == "UNSUPPORTED_SCHEMA_VERSION"


def test_v2_exported_uses_pdf_only_and_rejects_markdown_pair(tmp_path: Path) -> None:
    study = create_study(
        "human-pdf-export",
        "human_usability",
        KINDS,
        "c" * 64,
        candidate_profile="projecttown-human-pdf-v2",
    )
    material, result, markdown, pdf = _pdf_result(tmp_path)
    exported = create_trial(
        study,
        "T001",
        state="completed",
        actions=("open_task", "preview", "export_or_retain"),
        elapsed_seconds=20,
        manual_baseline_seconds=30,
        control_rating=4,
        structural_rewrite=False,
        citation_usable=True,
        disposition="exported",
        improvement_reason="none",
        material_root=material,
        result_path=result,
        pdf_export_path=pdf,
    )
    assert exported.schema_version == "v3-usability-trial-v2"
    assert exported.presentation is not None
    assert (
        exported.presentation.pdf_bytes_hash
        == hashlib.sha256(pdf.read_bytes()).hexdigest()
    )
    with pytest.raises(UsabilityTrialError):
        create_trial(
            study,
            "T001",
            state="completed",
            actions=("open_task",),
            elapsed_seconds=20,
            manual_baseline_seconds=30,
            control_rating=4,
            structural_rewrite=False,
            citation_usable=True,
            disposition="exported",
            improvement_reason="none",
            material_root=material,
            result_path=result,
            export_path=markdown,
            pdf_export_path=pdf,
        )


def test_failed_and_abandoned_need_no_result() -> None:
    study = _study()
    failed = _failed(study, "T001")
    abandoned = create_trial(
        study,
        "T002",
        state="abandoned",
        failure_stage="preview",
        failure_code="user_stopped",
        disposition="not_kept",
        improvement_reason="workflow",
    )
    assert failed.result is abandoned.result is None
    assert failed.call_observation == abandoned.call_observation == "not_observed"


def test_human_gate_exactly_seven_and_negative_cases(tmp_path: Path) -> None:
    study = _study("human_usability")
    bindings = [_result(tmp_path / f"record-{i}") for i in range(1, 11)]
    trials = [
        _completed(study, f"T{i:03d}", *bindings[i - 1], disposition="retained")
        for i in range(1, 8)
    ]
    trials += [
        _completed(
            study,
            f"T{i:03d}",
            *bindings[i - 1],
            disposition="not_kept",
            improvement_reason="artifact_quality",
        )
        for i in range(8, 11)
    ]
    summary = aggregate_summary(study, trials)
    assert summary.gate_state == "criteria_met_unanchored_awaiting_user_acceptance"
    six = list(trials)
    six[6] = _completed(
        study,
        "T007",
        *bindings[6],
        disposition="not_kept",
        improvement_reason="artifact_quality",
    )
    assert aggregate_summary(study, six).gate_state == "criteria_not_met"
    citations = list(trials)
    citations[0] = _completed(study, "T001", *bindings[0], citation_usable=False)
    assert aggregate_summary(study, citations).gate_state == "criteria_not_met"
    actions = list(trials)
    actions[0] = _completed(
        study,
        "T001",
        *bindings[0],
        actions=(
            "open_task",
            "select_materials",
            "confirm_and_generate",
            "preview",
            "export_or_retain",
            "resolve_conflict",
        ),
    )
    assert aggregate_summary(study, actions).gate_state == "criteria_not_met"
    unobserved = [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    assert aggregate_summary(study, unobserved).gate_state == "criteria_not_met"


def test_synthetic_is_engineering_only_and_summary_publication(tmp_path: Path) -> None:
    study = _study()
    trials = [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    summary = aggregate_summary(study, trials)
    assert summary.gate_state == "engineering_only"
    root = tmp_path / "study"
    root.mkdir()
    publish_study(root, study)
    for trial in trials:
        publish_trial(root, trial)
        assert load_trial(root, trial.task_id) == trial
    publish_summary(root, summary)
    assert load_summary(root) == summary
    assert verify_summary(study, trials, summary)


def test_trial_set_mismatch_duplicate_and_semantic_rehash_fail() -> None:
    study = _study()
    trials = [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    with pytest.raises(UsabilityTrialError):
        aggregate_summary(study, trials[:-1])
    with pytest.raises(UsabilityTrialError):
        aggregate_summary(study, trials[:9] + [trials[8]])
    forged = trials[0].model_copy(
        update={"state": "completed", "failure_stage": "draft"}
    )
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "record_hash": __import__(
                "backend.app.usability_trials", fromlist=["_hash"]
            )._hash(
                "projecttown/v3/usability-trial/v1",
                {key: value for key, value in data.items() if key != "record_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        serialize_trial(forged)


def test_result_stale_and_export_mismatch_rejected(tmp_path: Path) -> None:
    study = _study()
    material, result, export = _result(tmp_path)
    (material / "source.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(UsabilityTrialError):
        _completed(study, "T001", material, result, export)
    material, result, export = _result(tmp_path / "second")
    export.write_text("wrong\n", encoding="utf-8")
    with pytest.raises(UsabilityTrialError):
        _completed(study, "T001", material, result, export)


def test_canonical_json_rejections_and_hardlink(tmp_path: Path) -> None:
    study = _study()
    data = serialize_study(study)
    for bad in (
        data + b" ",
        data.replace(b"{", b'{"study_id":"x",', 1),
        b'{"x":NaN}',
        b"{" + b" " * MAX_RECORD_BYTES + b"}",
    ):
        with pytest.raises(UsabilityTrialError):
            parse_study_bytes(bad)
    with pytest.raises(UsabilityTrialError):
        parse_study_bytes(
            b'{"schema_version":"v3-usability-study-v1","study_id":"x","evaluation_kind":"synthetic_engineering_fixture","tasks":true,"study_hash":"'
            + b"0" * 64
            + b'"}\n'
        )
    root = tmp_path / "study"
    root.mkdir()
    publish_study(root, study)
    try:
        os.link(root / "study.json", root / "linked.json")
    except OSError as error:
        pytest.skip(str(error))
    with pytest.raises(UsabilityTrialError):
        load_study(root)


def test_deterministic_two_roots_and_no_sensitive_fields(tmp_path: Path) -> None:
    first = _study()
    second = create_study(
        "synthetic-study-1", "synthetic_engineering_fixture", KINDS, "a" * 64
    )
    assert serialize_study(first) == serialize_study(second)
    rendered = serialize_study(first).decode("utf-8")
    for forbidden in ("path", "prompt", "response", "note", "person", "content"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "field,value",
    [("metrics", None), ("gate_state", "criteria_not_met")],
    ids=["metrics_forge", "gate_forge"],
)
def test_rehashed_summary_semantic_forge_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    if field == "metrics":
        value = summary.metrics.model_copy(update={"completed": 10})
    forged = summary.model_copy(update={field: value})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {key: item for key, item in data.items() if key != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


@pytest.mark.parametrize(
    "payload",
    [b'{"unknown":1}\n', b'{"x":1,"x":2}\n', b"{} \n"],
    ids=["unknown", "duplicate", "noncanonical"],
)
def test_strict_json_adversaries(payload: bytes) -> None:
    with pytest.raises(UsabilityTrialError):
        parse_study_bytes(payload)


@pytest.mark.parametrize("length", [0, 9, 11], ids=["empty", "short", "long"])
def test_study_length_is_stable_domain_error(length: int) -> None:
    with pytest.raises(UsabilityTrialError) as error:
        create_study(
            "length", "synthetic_engineering_fixture", ("plan",) * length, "a" * 64
        )
    assert error.value.code == "INVALID_STUDY"


@pytest.mark.parametrize(
    "state", ["workflow_failed", "abandoned"], ids=["failed", "abandoned"]
)
def test_noncompleted_no_result_is_observed_not_observed(state: str) -> None:
    study = _study()
    trial = create_trial(
        study,
        "T001",
        state=state,
        failure_stage="draft",
        failure_code="invalid_input",
        disposition="not_kept",
        improvement_reason="workflow",
    )
    assert trial.call_observation == "not_observed"


@pytest.mark.parametrize(
    "kind",
    ["human_usability", "synthetic_engineering_fixture"],
    ids=["human_provenance", "synthetic_provenance"],
)
def test_measurement_provenance_is_derived(kind: str) -> None:
    study = _study(kind)
    trial = _failed(study, "T001")
    expected = (
        "human_reported_current_invocation"
        if kind == "human_usability"
        else "synthetic_fixture"
    )
    assert trial.measurement_provenance == expected


@pytest.mark.parametrize(
    "field",
    ["study", "projections", "metrics", "gate_state"],
    ids=["study", "artifact_kind", "adoptable", "gate"],
)
def test_rehashed_summary_projection_forges_rejected(field: str) -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    if field == "study":
        value = summary.study.model_copy(update={"study_id": "forged"})
    elif field == "projections":
        value = (
            summary.projections[0].model_copy(update={"artifact_kind": "report"}),
        ) + summary.projections[1:]
    elif field == "metrics":
        value = summary.metrics.model_copy(update={"time_saved_seconds": 1})
    else:
        value = "criteria_not_met"
    forged = summary.model_copy(update={field: value})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {k: v for k, v in data.items() if k != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


@pytest.mark.parametrize(
    "field,value",
    [
        ("within_five_actions", False),
        ("time_saved_seconds", 99),
        ("citations_complete", True),
        ("call_observation", "observed_zero"),
    ],
    ids=["within_five", "time_saved", "citation", "call"],
)
def test_rehashed_projection_derived_fields_rejected(field: str, value: object) -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    projections = (
        summary.projections[0].model_copy(update={field: value}),
    ) + summary.projections[1:]
    forged = summary.model_copy(update={"projections": projections})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {k: v for k, v in data.items() if k != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


@pytest.mark.parametrize("delta", [-1, 1], ids=["negative_time", "positive_time"])
def test_time_saved_is_exact(delta: int) -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    assert summary.metrics.time_saved_seconds == 0 + delta * 0


def test_candidate_manifest_hash_is_required_and_strict() -> None:
    for value in ("", "A" * 64, "a" * 63):
        with pytest.raises(UsabilityTrialError):
            create_study("candidate", "synthetic_engineering_fixture", KINDS, value)


def test_candidate_manifest_hash_changes_study_and_trial_lineage() -> None:
    first = create_study("candidate", "synthetic_engineering_fixture", KINDS, "a" * 64)
    second = create_study("candidate", "synthetic_engineering_fixture", KINDS, "b" * 64)
    assert serialize_study(first) != serialize_study(second)
    assert first.study_hash != second.study_hash
    assert _failed(first, "T001").study_hash != _failed(second, "T001").study_hash


def test_rehashed_embedded_summary_candidate_hash_is_rejected() -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    forged_study = summary.study.model_copy(
        update={"candidate_manifest_hash": "b" * 64}
    )
    forged = summary.model_copy(update={"study": forged_study})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {key: value for key, value in data.items() if key != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


def test_study_model_copy_bypass_rejected() -> None:
    study = _study()
    forged = study.model_copy(update={"tasks": tuple(reversed(study.tasks))})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "study_hash": trials_module._hash(
                "projecttown/v3/usability-study/v1",
                {k: v for k, v in data.items() if k != "study_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        serialize_study(forged)


def test_summary_order_model_copy_bypass_rejected() -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    forged = summary.model_copy(
        update={"projections": tuple(reversed(summary.projections))}
    )
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {k: v for k, v in data.items() if k != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


@pytest.mark.parametrize(
    "field,value",
    [
        ("call_observation", "observed_nonzero"),
        ("time_saved_seconds", 99),
    ],
    ids=["observed_nonzero", "time"],
)
def test_projection_impossibilities_rejected(field: str, value: object) -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    p = summary.projections[0].model_copy(update={field: value})
    forged = summary.model_copy(update={"projections": (p,) + summary.projections[1:]})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {k: v for k, v in data.items() if k != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


def test_rich_render_is_deterministic() -> None:
    study = _study()
    summary = aggregate_summary(
        study, [_failed(study, f"T{i:03d}") for i in range(1, 11)]
    )
    assert trials_module.render_summary(summary) == trials_module.render_summary(
        summary
    )


def test_completed_projection_failure_fields_rejected(tmp_path: Path) -> None:
    study = _study("human_usability")
    completed = _completed(study, "T001", *_result(tmp_path))
    summary = aggregate_summary(
        study, [completed] + [_failed(study, f"T{i:03d}") for i in range(2, 11)]
    )
    projection = summary.projections[0].model_copy(
        update={"failure_stage": "draft", "failure_code": "invalid_input"}
    )
    forged = summary.model_copy(
        update={"projections": (projection,) + summary.projections[1:]}
    )
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "summary_hash": trials_module._hash(
                "projecttown/v3/usability-summary/v1",
                {key: value for key, value in data.items() if key != "summary_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        trials_module.serialize_summary(forged)


def test_result_backed_trial_counter_observation_forge_rejected(tmp_path: Path) -> None:
    study = _study("human_usability")
    trial = _completed(study, "T001", *_result(tmp_path))
    assert trial.result is not None
    binding = trial.result.model_copy(update={"provider_calls": 1})
    forged = trial.model_copy(update={"result": binding})
    data = forged.model_dump(mode="json")
    forged = forged.model_copy(
        update={
            "record_hash": trials_module._hash(
                "projecttown/v3/usability-trial/v1",
                {key: value for key, value in data.items() if key != "record_hash"},
            )
        }
    )
    with pytest.raises(UsabilityTrialError):
        serialize_trial(forged)


def test_root_replacement_after_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study = _study()
    root = tmp_path / "study"
    root.mkdir()
    publish_study(root, study)
    original = trials_module.read_stable_regular_file

    def replace(*args: object, **kwargs: object):
        stable = original(*args, **kwargs)
        moved = tmp_path / "former"
        root.rename(moved)
        root.mkdir()
        return stable

    monkeypatch.setattr(trials_module, "read_stable_regular_file", replace)
    with pytest.raises(UsabilityTrialError) as error:
        load_study(root)
    assert error.value.code == "UNSTABLE_RECORD"


@pytest.mark.parametrize(
    "mode",
    ["inside", "hardlink", "noncanonical"],
    ids=["inside_material", "hardlink", "dotdot"],
)
def test_export_boundary_paths_rejected(tmp_path: Path, mode: str) -> None:
    study = _study("human_usability")
    material, result, export = _result(tmp_path)
    if mode == "inside":
        candidate = material / "inside.md"
        candidate.write_bytes(export.read_bytes())
    elif mode == "hardlink":
        candidate = tmp_path / "external" / "linked.md"
        try:
            os.link(export, candidate)
        except OSError as error:
            pytest.skip(f"hard links unavailable: {error}")
    else:
        child = tmp_path / "external" / "canonical-child"
        child.mkdir()
        candidate = child / ".." / "artifact.md"
    with pytest.raises(UsabilityTrialError):
        _completed(study, "T001", material, result, candidate)


def test_v4_pdf_profile_binds_only_runbook_presentation(tmp_path: Path) -> None:
    study = create_study(
        "human-pdf-runbook-study",
        "human_usability",
        KINDS,
        "f" * 64,
        candidate_profile="projecttown-human-pdf-v4",
    )
    assert study.candidate_profile == "projecttown-human-pdf-v4"
    assert trials_module.pdf_presentation_pair_for_profile(study.candidate_profile) == (
        "v3-material-pdf-export-v3",
        "projecttown-reportlab-pdf-v3",
    )
    binding = trials_module.PresentationBinding(
        presentation_format="pdf",
        pdf_bytes_hash="a" * 64,
        pdf_export_version="v3-material-pdf-export-v3",
        pdf_renderer_version="projecttown-reportlab-pdf-v3",
        pdf_source_artifact_hash="b" * 64,
    )
    assert binding.pdf_renderer_version == "projecttown-reportlab-pdf-v3"
    material, result, _markdown, v2_pdf = _pdf_result(
        tmp_path / "mixed-v4", export_version="v3-material-pdf-export-v2"
    )
    with pytest.raises(UsabilityTrialError):
        create_trial(
            study,
            "T001",
            state="completed",
            actions=("open_task",),
            elapsed_seconds=20,
            manual_baseline_seconds=30,
            control_rating=4,
            structural_rewrite=False,
            citation_usable=True,
            disposition="not_kept",
            improvement_reason="artifact_quality",
            material_root=material,
            result_path=result,
            pdf_export_path=v2_pdf,
        )
    with pytest.raises(ValueError):
        trials_module.PresentationBinding(
            presentation_format="pdf",
            pdf_bytes_hash="a" * 64,
            pdf_export_version="v3-material-pdf-export-v3",
            pdf_renderer_version="projecttown-reportlab-pdf-v2",
            pdf_source_artifact_hash="b" * 64,
        )


def test_v5_pdf_profile_binds_only_v4_presentation() -> None:
    study = create_study(
        "human-pdf-runbook-v5-study",
        "human_usability",
        KINDS,
        "f" * 64,
        candidate_profile="projecttown-human-pdf-v5",
    )
    assert trials_module.pdf_presentation_pair_for_profile(study.candidate_profile) == (
        "v3-material-pdf-export-v4",
        "projecttown-reportlab-pdf-v4",
    )
    binding = trials_module.PresentationBinding(
        presentation_format="pdf",
        pdf_bytes_hash="a" * 64,
        pdf_export_version="v3-material-pdf-export-v4",
        pdf_renderer_version="projecttown-reportlab-pdf-v4",
        pdf_source_artifact_hash="b" * 64,
    )
    assert binding.pdf_renderer_version == "projecttown-reportlab-pdf-v4"
    with pytest.raises(ValueError):
        trials_module.PresentationBinding(
            presentation_format="pdf",
            pdf_bytes_hash="a" * 64,
            pdf_export_version="v3-material-pdf-export-v4",
            pdf_renderer_version="projecttown-reportlab-pdf-v3",
            pdf_source_artifact_hash="b" * 64,
        )


def test_v6_pdf_profile_uses_v3_trial_contract_and_v5_presentation() -> None:
    study = create_study(
        "human-pdf-runbook-v6-study",
        "human_usability",
        KINDS,
        "f" * 64,
        candidate_profile="projecttown-human-pdf-v6",
    )
    assert trials_module.pdf_presentation_pair_for_profile(study.candidate_profile) == (
        "v3-material-pdf-export-v5",
        "projecttown-reportlab-pdf-v5",
    )
    assert study.candidate_profile in {
        "projecttown-human-pdf-v5",
        "projecttown-human-pdf-v6",
    }
    common = {
        "state": "workflow_failed",
        "actions": ("open_task",),
        "elapsed_seconds": 20,
        "manual_baseline_seconds": 30,
        "control_rating": 3,
        "structural_rewrite": None,
        "citation_usable": None,
        "disposition": "not_kept",
        "improvement_reason": "workflow",
        "failure_stage": "draft",
        "failure_code": "invalid_input",
        "participant_notes": "暂无",
        "participant_timestamp": "2026-08-28T08:00:00Z",
    }
    assert isinstance(create_trial(study, "T001", **common), trials_module.TrialV3)
    v5 = create_study(
        "human-pdf-v5-routing",
        "human_usability",
        KINDS,
        "e" * 64,
        candidate_profile="projecttown-human-pdf-v5",
    )
    assert isinstance(create_trial(v5, "T001", **common), trials_module.TrialV3)
    v4 = create_study(
        "human-pdf-v4-routing",
        "human_usability",
        KINDS,
        "d" * 64,
        candidate_profile="projecttown-human-pdf-v4",
    )
    old_common = {
        key: value
        for key, value in common.items()
        if not key.startswith("participant_")
    }
    assert isinstance(create_trial(v4, "T001", **old_common), trials_module.TrialV2)


def test_v7_pdf_profile_uses_v3_trial_contract_and_v6_presentation() -> None:
    study = create_study(
        "human-pdf-runbook-v7-study",
        "human_usability",
        KINDS,
        "c" * 64,
        candidate_profile="projecttown-human-pdf-v7",
    )
    assert trials_module.pdf_presentation_pair_for_profile(study.candidate_profile) == (
        "v3-material-pdf-export-v6",
        "projecttown-reportlab-pdf-v6",
    )
    trial = create_trial(
        study,
        "T001",
        state="workflow_failed",
        actions=("open_task",),
        elapsed_seconds=20,
        manual_baseline_seconds=30,
        control_rating=3,
        structural_rewrite=None,
        citation_usable=None,
        disposition="not_kept",
        improvement_reason="workflow",
        failure_stage="draft",
        failure_code="invalid_input",
        participant_notes="暂无",
        participant_timestamp="2026-08-28T08:00:00Z",
    )
    assert isinstance(trial, trials_module.TrialV3)
    assert trials_module.verify_trial(trial)


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
def test_v7_to_v9_completed_trial_binds_pdf_and_rejects_drift_or_other_evidence_path(
    tmp_path: Path,
    candidate_profile: str,
    generator_version: str,
    export_version: str,
    renderer_version: str,
) -> None:
    """Participant-evidence profiles must exercise real PDF binding, not failure-only."""
    study = create_study(
        f"human-pdf-{candidate_profile}-completed",
        "human_usability",
        KINDS,
        "9" * 64,
        candidate_profile=candidate_profile,  # type: ignore[arg-type]
    )
    material = tmp_path / candidate_profile / "material"
    outside = Path(tempfile.mkdtemp(prefix="projecttown-pdf-trial-evidence-"))
    material.mkdir(parents=True)
    (material / "source.md").write_text(
        "# source\nverified runbook\n", encoding="utf-8"
    )
    draft = create_draft(
        material,
        ["source.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=(
            _v10_binding_constraints(tmp_path, material, outside)
            if generator_version == "deterministic-grounded-plan-v9"
            else None
        ),
        generator_version=generator_version,
    )
    rendered = generate_result(material, draft, draft.contract_hash)
    result = outside / "result.json"
    publish_new_file(material, result, serialize_session(rendered))
    pdf = outside / "candidate.pdf"
    publish_new_file(
        material,
        pdf,
        render_pdf_export(material, rendered, export_version=export_version),
    )
    completed = {
        "state": "completed",
        "actions": ("open_task",),
        "elapsed_seconds": 20,
        "manual_baseline_seconds": 30,
        "control_rating": 4,
        "structural_rewrite": False,
        "citation_usable": True,
        "disposition": "retained",
        "improvement_reason": "none",
        "material_root": material,
        "result_path": result,
        "pdf_export_path": pdf,
        "participant_notes": "participant-confirmed",
        "participant_timestamp": "2026-08-29T08:00:00Z",
        "participant_evidence_path": pdf,
    }
    trial = create_trial(study, "T001", **completed)
    assert isinstance(trial, trials_module.TrialV3)
    assert trial.presentation is not None
    assert (
        trial.presentation.pdf_export_version,
        trial.presentation.pdf_renderer_version,
    ) == (export_version, renderer_version)
    raw = serialize_trial(trial)
    assert parse_trial_bytes(raw) == trial
    assert verify_trial(trial)

    alternate = pdf.with_name("participant-confirmed-copy.pdf")
    alternate.write_bytes(pdf.read_bytes())
    mismatch = dict(completed)
    mismatch["participant_evidence_path"] = alternate
    with pytest.raises(UsabilityTrialError) as error:
        create_trial(study, "T002", **mismatch)
    assert error.value.code == "PARTICIPANT_EVIDENCE_PATH_MISMATCH"

    pdf.write_bytes(b"drift-before-trial-create")
    with pytest.raises(UsabilityTrialError) as error:
        create_trial(study, "T003", **completed)
    assert error.value.code == "INVALID_PDF_EXPORT"


def test_trial_schema_routes_v2_to_v8_profiles() -> None:
    common = {
        "state": "workflow_failed",
        "actions": ("open_task",),
        "elapsed_seconds": 20,
        "manual_baseline_seconds": 30,
        "control_rating": 3,
        "structural_rewrite": None,
        "citation_usable": None,
        "disposition": "not_kept",
        "improvement_reason": "workflow",
        "failure_stage": "draft",
        "failure_code": "invalid_input",
    }
    for index, profile in enumerate(
        (
            "projecttown-human-pdf-v2",
            "projecttown-human-pdf-v3",
            "projecttown-human-pdf-v4",
        ),
        start=2,
    ):
        study = create_study(
            f"human-pdf-{profile}-routing",
            "human_usability",
            KINDS,
            str(index) * 64,
            candidate_profile=profile,  # type: ignore[arg-type]
        )
        assert isinstance(create_trial(study, "T001", **common), trials_module.TrialV2)
    for index, profile in enumerate(
        (
            "projecttown-human-pdf-v5",
            "projecttown-human-pdf-v6",
            "projecttown-human-pdf-v7",
            "projecttown-human-pdf-v8",
            "projecttown-human-pdf-v9",
            "projecttown-human-pdf-v10",
        ),
        start=5,
    ):
        study = create_study(
            f"human-pdf-{profile}-routing",
            "human_usability",
            KINDS,
            "abcdef"[index - 5] * 64,
            candidate_profile=profile,  # type: ignore[arg-type]
        )
        assert isinstance(
            create_trial(
                study,
                "T001",
                **common,
                participant_notes="暂无",
                participant_timestamp="2026-08-29T08:00:00Z",
            ),
            trials_module.TrialV3,
        )


def test_legacy_v1_and_v6_trial_bytes_and_completed_v6_pdf_route_remain_compatible(
    tmp_path: Path,
) -> None:
    """The v7 binding repair is additive to canonical legacy Trial records."""
    legacy_v1 = _failed(_study(), "T001")
    assert parse_trial_bytes(serialize_trial(legacy_v1)) == legacy_v1
    assert verify_trial(legacy_v1)

    study = create_study(
        "human-pdf-v6-completed",
        "human_usability",
        KINDS,
        "8" * 64,
        candidate_profile="projecttown-human-pdf-v6",
    )
    material = tmp_path / "v6-completed" / "material"
    outside = Path(tempfile.mkdtemp(prefix="projecttown-v6-trial-evidence-"))
    material.mkdir(parents=True)
    (material / "source.md").write_text("# source\nlegacy runbook\n", encoding="utf-8")
    draft = create_draft(
        material,
        ["source.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v5",
    )
    rendered = generate_result(material, draft, draft.contract_hash)
    result = outside / "result.json"
    pdf = outside / "candidate.pdf"
    publish_new_file(material, result, serialize_session(rendered))
    publish_new_file(
        material,
        pdf,
        render_pdf_export(
            material, rendered, export_version="v3-material-pdf-export-v5"
        ),
    )
    completed = create_trial(
        study,
        "T001",
        state="completed",
        actions=("open_task",),
        elapsed_seconds=20,
        manual_baseline_seconds=30,
        control_rating=4,
        structural_rewrite=False,
        citation_usable=True,
        disposition="retained",
        improvement_reason="none",
        material_root=material,
        result_path=result,
        pdf_export_path=pdf,
        participant_notes="legacy participant confirmation",
        participant_timestamp="2026-08-28T08:00:00Z",
        participant_evidence_path=pdf,
    )
    assert isinstance(completed, trials_module.TrialV3)
    assert completed.presentation is not None
    assert completed.presentation.pdf_export_version == "v3-material-pdf-export-v5"
    raw = serialize_trial(completed)
    assert serialize_trial(parse_trial_bytes(raw)) == raw
    assert verify_trial(completed)


@pytest.mark.parametrize(
    ("export_version", "renderer_version", "wrong_renderer_version"),
    [
        (
            "v3-material-pdf-export-v6",
            "projecttown-reportlab-pdf-v6",
            "projecttown-reportlab-pdf-v5",
        ),
        (
            "v3-material-pdf-export-v7",
            "projecttown-reportlab-pdf-v7",
            "projecttown-reportlab-pdf-v6",
        ),
        (
            "v3-material-pdf-export-v8",
            "projecttown-reportlab-pdf-v8",
            "projecttown-reportlab-pdf-v7",
        ),
        (
            "v3-material-pdf-export-v9",
            "projecttown-reportlab-pdf-v9",
            "projecttown-reportlab-pdf-v8",
        ),
    ],
)
def test_presentation_binding_accepts_profile_pairs_and_rejects_mixed_pair(
    export_version: str,
    renderer_version: str,
    wrong_renderer_version: str,
) -> None:
    valid = trials_module.PresentationBinding(
        presentation_format="pdf",
        pdf_bytes_hash="a" * 64,
        pdf_export_version=export_version,
        pdf_renderer_version=renderer_version,
        pdf_source_artifact_hash="b" * 64,
    )
    assert valid.pdf_renderer_version == renderer_version
    with pytest.raises(trials_module.ValidationError):
        trials_module.PresentationBinding(
            presentation_format="pdf",
            pdf_bytes_hash="a" * 64,
            pdf_export_version=export_version,
            pdf_renderer_version=wrong_renderer_version,
            pdf_source_artifact_hash="b" * 64,
        )
