from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from backend.app import controlled_write, phase2_closeout, usability_trials
from backend.app.controlled_apply import parse_apply_plan_bytes
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    parse_session_bytes,
)
from backend.app.phase3e_release_candidate import (
    CANDIDATE_PROFILE,
    CANDIDATE_PROFILE_V3,
    CANDIDATE_PROFILE_V4,
    ROUND_HASH_DOMAIN_V4,
    ROUND_IDS,
    SUMMARY_HASH_DOMAIN,
    ControlledOperationBindingV3,
    EngineeringAcceptanceV4,
    P0Values,
    ParticipantEvidence,
    ParticipantEvidenceV4,
    Phase3EError,
    Phase3ERoundV4,
    PredecessorEvidenceV4,
    ReviewerEvidence,
    Round1ContractV4,
    _make,
    canonical_json,
    create_engineering_acceptance_v4,
    create_round,
    create_round1_contract_v3,
    create_source_set_manifest,
    create_study,
    create_summary,
    create_user_rc_decision,
    parse_record_bytes,
    publish_record,
    serialize_record,
    status_projection,
    verify_record,
    verify_round_for_study,
    verify_summary_for_study,
    verify_user_decision_for_study,
)
from tests.controlled_write_support import ready


def _file(directory: Path, name: str, contents: bytes = b"evidence") -> Path:
    path = directory / name
    path.write_bytes(contents)
    return path


def _meta(kind: str, path: Path, record: object) -> tuple[str, Path, str, str]:
    hashes = {
        "result": "session_hash",
        "apply_plan": "plan_hash",
        "executable_proposal": "proposal_hash",
        "user_authorization": "authorization_hash",
        "restore_authorization": "authorization_hash",
        "apply_receipt": "event_hash",
        "restore_receipt": "event_hash",
    }
    return kind, path, record.schema_version, getattr(record, hashes[kind])


def _human(
    work: Path,
    suffix: str,
    *,
    participant_identity: str,
    reviewer_identity: str = "independent reviewer",
    disposition: str = "PASS",
    control: int = 4,
) -> tuple[ParticipantEvidence, ReviewerEvidence]:
    participant = ParticipantEvidence(
        participant_identity=participant_identity,
        disposition="retained",
        elapsed_seconds=120,
        actions=("open_task",),
        notes="participant supplied evidence",
        timestamp="2026-08-31T10:00:00+08:00",
        evidence_path=str(_file(work, f"participant-{suffix}.pdf")),
    )
    reviewer = ReviewerEvidence(
        reviewer_identity=reviewer_identity,
        disposition=disposition,
        executability_rating=4,
        readability_rating=4,
        control_rating=control,
        citation_traceability_rating=4,
        fixed_question_answers=("rerun", "history", "user", "next"),
        notes="reviewer supplied evidence",
        actions=("open_task",),
        timestamp="2026-08-31T10:01:00+08:00",
        evidence_path=str(_file(work, f"reviewer-{suffix}.json")),
    )
    return participant, reviewer


def _case(
    tmp_path: Path,
    *,
    participant_count: int = 2,
    r1_identity: str = "participant-one",
    r2_identity: str = "participant-two",
):
    case = ready(tmp_path)
    work = case["evidence"]
    study_root = tmp_path / "study"
    study_root.mkdir()
    manifest = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "v3-phase-3"
        / "projecttown-phase3e-manifest-v1.json"
    )
    study = create_study(
        "phase3e-fixture-001",
        study_root,
        work,
        manifest,
        p0=P0Values(
            control_rating_threshold=4,
            participant_arrangement="independent human reviewers",
            participant_count=participant_count,
            backup_retention="retain backup until user decision",
            release_evidence_format="canonical json sha256",
        ),
    )
    receipt = controlled_write.apply(
        case["root"],
        case["auth_path"],
        case["result"],
        case["proposal_path"],
        case["target"],
        case["plan"],
        case["ledger"],
        Path(case["auth"].backup_path),
        Path(case["auth"].receipt_path),
    )
    restore_ledger = work / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = work / "restore-authorization.json"
    restore_auth = controlled_write.create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-operation-001",
        "b" * 32,
    )
    restore_receipt_path = Path(restore_auth.receipt_path)
    restore_receipt = controlled_write.restore(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        restore_receipt_path,
    )
    result = parse_session_bytes(Path(case["result"]).read_bytes())
    plan = parse_apply_plan_bytes(Path(case["plan"]).read_bytes())
    participant1, reviewer1 = _human(work, "r1", participant_identity=r1_identity)
    r1_evidence = (
        _meta("result", case["result"], result),
        _meta("apply_plan", case["plan"], plan),
        _meta("executable_proposal", case["proposal_path"], case["proposal"]),
        _meta("user_authorization", case["auth_path"], case["auth"]),
        _meta("restore_authorization", restore_auth_path, restore_auth),
        ("ledger", next((case["ledger"] / case["auth"].operation_id).glob("*.json"))),
        ("backup", Path(case["auth"].backup_path)),
        ("restore_backup", Path(restore_auth.backup_path)),
        _meta("apply_receipt", Path(case["auth"].receipt_path), receipt),
        _meta("restore_receipt", restore_receipt_path, restore_receipt),
        ("reconcile_observation", _file(work, "reconcile.json")),
    )
    r1 = create_round(
        study,
        round_id=ROUND_IDS[0],
        binding_status="verified",
        target_is_disposable_external_fixture=True,
        evidence_paths=r1_evidence,
        participant=participant1,
        reviewer=reviewer1,
        citation_usable=True,
        structural_rewrite=False,
    )
    export = _file(work, "candidate.pdf")
    participant2, reviewer2 = _human(work, "r2", participant_identity=r2_identity)
    participant2 = participant2.model_copy(update={"evidence_path": str(export)})
    r2 = create_round(
        study,
        round_id=ROUND_IDS[1],
        binding_status="verified",
        target_is_disposable_external_fixture=False,
        evidence_paths=(
            ("material_manifest", manifest),
            _meta("result", case["result"], result),
            ("preview", _file(work, "preview.json")),
            ("citation", _file(work, "citation.json")),
            ("pdf_export", export),
        ),
        participant=participant2,
        reviewer=reviewer2,
        citation_usable=True,
        structural_rewrite=False,
    )
    return case, study, r1, r2


def test_positive_real_canonical_chain_summary_decision_and_status(
    tmp_path: Path,
) -> None:
    _, study, r1, r2 = _case(tmp_path)
    summary = create_summary(study, (r1, r2))
    decision = create_user_rc_decision(
        study,
        summary,
        decision="ACCEPT",
        user_timestamp="2026-08-31T10:02:00+08:00",
        evidence_path=_file(Path(study.work_root), "user.json"),
        notes="user supplied decision",
    )
    assert study.lineage.candidate_profile == CANDIDATE_PROFILE
    assert all(verify_record(item) for item in (study, r1, r2, summary, decision))
    assert verify_round_for_study(study, r1)
    assert verify_summary_for_study(study, (r1, r2), summary)
    assert verify_user_decision_for_study(study, summary, decision)
    assert parse_record_bytes(serialize_record(r1)) == r1
    assert decision.outcome == "rc_accepted_pending_version_gate"
    assert (
        status_projection(study, (r1, r2), summary, decision)["next_action"]
        == "hold_for_version_gate"
    )


def test_verified_rejects_raw_bytes_outside_paths_and_tamper(tmp_path: Path) -> None:
    case, study, r1, _ = _case(tmp_path)
    with pytest.raises(Phase3EError):
        create_round(
            study,
            round_id=ROUND_IDS[0],
            binding_status="verified",
            target_is_disposable_external_fixture=True,
            evidence_paths=(("result", _file(tmp_path, "raw.json")),),
        )
    with pytest.raises(Phase3EError):
        create_round(
            study,
            round_id=ROUND_IDS[1],
            binding_status="verified",
            target_is_disposable_external_fixture=False,
            evidence_paths=(("result", case["result"]),),
        )
    Path(case["result"]).write_bytes(Path(case["result"]).read_bytes() + b" ")
    assert not verify_record(r1)


def test_human_contract_threshold_questions_and_round2_export_binding(
    tmp_path: Path,
) -> None:
    _, study, r1, r2 = _case(tmp_path)
    assert r1.participant is not None and r1.participant.actions == ("open_task",)
    assert r2.reviewer is not None and len(r2.reviewer.fixed_question_answers) == 4
    low_reviewer = r2.reviewer.model_copy(update={"control_rating": 3})
    lower = create_round(
        study,
        round_id=ROUND_IDS[1],
        binding_status="verified",
        target_is_disposable_external_fixture=False,
        evidence_paths=tuple(
            (item.kind, Path(item.path), item.schema_version, item.record_hash)
            if item.schema_version
            else (item.kind, Path(item.path))
            for item in r2.evidence
        ),
        participant=r2.participant,
        reviewer=low_reviewer,
        citation_usable=True,
        structural_rewrite=False,
    )
    assert create_summary(study, (r1, lower)).gate_state == "criteria_not_met"
    wrong = r2.participant.model_copy(
        update={"evidence_path": str(_file(Path(study.work_root), "wrong.pdf"))}
    )
    with pytest.raises(Phase3EError):
        create_round(
            study,
            round_id=ROUND_IDS[1],
            binding_status="verified",
            target_is_disposable_external_fixture=False,
            evidence_paths=tuple(
                (item.kind, Path(item.path), item.schema_version, item.record_hash)
                if item.schema_version
                else (item.kind, Path(item.path))
                for item in r2.evidence
            ),
            participant=wrong,
            reviewer=r2.reviewer,
            citation_usable=True,
            structural_rewrite=False,
        )


def test_explicit_stale_record_blocks_summary_without_claiming_verified(
    tmp_path: Path,
) -> None:
    _, study, r1, r2 = _case(tmp_path)
    stale = create_round(
        study,
        round_id=ROUND_IDS[1],
        binding_status="stale",
        target_is_disposable_external_fixture=False,
        evidence_paths=tuple(
            (item.kind, Path(item.path), item.schema_version, item.record_hash)
            if item.schema_version
            else (item.kind, Path(item.path))
            for item in r2.evidence
        ),
        participant=r2.participant,
        reviewer=r2.reviewer,
        citation_usable=True,
        structural_rewrite=False,
    )
    assert create_summary(study, (r1, stale)).gate_state == "criteria_not_met"
    assert verify_round_for_study(study, stale)
    assert (
        f"INVALID_{ROUND_IDS[1]}_BINDING"
        not in status_projection(study, (r1, stale))["blockers"]
    )


def test_participant_count_and_reviewer_independence_are_fail_closed(
    tmp_path: Path,
) -> None:
    same_root = tmp_path / "same"
    same_root.mkdir()
    _, study, r1, r2 = _case(
        same_root,
        participant_count=1,
        r1_identity="participant",
        r2_identity="participant",
    )
    assert (
        create_summary(study, (r1, r2)).gate_state
        == "criteria_met_awaiting_user_rc_acceptance"
    )
    wrong_root = tmp_path / "wrong"
    wrong_root.mkdir()
    _, study, r1, r2 = _case(
        wrong_root,
        participant_count=2,
        r1_identity="participant",
        r2_identity="participant",
    )
    summary = create_summary(study, (r1, r2))
    assert summary.gate_state == "criteria_not_met"
    assert "PARTICIPANT_COUNT_MISMATCH" in summary.blockers
    same_reviewer = r2.reviewer.model_copy(
        update={"reviewer_identity": r2.participant.participant_identity}
    )
    not_independent = create_round(
        study,
        round_id=ROUND_IDS[1],
        binding_status="verified",
        target_is_disposable_external_fixture=False,
        evidence_paths=tuple(
            (item.kind, Path(item.path), item.schema_version, item.record_hash)
            if item.schema_version
            else (item.kind, Path(item.path))
            for item in r2.evidence
        ),
        participant=r2.participant,
        reviewer=same_reviewer,
        citation_usable=True,
        structural_rewrite=False,
    )
    summary = create_summary(study, (r1, not_independent))
    assert summary.gate_state == "criteria_not_met"
    assert f"{ROUND_IDS[1]}:REVIEWER_NOT_INDEPENDENT" in summary.blockers


def test_restore_receipt_missing_tampered_or_apply_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    _, study, r1, _ = _case(tmp_path)
    as_inputs = tuple(
        (item.kind, Path(item.path), item.schema_version, item.record_hash)
        if item.schema_version
        else (item.kind, Path(item.path))
        for item in r1.evidence
    )
    missing = tuple(item for item in as_inputs if item[0] != "restore_receipt")
    with pytest.raises(Phase3EError):
        create_round(
            study,
            round_id=ROUND_IDS[0],
            binding_status="verified",
            target_is_disposable_external_fixture=True,
            evidence_paths=missing,
        )
    restore_path = next(
        Path(item.path) for item in r1.evidence if item.kind == "restore_receipt"
    )
    restore_path.write_bytes(restore_path.read_bytes() + b" ")
    assert not verify_record(r1)
    apply = next(item for item in r1.evidence if item.kind == "apply_receipt")
    wrong = tuple(
        ("restore_receipt", Path(apply.path), apply.schema_version, apply.record_hash)
        if item[0] == "restore_receipt"
        else item
        for item in as_inputs
    )
    with pytest.raises(Phase3EError):
        create_round(
            study,
            round_id=ROUND_IDS[0],
            binding_status="verified",
            target_is_disposable_external_fixture=True,
            evidence_paths=wrong,
        )


def test_cross_record_duplicate_summary_decision_and_create_only(
    tmp_path: Path,
) -> None:
    _, study, r1, r2 = _case(tmp_path)
    summary = create_summary(study, (r1, r2))
    assert (
        "DUPLICATE_ROUND_ID" in status_projection(study, (r1, r1), summary)["blockers"]
    )
    forged = summary.model_copy(update={"summary_hash": "0" * 64})
    assert "INVALID_SUMMARY" in status_projection(study, (r1, r2), forged)["blockers"]
    false_gate = summary.model_copy(
        update={
            "gate_state": "criteria_not_met",
            "blockers": ("FORGED_GATE",),
            "summary_hash": "0" * 64,
        }
    )
    payload = false_gate.model_dump(mode="json")
    payload.pop("summary_hash")
    false_gate = false_gate.model_copy(
        update={
            "summary_hash": hashlib.sha256(
                SUMMARY_HASH_DOMAIN.encode("ascii") + b"\0" + canonical_json(payload)
            ).hexdigest()
        }
    )
    projected = status_projection(study, (r1, r2), false_gate)
    assert "INVALID_SUMMARY" in projected["blockers"]
    assert projected["gate_state"] == "engineering_only"
    publish_record(Path(study.study_root), "study.json", study)
    with pytest.raises(Phase3EError):
        publish_record(Path(study.study_root), "study.json", study)
    assert phase2_closeout.SCHEMA_VERSION == "v3-phase2-closeout-v1"
    assert usability_trials.TRIAL_SCHEMA_VERSION == "v3-usability-trial-v1"


def test_participant_reviewer_and_user_evidence_drift_is_rejected(
    tmp_path: Path,
) -> None:
    _, study, r1, r2 = _case(tmp_path)
    summary = create_summary(study, (r1, r2))
    decision_path = _file(Path(study.work_root), "user-drift.json")
    decision = create_user_rc_decision(
        study,
        summary,
        decision="ACCEPT",
        user_timestamp="2026-08-31T10:02:00+08:00",
        evidence_path=decision_path,
        notes="user supplied decision",
    )
    Path(r1.participant.evidence_path).write_bytes(b"drift")
    assert not verify_record(r1)
    Path(r2.reviewer.evidence_path).write_bytes(b"drift")
    assert not verify_record(r2)
    decision_path.write_bytes(b"drift")
    assert not verify_record(decision)


def test_duplicate_extra_noncanonical_and_manifest_identity(tmp_path: Path) -> None:
    _, study, _, _ = _case(tmp_path)
    data = serialize_record(study)
    with pytest.raises(Phase3EError):
        parse_record_bytes(data[:-1])
    with pytest.raises(Phase3EError):
        parse_record_bytes(data.replace(b'"study_id":', b'"study_id":"x","study_id":'))
    raw = json.loads(data)
    raw["extra"] = True
    with pytest.raises(Phase3EError):
        parse_record_bytes(json.dumps(raw, sort_keys=True).encode() + b"\n")
    with pytest.raises(Phase3EError):
        create_study(
            "x",
            Path(study.study_root),
            Path(study.work_root),
            _file(tmp_path, "other-manifest.json"),
            p0=study.p0,
        )


def test_v2_external_source_snapshot_result_binding_and_drift(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    paths = (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    )
    with tempfile.TemporaryDirectory(prefix="projecttown-phase3e-v2-") as temporary:
        source_root = Path(temporary)
        for relative in paths:
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / relative, destination)
        study_root, work_root = tmp_path / "study-v2", tmp_path / "work-v2"
        study_root.mkdir()
        work_root.mkdir()
        source_set = create_source_set_manifest(source_root)
        publish_record(work_root, "source-set-manifest.json", source_set)
        manifest = (
            repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v2.json"
        )
        study = create_study(
            "phase3e-v2-fixture",
            study_root,
            work_root,
            manifest,
            p0=P0Values(
                control_rating_threshold=4,
                participant_arrangement="same participant completes both rounds",
                participant_count=1,
                backup_retention="retain until explicit cleanup authorization",
                release_evidence_format="canonical create-only json sha256 list",
            ),
            material_source_root=source_root,
            source_set_manifest_path=work_root / "source-set-manifest.json",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
        )
        assert verify_record(study)
        task = (
            "生成一份 release-candidate 状态报告，说明已完成能力、当前阻断、工程证据、"
            "用户门禁和回滚步骤；关键结论必须有引用，不执行 Apply。"
        )
        draft = create_draft(source_root, paths, task=task, artifact_kind="report")
        result = generate_result(source_root, draft, draft.contract_hash)
        result_path = _file(work_root, "result.json", result.model_dump_json().encode())
        # Use the module's canonical serializer so the Result carries its embedded manifest.
        from backend.app.material_workflow import serialize_session

        result_path.write_bytes(serialize_session(result))
        export = _file(work_root, "candidate.pdf")
        participant, reviewer = _human(
            work_root,
            "v2",
            participant_identity="participant-user-01",
            reviewer_identity="independent-reviewer-01",
        )
        participant = participant.model_copy(update={"evidence_path": str(export)})
        round_ = create_round(
            study,
            round_id="R2-REPORT-EXPORT",
            binding_status="verified",
            target_is_disposable_external_fixture=False,
            evidence_paths=(
                ("material_manifest", manifest),
                ("source_set_manifest", work_root / "source-set-manifest.json"),
                _meta("result", result_path, result),
                ("preview", _file(work_root, "preview.json")),
                ("citation", _file(work_root, "citation.json")),
                ("pdf_export", export),
            ),
            participant=participant,
            reviewer=reviewer,
            citation_usable=True,
            structural_rewrite=False,
        )
        assert verify_round_for_study(study, round_)
        source_record_path = work_root / "source-set-manifest.json"
        original_source_record = source_record_path.read_bytes()
        source_record_path.write_bytes(original_source_record + b" ")
        assert not verify_record(study)
        source_record_path.write_bytes(original_source_record)
        assert verify_round_for_study(study, round_)
        wrong_draft = create_draft(
            source_root,
            paths,
            task="different fixed task",
            artifact_kind="report",
        )
        wrong_result = generate_result(
            source_root, wrong_draft, wrong_draft.contract_hash
        )
        wrong_result_path = _file(work_root, "wrong-result.json")
        wrong_result_path.write_bytes(serialize_session(wrong_result))
        with pytest.raises(Phase3EError):
            create_round(
                study,
                round_id="R2-REPORT-EXPORT",
                binding_status="verified",
                target_is_disposable_external_fixture=False,
                evidence_paths=(
                    ("material_manifest", manifest),
                    ("source_set_manifest", source_record_path),
                    _meta("result", wrong_result_path, wrong_result),
                    ("preview", _file(work_root, "wrong-preview.json")),
                    ("citation", _file(work_root, "wrong-citation.json")),
                    ("pdf_export", export),
                ),
                participant=participant,
                reviewer=reviewer,
                citation_usable=True,
                structural_rewrite=False,
            )
        (source_root / paths[0]).write_text("source drift", encoding="utf-8")
        assert not verify_record(study)
        assert not verify_round_for_study(study, round_)


def test_v3_round1_requires_real_apply_restore_canonical_events(tmp_path: Path) -> None:
    """V3 binds actual controlled-write ledger events, not free-form observations."""
    from backend.app.controlled_write import (
        BackupManifest,
        PostWriteObservation,
        check,
        parse_event_bytes,
    )

    case_root = Path(tempfile.mkdtemp(prefix="phase3e-v3-materials-"))
    case = ready(case_root)
    initial_target = case["target"].read_bytes()
    work = case["evidence"]
    study_root = case_root / "study-v3"
    study_root.mkdir()
    source_root = Path(tempfile.mkdtemp(prefix="phase3e-v3-sources-"))
    repository = Path(__file__).resolve().parents[2]
    source_paths = (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    )
    for relative in source_paths:
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    source_set = create_source_set_manifest(source_root)
    publish_record(work, "source-set-manifest.json", source_set)
    apply_receipt = controlled_write.apply(
        case["root"],
        case["auth_path"],
        case["result"],
        case["proposal_path"],
        case["target"],
        case["plan"],
        case["ledger"],
        Path(case["auth"].backup_path),
        Path(case["auth"].receipt_path),
    )
    expected_post_bytes = case["target"].read_bytes()
    expected_post = _file(work, "expected-post-image.md", expected_post_bytes)
    assert check(case["auth_path"], case["ledger"]) == "COMMITTED"
    restore_ledger = work / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = work / "restore-authorization.json"
    restore_auth = controlled_write.create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-operation-001",
        "b" * 32,
    )
    restore_receipt = controlled_write.restore(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    assert check(case["auth_path"], case["ledger"]) == "TARGET_CHANGED_AFTER_RECEIPT"
    assert check(restore_auth_path, restore_ledger) == "COMMITTED"

    def event_path(root: Path, operation: str, model: type[object]) -> Path:
        return next(
            item
            for item in (root / operation).glob("*.json")
            if isinstance(parse_event_bytes(item.read_bytes()), model)
        )

    manifest = repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v3.json"
    study = create_study(
        "phase3e-v3-fixture",
        study_root,
        work,
        manifest,
        p0=P0Values(
            control_rating_threshold=4,
            participant_arrangement="same participant completes both rounds",
            participant_count=1,
            backup_retention="retain backup until explicit cleanup authorization",
            release_evidence_format="canonical create-only json sha256 list",
        ),
        material_source_root=source_root,
        source_set_manifest_path=work / "source-set-manifest.json",
        expected_participant_identity="participant-user-01",
        expected_reviewer_identity="independent-reviewer-01",
        round1_contract=create_round1_contract_v3(
            material_root=case["root"],
            source_paths=(),
            no_external_sources=True,
            exact_task="Add grounded details",
            constraints=(),
            target_path=case["target"],
            expected_post_image_path=expected_post,
            restore_executor_label="Verifier",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
        ),
        expected_post_image_path=expected_post,
    )
    assert study.lineage.candidate_profile == CANDIDATE_PROFILE_V3 and verify_record(
        study
    )
    case["target"].write_bytes(b"target drift")
    assert not verify_record(study)
    case["target"].write_bytes(initial_target)
    assert verify_record(study)
    expected_post.write_bytes(b"post drift")
    second_study = case_root / "study-v3-post-drift"
    second_study.mkdir()
    with pytest.raises(Phase3EError):
        create_study(
            "phase3e-v3-post-drift",
            second_study,
            work,
            manifest,
            p0=study.p0,
            material_source_root=source_root,
            source_set_manifest_path=work / "source-set-manifest.json",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
            round1_contract=study.round1_contract,
            expected_post_image_path=expected_post,
        )
    expected_post.write_bytes(expected_post_bytes)
    outside_post_study = case_root / "study-v3-outside-post"
    outside_post_work = case_root / "study-v3-outside-post-work"
    outside_post_study.mkdir()
    outside_post_work.mkdir()
    publish_record(
        outside_post_work,
        "source-set-manifest.json",
        create_source_set_manifest(source_root),
    )
    outside_post = _file(case_root, "outside-post-image.md", expected_post_bytes)
    with pytest.raises(Phase3EError, match="INVALID_R1_EXPECTED_POST_IMAGE"):
        create_study(
            "phase3e-v3-outside-post",
            outside_post_study,
            outside_post_work,
            manifest,
            p0=study.p0,
            material_source_root=source_root,
            source_set_manifest_path=outside_post_work / "source-set-manifest.json",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
            round1_contract=study.round1_contract,
            expected_post_image_path=outside_post,
        )
    overlap_study = case_root / "study-v3-root-overlap"
    overlap_work = case_root / "study-v3-root-overlap-work"
    overlap_study.mkdir()
    overlap_work.mkdir()
    for relative in source_paths:
        destination = case["root"] / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    publish_record(
        overlap_work,
        "source-set-manifest.json",
        create_source_set_manifest(case["root"]),
    )
    overlap_post = _file(overlap_work, "expected-post-image.md", expected_post_bytes)
    with pytest.raises(Phase3EError, match="R1_R2_MATERIAL_ROOT_OVERLAP"):
        create_study(
            "phase3e-v3-root-overlap",
            overlap_study,
            overlap_work,
            manifest,
            p0=study.p0,
            material_source_root=case["root"],
            source_set_manifest_path=overlap_work / "source-set-manifest.json",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
            round1_contract=study.round1_contract,
            expected_post_image_path=overlap_post,
        )
    result = parse_session_bytes(Path(case["result"]).read_bytes())
    plan = parse_apply_plan_bytes(Path(case["plan"]).read_bytes())
    apply_operation = ControlledOperationBindingV3(
        authorization_path=str(case["auth_path"]),
        ledger_root=str(case["ledger"]),
        operation_id=case["auth"].operation_id,
        backup_manifest_path=str(
            event_path(case["ledger"], case["auth"].operation_id, BackupManifest)
        ),
        post_observation_path=str(
            event_path(case["ledger"], case["auth"].operation_id, PostWriteObservation)
        ),
        receipt_path=str(case["auth"].receipt_path),
    )
    restore_operation = ControlledOperationBindingV3(
        authorization_path=str(restore_auth_path),
        ledger_root=str(restore_ledger),
        operation_id=restore_auth.operation_id,
        backup_manifest_path=str(
            event_path(restore_ledger, restore_auth.operation_id, BackupManifest)
        ),
        post_observation_path=str(
            event_path(restore_ledger, restore_auth.operation_id, PostWriteObservation)
        ),
        receipt_path=str(restore_auth.receipt_path),
    )
    participant, reviewer = _human(
        work,
        "v3",
        participant_identity="participant-user-01",
        reviewer_identity="independent-reviewer-01",
    )
    with pytest.raises(Phase3EError, match="PROTOCOL_HOLD"):
        create_round(
            study,
            round_id="R1-CONTROLLED-APPLY",
            binding_status="verified",
            target_is_disposable_external_fixture=True,
            evidence_paths=(
                _meta("result", case["result"], result),
                _meta("apply_plan", case["plan"], plan),
                _meta("executable_proposal", case["proposal_path"], case["proposal"]),
                _meta("user_authorization", case["auth_path"], case["auth"]),
                _meta("restore_authorization", restore_auth_path, restore_auth),
                _meta("apply_receipt", Path(case["auth"].receipt_path), apply_receipt),
                _meta(
                    "restore_receipt", Path(restore_auth.receipt_path), restore_receipt
                ),
            ),
            participant=participant,
            reviewer=reviewer,
            citation_usable=True,
            structural_rewrite=False,
            apply_operation=apply_operation,
            restore_operation=restore_operation,
        )
    # v3 records remain verifiable history; new lifecycle records are held.
    with pytest.raises(ValueError):
        create_round1_contract_v3(
            material_root=case["root"],
            source_paths=(),
            no_external_sources=True,
            exact_task="Add grounded details",
            constraints=(),
            target_path=case["target"],
            expected_post_image_path=case["target"],
            restore_executor_label="Verifier",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
        )
    with pytest.raises(ValueError):
        create_round1_contract_v3(
            material_root=case["root"],
            source_paths=(case["target"],),
            no_external_sources=False,
            exact_task="Add grounded details",
            constraints=(),
            target_path=case["target"],
            expected_post_image_path=expected_post,
            restore_executor_label="Verifier",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
        )


def _v4_case() -> dict[str, object]:
    """Build only disposable, external predecessor and v4 evidence roots."""
    from backend.app.material_workflow import serialize_session

    repository = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix="phase3e-v4-unit-"))
    predecessor_root = root / "predecessor"
    predecessor_root.mkdir()
    predecessor_fixture = predecessor_root / "fixture"
    predecessor_fixture.mkdir()
    predecessor_case = ready(predecessor_fixture)
    predecessor_work = predecessor_case["evidence"]
    predecessor_study_root = predecessor_fixture / "predecessor-study"
    predecessor_study_root.mkdir()
    source_root = root / "sources"
    source_root.mkdir()
    paths = (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    )
    for relative in paths:
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    source_set = create_source_set_manifest(source_root)
    publish_record(predecessor_work, "source-set-manifest.json", source_set)
    apply_receipt = controlled_write.apply(
        predecessor_case["root"],
        predecessor_case["auth_path"],
        predecessor_case["result"],
        predecessor_case["proposal_path"],
        predecessor_case["target"],
        predecessor_case["plan"],
        predecessor_case["ledger"],
        Path(predecessor_case["auth"].backup_path),
        Path(predecessor_case["auth"].receipt_path),
    )
    expected_post = _file(
        predecessor_work,
        "expected-post-image.md",
        predecessor_case["target"].read_bytes(),
    )
    restore_ledger = predecessor_work / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = predecessor_work / "restore-authorization.json"
    restore_auth = controlled_write.create_restore_authorization(
        predecessor_case["root"],
        Path(predecessor_case["auth"].receipt_path),
        predecessor_case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-operation-v4-001",
        "c" * 32,
    )
    restore_receipt = controlled_write.restore(
        predecessor_case["root"],
        restore_auth_path,
        predecessor_case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    predecessor_study = create_study(
        "phase3e-v4-predecessor",
        predecessor_study_root,
        predecessor_work,
        repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v3.json",
        p0=P0Values(
            control_rating_threshold=4,
            participant_arrangement="same participant completes both rounds",
            participant_count=1,
            backup_retention="retain until explicit cleanup authorization",
            release_evidence_format="canonical create-only json sha256 list",
        ),
        material_source_root=source_root,
        source_set_manifest_path=predecessor_work / "source-set-manifest.json",
        expected_participant_identity="participant-user-01",
        expected_reviewer_identity="independent-reviewer-01",
        round1_contract=create_round1_contract_v3(
            material_root=predecessor_case["root"],
            source_paths=(),
            no_external_sources=True,
            exact_task="Add grounded details",
            constraints=(),
            target_path=predecessor_case["target"],
            expected_post_image_path=expected_post,
            restore_executor_label="Verifier",
            expected_participant_identity="participant-user-01",
            expected_reviewer_identity="independent-reviewer-01",
        ),
        expected_post_image_path=expected_post,
    )
    publish_record(predecessor_study_root, "study.json", predecessor_study)
    predecessor_result = parse_session_bytes(
        Path(predecessor_case["result"]).read_bytes()
    )
    predecessor_plan = parse_apply_plan_bytes(
        Path(predecessor_case["plan"]).read_bytes()
    )
    predecessor_paths = {
        "v3_study": predecessor_study_root / "study.json",
        "result": predecessor_case["result"],
        "apply_plan": predecessor_case["plan"],
        "executable_proposal": predecessor_case["proposal_path"],
        "apply_authorization": predecessor_case["auth_path"],
        "restore_authorization": restore_auth_path,
        "apply_receipt": Path(predecessor_case["auth"].receipt_path),
        "restore_receipt": Path(restore_auth.receipt_path),
        "apply_backup": Path(predecessor_case["auth"].backup_path),
        "restore_backup": Path(restore_auth.backup_path),
    }
    study_root, work_root = root / "v4-study", root / "v4-work"
    study_root.mkdir()
    work_root.mkdir()
    v4_source_set = create_source_set_manifest(source_root)
    publish_record(work_root, "source-set-manifest.json", v4_source_set)
    predecessor_evidence = tuple(
        PredecessorEvidenceV4(
            role=role,
            path=str(path),
            bytes_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            inherited_historical_evidence=True,
        )
        for role, path in predecessor_paths.items()
    )
    study = create_study(
        "phase3e-v4-fixture",
        study_root,
        work_root,
        repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v4.json",
        p0=P0Values(
            control_rating_threshold=4,
            participant_arrangement="same participant completes both rounds",
            participant_count=1,
            backup_retention="retain until explicit cleanup authorization",
            release_evidence_format="canonical create-only json sha256 list",
        ),
        material_source_root=source_root,
        source_set_manifest_path=work_root / "source-set-manifest.json",
        expected_participant_identity="participant-user-01",
        round1_contract=Round1ContractV4(
            material_root=str(predecessor_case["root"]),
            exact_task="Add grounded details",
            target_path=str(predecessor_case["target"]),
            expected_participant_identity="participant-user-01",
            predecessor_study_hash=predecessor_study.study_hash,
            predecessor_evidence=predecessor_evidence,
        ),
        protocol_v4=True,
    )
    participant_paths = {
        "r1": _file(work_root, "participant-r1.txt"),
        "r2": _file(work_root, "r2-candidate.pdf", b"%PDF-1.4\n"),
    }
    engineering_paths = {
        "r1": _file(work_root, "engineering-r1.txt"),
        "r2": _file(work_root, "engineering-r2.txt"),
    }

    def human(round_key: str) -> tuple[ParticipantEvidenceV4, EngineeringAcceptanceV4]:
        return (
            ParticipantEvidenceV4(
                participant_identity="participant-user-01",
                disposition="retained",
                elapsed_seconds=120,
                actions=("open_task",),
                notes="participant supplied evidence",
                timestamp="2026-09-01T10:00:00+08:00",
                evidence_path=str(participant_paths[round_key]),
                control_rating=4,
                citation_usable=True,
                structural_rewrite=False,
            ),
            create_engineering_acceptance_v4(
                outcome="PASS",
                verifier_identity="sol-engineering-01",
                checks=("canonical binding",),
                notes="engineering acceptance",
                actions=("check",),
                timestamp="2026-09-01T10:01:00+08:00",
                evidence_path=str(engineering_paths[round_key]),
                citation_traceable=True,
                citation_usable=True,
                blocking_defect=False,
            ),
        )

    r1_participant, r1_engineering = human("r1")
    r1 = create_round(
        study,
        round_id=ROUND_IDS[0],
        binding_status="verified",
        target_is_disposable_external_fixture=True,
        evidence_paths=(
            _meta("result", predecessor_paths["result"], predecessor_result),
            _meta("apply_plan", predecessor_paths["apply_plan"], predecessor_plan),
            _meta(
                "executable_proposal",
                predecessor_paths["executable_proposal"],
                predecessor_case["proposal"],
            ),
            _meta(
                "user_authorization",
                predecessor_paths["apply_authorization"],
                predecessor_case["auth"],
            ),
            _meta(
                "restore_authorization",
                predecessor_paths["restore_authorization"],
                restore_auth,
            ),
            _meta("apply_receipt", predecessor_paths["apply_receipt"], apply_receipt),
            _meta(
                "restore_receipt", predecessor_paths["restore_receipt"], restore_receipt
            ),
            ("backup", predecessor_paths["apply_backup"]),
            ("restore_backup", predecessor_paths["restore_backup"]),
        ),
        participant=r1_participant,
        engineering_acceptance=r1_engineering,
    )
    task = study.round2_source.fixed_task
    draft = create_draft(source_root, paths, task=task, artifact_kind="report")
    result = generate_result(source_root, draft, draft.contract_hash)
    result_path = _file(work_root, "r2-result.json", serialize_session(result))
    r2_participant, r2_engineering = human("r2")
    r2 = create_round(
        study,
        round_id=ROUND_IDS[1],
        binding_status="verified",
        target_is_disposable_external_fixture=False,
        evidence_paths=(
            (
                "material_manifest",
                repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v4.json",
            ),
            ("source_set_manifest", work_root / "source-set-manifest.json"),
            _meta("result", result_path, result),
            ("preview", _file(work_root, "preview.json")),
            ("citation", _file(work_root, "citation.json")),
            ("pdf_export", participant_paths["r2"]),
        ),
        participant=r2_participant,
        engineering_acceptance=r2_engineering,
    )
    return {
        "root": root,
        "study": study,
        "study_root": study_root,
        "work": work_root,
        "r1": r1,
        "r2": r2,
        "human": human,
        "participant_paths": participant_paths,
        "engineering_paths": engineering_paths,
        "predecessor_paths": predecessor_paths,
        "source_root": source_root,
    }


def test_v4_participant_engineering_chain_tamper_and_fail_closed() -> None:
    """V4 binds inherited v3 history while requiring only participant plus engineering gates."""
    case = _v4_case()
    try:
        study, r1, r2 = case["study"], case["r1"], case["r2"]
        assert study.lineage.candidate_profile == CANDIDATE_PROFILE_V4
        assert study.schema_version == "v3-phase3e-study-v4" and verify_record(study)
        assert verify_round_for_study(study, r1) and verify_round_for_study(study, r2)
        assert "reviewer" not in type(r1).model_fields
        assert "reviewer" not in type(r2).model_fields
        summary = create_summary(study, (r1, r2))
        assert summary.gate_state == "criteria_met_awaiting_user_rc_acceptance"
        assert verify_summary_for_study(study, (r1, r2), summary)
        decision = create_user_rc_decision(
            study,
            summary,
            decision="ACCEPT",
            user_timestamp="2026-09-01T10:02:00+08:00",
            evidence_path=_file(case["work"], "user-decision.txt"),
            notes="user decision",
        )
        assert verify_user_decision_for_study(study, summary, decision)
        assert status_projection(study, (r1, r2), summary)["blockers"] == (
            "WAITING_USER_RC_DECISION",
        )

        participant_path = case["participant_paths"]["r1"]
        original = participant_path.read_bytes()
        participant_path.write_bytes(b"drift")
        assert not verify_round_for_study(study, r1)
        participant_path.write_bytes(original)
        assert verify_round_for_study(study, r1)
        predecessor_path = case["predecessor_paths"]["apply_plan"]
        original = predecessor_path.read_bytes()
        predecessor_path.write_bytes(original + b" ")
        assert not verify_record(study)
        predecessor_path.write_bytes(original)
        assert verify_record(study)

        engineering_path = case["engineering_paths"]["r1"]
        original = engineering_path.read_bytes()
        engineering_path.write_bytes(b"drift")
        assert not verify_round_for_study(study, r1)
        engineering_path.write_bytes(original)
        assert verify_round_for_study(study, r1)
        source_path = case["source_root"] / "docs/v3-phase-3.md"
        original = source_path.read_bytes()
        source_path.write_bytes(original + b"\nsource drift")
        assert not verify_record(study)
        source_path.write_bytes(original)
        assert verify_record(study)

        participant, engineering = case["human"]("r1")
        r1_evidence = tuple(
            (item.kind, Path(item.path), item.schema_version, item.record_hash)
            for item in r1.evidence
        )
        r2_evidence = tuple(
            (item.kind, Path(item.path), item.schema_version, item.record_hash)
            for item in r2.evidence
        )
        from backend.app.material_workflow import serialize_session

        result_binding = next(item for item in r2.evidence if item.kind == "result")
        good_result = parse_session_bytes(Path(result_binding.path).read_bytes())
        wrong_draft = create_draft(
            case["source_root"],
            good_result.draft.selections,
            task="wrong v4 fixed task",
            artifact_kind="report",
        )
        wrong_result = generate_result(
            case["source_root"], wrong_draft, wrong_draft.contract_hash
        )
        wrong_result_path = _file(
            case["work"], "wrong-r2-result.json", serialize_session(wrong_result)
        )
        wrong_r2_evidence = tuple(
            _meta("result", wrong_result_path, wrong_result)
            if item.kind == "result"
            else (item.kind, Path(item.path), item.schema_version, item.record_hash)
            for item in r2.evidence
        )
        with pytest.raises(Phase3EError, match="UNPROVEN_VERIFIED_BINDING"):
            create_round(
                study,
                round_id=ROUND_IDS[1],
                binding_status="verified",
                target_is_disposable_external_fixture=False,
                evidence_paths=wrong_r2_evidence,
                participant=r2.participant,
                engineering_acceptance=r2.engineering_acceptance,
            )
        wrong_bindings = tuple(
            item.model_copy(
                update={
                    "path": str(wrong_result_path),
                    "bytes_sha256": hashlib.sha256(
                        wrong_result_path.read_bytes()
                    ).hexdigest(),
                    "schema_version": wrong_result.schema_version,
                    "record_hash": wrong_result.session_hash,
                }
            )
            if item.kind == "result"
            else item
            for item in r2.evidence
        )
        pre_fix_values = r2.model_dump()
        pre_fix_values["evidence"] = wrong_bindings
        pre_fix_values.pop("round_hash")
        pre_fix_round = _make(
            Phase3ERoundV4,
            ROUND_HASH_DOMAIN_V4,
            "round_hash",
            pre_fix_values,
        )
        assert verify_record(pre_fix_round)
        assert not verify_round_for_study(study, pre_fix_round)
        with pytest.raises(Phase3EError, match="UNPROVEN_VERIFIED_BINDING"):
            failed_engineering = create_engineering_acceptance_v4(
                outcome="FAIL",
                verifier_identity=engineering.verifier_identity,
                checks=engineering.checks,
                notes=engineering.notes,
                actions=engineering.actions,
                timestamp=engineering.timestamp,
                evidence_path=engineering.evidence_path,
                evidence_sha256=engineering.evidence_sha256,
                citation_traceable=engineering.citation_traceable,
                citation_usable=engineering.citation_usable,
                blocking_defect=engineering.blocking_defect,
            )
            create_round(
                study,
                round_id=ROUND_IDS[0],
                binding_status="verified",
                target_is_disposable_external_fixture=True,
                evidence_paths=r1_evidence,
                participant=participant,
                engineering_acceptance=failed_engineering,
            )
        with pytest.raises(Phase3EError, match="PARTICIPANT_EVIDENCE_PATH_MISMATCH"):
            create_round(
                study,
                round_id=ROUND_IDS[1],
                binding_status="verified",
                target_is_disposable_external_fixture=False,
                evidence_paths=r2_evidence,
                participant=participant,
                engineering_acceptance=engineering,
            )
        with pytest.raises(Phase3EError, match="INVALID_ENGINEERING_ACCEPTANCE"):
            create_round(
                study,
                round_id=ROUND_IDS[0],
                binding_status="verified",
                target_is_disposable_external_fixture=True,
                evidence_paths=r1_evidence,
                participant=participant,
                engineering_acceptance=engineering.model_copy(
                    update={"acceptance_hash": "0" * 64}
                ),
            )
        for update, blocker in (
            ({"disposition": "not_kept"}, "PARTICIPANT_NOT_RETAINED"),
            ({"control_rating": 3}, "RATING_BELOW_THRESHOLD"),
            ({"citation_usable": False}, "CITATION_UNUSABLE"),
            ({"structural_rewrite": True}, "STRUCTURAL_REWRITE"),
        ):
            bad_round = create_round(
                study,
                round_id=ROUND_IDS[0],
                binding_status="verified",
                target_is_disposable_external_fixture=True,
                evidence_paths=r1_evidence,
                participant=participant.model_copy(update=update),
                engineering_acceptance=engineering,
            )
            bad_summary = create_summary(study, (bad_round, r2))
            assert bad_summary.gate_state == "criteria_not_met"
            assert any(blocker in value for value in bad_summary.blockers)
        with pytest.raises(ValueError):
            Round1ContractV4(
                material_root=study.round1_contract.material_root,
                exact_task=study.round1_contract.exact_task,
                target_path=study.round1_contract.target_path,
                expected_participant_identity="participant-user-01",
                predecessor_study_hash=study.round1_contract.predecessor_study_hash,
                predecessor_evidence=study.round1_contract.predecessor_evidence[:-1],
            )
        duplicate = study.round1_contract.predecessor_evidence
        with pytest.raises(ValueError):
            Round1ContractV4(
                material_root=study.round1_contract.material_root,
                exact_task=study.round1_contract.exact_task,
                target_path=study.round1_contract.target_path,
                expected_participant_identity="participant-user-01",
                predecessor_study_hash=study.round1_contract.predecessor_study_hash,
                predecessor_evidence=duplicate[:-1] + (duplicate[0],),
            )
        legacy_root = case["root"] / "legacy"
        legacy_root.mkdir()
        _, _, _, legacy_r2 = _case(legacy_root)
        with pytest.raises(Phase3EError, match="ROUND_BINDING_MISMATCH"):
            create_summary(study, (r1, legacy_r2))
        publish_record(case["study_root"], "study.json", study)
        with pytest.raises(Phase3EError, match="INVALID_OUTPUT_PATH"):
            publish_record(case["study_root"], "study.json", study)
    finally:
        shutil.rmtree(case["root"], ignore_errors=True)
