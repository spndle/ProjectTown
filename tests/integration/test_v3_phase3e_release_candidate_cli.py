from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from backend.app import controlled_write
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    parse_session_bytes,
    serialize_session,
)
from backend.app.phase3e_release_candidate import (
    ROUND_HASH_DOMAIN_V4,
    P0Values,
    Phase3ERoundV4,
    _make,
    create_source_set_manifest,
    create_study,
    load_record,
    publish_record,
    serialize_record,
)
from scripts import run_v3_phase3e_release_candidate as cli
from tests.controlled_write_support import ready
from tests.unit.test_phase3e_release_candidate import _v4_case


def _run(capsys, args: list[str]) -> tuple[int, dict[str, object]]:
    code = cli.main(args)
    captured = capsys.readouterr()
    assert captured.err == ""
    value = json.loads(captured.out)
    assert value["schema_version"] == "v3-phase3e-cli-status-v1"
    assert value["paths_disclosed"] is False and value["write_performed"] is False
    assert all(item == 0 for item in value["offline_calls"].values())
    return code, value


def _manifest() -> Path:
    return (
        Path(__file__).parents[2]
        / "examples"
        / "v3-phase-3"
        / "projecttown-phase3e-manifest-v1.json"
    )


def _study(study: Path, work: Path) -> list[str]:
    return [
        "study-create",
        "--study-root",
        str(study),
        "--work-root",
        str(work),
        "--study-id",
        "phase3e-cli-fixture",
        "--manifest",
        str(_manifest()),
        "--control-rating-threshold",
        "4",
        "--participant-arrangement",
        "two independent reviewers",
        "--participant-count",
        "2",
        "--backup-retention",
        "retain until user decision",
        "--release-evidence-format",
        "canonical json",
    ]


def _human(work: Path, label: str, evidence: Path) -> list[str]:
    reviewer = work / f"reviewer-{label}.json"
    reviewer.write_text("{}", encoding="utf-8")
    return [
        "--participant-disposition",
        "retained",
        "--participant-identity",
        f"participant-{label}",
        "--participant-elapsed-seconds",
        "120",
        "--participant-action",
        "open_task",
        "--participant-notes",
        "participant notes",
        "--participant-timestamp",
        "2026-08-31T10:00:00+08:00",
        "--participant-evidence-path",
        str(evidence),
        "--reviewer-identity",
        f"reviewer-{label}",
        "--reviewer-disposition",
        "PASS",
        "--reviewer-executability-rating",
        "4",
        "--reviewer-readability-rating",
        "4",
        "--reviewer-control-rating",
        "4",
        "--reviewer-citation-traceability-rating",
        "4",
        "--reviewer-fixed-answer",
        "rerun",
        "--reviewer-fixed-answer",
        "history",
        "--reviewer-fixed-answer",
        "user",
        "--reviewer-fixed-answer",
        "next",
        "--reviewer-notes",
        "reviewer notes",
        "--reviewer-timestamp",
        "2026-08-31T10:01:00+08:00",
        "--reviewer-evidence-path",
        str(reviewer),
        "--citation-usable",
        "true",
        "--structural-rewrite",
        "false",
    ]


def _prepared(tmp_path: Path):
    case = ready(tmp_path)
    work, study = case["evidence"], tmp_path / "study"
    study.mkdir()
    controlled_write.apply(
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
    restore_ledger, restore_auth_path = (
        work / "restore-ledger",
        work / "restore-authorization.json",
    )
    restore_ledger.mkdir()
    restore_auth = controlled_write.create_restore_authorization(
        case["root"],
        Path(case["auth"].receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-operation-001",
        "b" * 32,
    )
    controlled_write.restore(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    paths = {
        "result": case["result"],
        "apply_plan": case["plan"],
        "executable_proposal": case["proposal_path"],
        "user_authorization": case["auth_path"],
        "restore_authorization": restore_auth_path,
        "ledger": next((case["ledger"] / case["auth"].operation_id).glob("*.json")),
        "backup": Path(case["auth"].backup_path),
        "restore_backup": Path(restore_auth.backup_path),
        "apply_receipt": Path(case["auth"].receipt_path),
        "restore_receipt": Path(restore_auth.receipt_path),
    }
    for key in ("reconcile_observation", "restore_observation"):
        path = work / f"{key}.json"
        path.write_text("{}", encoding="utf-8")
        paths[key] = path
    return case, study, work, paths


def test_v3_help_and_v2_round_creation_hold(tmp_path, capsys) -> None:
    """The additive CLI exposes v3 bindings while the flawed v2 writer is held."""
    help_text = (
        cli._parser()
        ._subparsers._group_actions[0]
        .choices["study-create"]
        .format_help()
    )
    assert "--protocol-v3" in help_text
    assert "--round1-expected-post-image" in help_text
    assert "--round1-initial-sha256" not in help_text
    assert "--round1-expected-post-sha256" not in help_text
    assert cli._round1_constraint("Scope=local only") == ("Scope", "local only")
    with pytest.raises(cli.CliError):
        cli._round1_constraint("invalid")
    round_help = (
        cli._parser()
        ._subparsers._group_actions[0]
        .choices["round-create"]
        .format_help()
    )
    assert "--apply-post-observation" in round_help
    case = ready(tmp_path)
    study_root = tmp_path / "study-v2"
    study_root.mkdir()
    source_root = Path(tempfile.mkdtemp(prefix="phase3e-cli-v2-sources-"))
    repository = Path(__file__).resolve().parents[2]
    for relative in (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    ):
        target = source_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, target)
    source_set = create_source_set_manifest(source_root)
    publish_record(case["evidence"], "source-set-manifest.json", source_set)
    study = create_study(
        "phase3e-cli-v2-hold",
        study_root,
        case["evidence"],
        repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v2.json",
        p0=P0Values(
            control_rating_threshold=4,
            participant_arrangement="same participant completes both rounds",
            participant_count=1,
            backup_retention="retain until explicit cleanup authorization",
            release_evidence_format="canonical create-only json sha256 list",
        ),
        material_source_root=source_root,
        source_set_manifest_path=case["evidence"] / "source-set-manifest.json",
        expected_participant_identity="participant-user-01",
        expected_reviewer_identity="independent-reviewer-01",
    )
    publish_record(study_root, "study.json", study)
    code, status = _run(
        capsys,
        [
            "round-create",
            "--study-root",
            str(study_root),
            "--round-id",
            "R1-CONTROLLED-APPLY",
            "--binding-status",
            "verified",
            "--target-is-disposable-external-fixture",
            "true",
        ],
    )
    assert code == 2 and status["code"] == "MISSING_REQUIRED_EVIDENCE"


def test_v3_study_create_routes_to_additive_profile(tmp_path, capsys) -> None:
    case_root = Path(tempfile.mkdtemp(prefix="phase3e-cli-v3-materials-"))
    case = ready(case_root)
    study_root = case_root / "study-v3"
    study_root.mkdir()
    source_root = Path(tempfile.mkdtemp(prefix="phase3e-cli-v3-sources-"))
    repository = Path(__file__).resolve().parents[2]
    for relative in (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    ):
        target = source_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, target)
    auth = case["auth"]
    controlled_write.apply(
        case["root"],
        case["auth_path"],
        case["result"],
        case["proposal_path"],
        case["target"],
        case["plan"],
        case["ledger"],
        Path(auth.backup_path),
        Path(auth.receipt_path),
    )
    expected_post = case["evidence"] / "expected-post-image.md"
    expected_post.write_bytes(case["target"].read_bytes())
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_auth_path = case["evidence"] / "restore-authorization.json"
    restore_auth = controlled_write.create_restore_authorization(
        case["root"],
        Path(auth.receipt_path),
        case["target"],
        restore_ledger,
        restore_auth_path,
        "restore-operation-001",
        "b" * 32,
    )
    controlled_write.restore(
        case["root"],
        restore_auth_path,
        case["target"],
        restore_ledger,
        Path(restore_auth.backup_path),
        Path(restore_auth.receipt_path),
    )
    code, status = _run(
        capsys,
        [
            "study-create",
            "--study-root",
            str(study_root),
            "--work-root",
            str(case["evidence"]),
            "--study-id",
            "phase3e-cli-v3",
            "--manifest",
            str(
                repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v3.json"
            ),
            "--control-rating-threshold",
            "4",
            "--participant-arrangement",
            "same participant completes both rounds",
            "--participant-count",
            "1",
            "--backup-retention",
            "retain until explicit cleanup authorization",
            "--release-evidence-format",
            "canonical create-only json sha256 list",
            "--material-source-root",
            str(source_root),
            "--expected-participant-identity",
            "participant-user-01",
            "--expected-reviewer-identity",
            "independent-reviewer-01",
            "--protocol-v3",
            "--round1-material-root",
            str(case["root"]),
            "--round1-no-external-sources",
            "--round1-exact-task",
            "Add grounded details",
            "--round1-target",
            str(case["target"]),
            "--round1-expected-post-image",
            str(expected_post),
            "--restore-executor-label",
            "Verifier",
        ],
    )
    assert code == 0 and status["candidate_profile"] == "projecttown-phase3e-rc-v3"
    assert status["record_schema_version"] == "v3-phase3e-study-v3"

    from backend.app.controlled_write import (
        BackupManifest,
        PostWriteObservation,
        parse_event_bytes,
    )

    def event_path(root: Path, operation: str, model: type[object]) -> Path:
        return next(
            item
            for item in (root / operation).glob("*.json")
            if isinstance(parse_event_bytes(item.read_bytes()), model)
        )

    r1_args = [
        "round-create",
        "--study-root",
        str(study_root),
        "--round-id",
        "R1-CONTROLLED-APPLY",
        "--binding-status",
        "verified",
        "--target-is-disposable-external-fixture",
        "true",
        "--result",
        str(case["result"]),
        "--apply-plan",
        str(case["plan"]),
        "--executable-proposal",
        str(case["proposal_path"]),
        "--user-authorization",
        str(case["auth_path"]),
        "--restore-authorization",
        str(restore_auth_path),
        "--apply-receipt",
        str(auth.receipt_path),
        "--restore-receipt",
        str(restore_auth.receipt_path),
        "--apply-ledger-root",
        str(case["ledger"]),
        "--apply-operation-id",
        auth.operation_id,
        "--apply-backup-manifest",
        str(event_path(case["ledger"], auth.operation_id, BackupManifest)),
        "--apply-post-observation",
        str(event_path(case["ledger"], auth.operation_id, PostWriteObservation)),
        "--restore-ledger-root",
        str(restore_ledger),
        "--restore-operation-id",
        restore_auth.operation_id,
        "--restore-backup-manifest",
        str(event_path(restore_ledger, restore_auth.operation_id, BackupManifest)),
        "--restore-post-observation",
        str(
            event_path(restore_ledger, restore_auth.operation_id, PostWriteObservation)
        ),
        "--engineering-only",
    ]
    code, r1_status = _run(capsys, r1_args)
    assert code == 2 and r1_status["code"] == "PROTOCOL_HOLD"
    assert not (study_root / "R1-CONTROLLED-APPLY.json").exists()
    # The v3 Study remains a read-only/checkable historical protocol.
    assert (
        _run(capsys, ["check", "--study-root", str(study_root), "--record", "study"])[0]
        == 0
    )
    outside_study = case_root / "study-v3-outside-post"
    outside_work = case_root / "study-v3-outside-post-work"
    outside_study.mkdir()
    outside_work.mkdir()
    outside_post = case_root / "outside-post-image.md"
    outside_post.write_bytes(expected_post.read_bytes())
    v3_base = [
        "study-create",
        "--study-id",
        "phase3e-cli-v3-boundary",
        "--manifest",
        str(repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v3.json"),
        "--control-rating-threshold",
        "4",
        "--participant-arrangement",
        "same participant completes both rounds",
        "--participant-count",
        "1",
        "--backup-retention",
        "retain until explicit cleanup authorization",
        "--release-evidence-format",
        "canonical create-only json sha256 list",
        "--material-source-root",
        str(source_root),
        "--expected-participant-identity",
        "participant-user-01",
        "--expected-reviewer-identity",
        "independent-reviewer-01",
        "--protocol-v3",
        "--round1-material-root",
        str(case["root"]),
        "--round1-no-external-sources",
        "--round1-exact-task",
        "Add grounded details",
        "--round1-target",
        str(case["target"]),
        "--restore-executor-label",
        "Verifier",
    ]
    outside_code, outside_status = _run(
        capsys,
        [
            *v3_base,
            "--study-root",
            str(outside_study),
            "--work-root",
            str(outside_work),
            "--round1-expected-post-image",
            str(outside_post),
        ],
    )
    assert (
        outside_code == 2 and outside_status["code"] == "INVALID_R1_EXPECTED_POST_IMAGE"
    )
    overlap_study = case_root / "study-v3-root-overlap"
    overlap_work = case_root / "study-v3-root-overlap-work"
    overlap_study.mkdir()
    overlap_work.mkdir()
    for relative in (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    ):
        destination = case["root"] / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    overlap_post = overlap_work / "expected-post-image.md"
    overlap_post.write_bytes(expected_post.read_bytes())
    overlap_code, overlap_status = _run(
        capsys,
        [
            *v3_base,
            "--study-root",
            str(overlap_study),
            "--work-root",
            str(overlap_work),
            "--material-source-root",
            str(case["root"]),
            "--round1-expected-post-image",
            str(overlap_post),
        ],
    )
    assert overlap_code == 2 and overlap_status["code"] == "R1_R2_MATERIAL_ROOT_OVERLAP"


def test_full_cli_chain_human_summary_decision_and_redaction(tmp_path, capsys) -> None:
    case, study, work, r1 = _prepared(tmp_path)
    assert _run(capsys, _study(study, work))[0] == 0
    participant = work / "participant-r1.pdf"
    participant.write_bytes(b"r1")
    r1_args = [
        "round-create",
        "--study-root",
        str(study),
        "--round-id",
        "R1-CONTROLLED-APPLY",
        "--binding-status",
        "verified",
        "--target-is-disposable-external-fixture",
        "true",
    ]
    for kind, path in r1.items():
        r1_args.extend(["--" + kind.replace("_", "-"), str(path)])
    assert _run(capsys, r1_args + _human(work, "r1", participant))[0] == 0
    preview, citation, export = (
        work / "preview.json",
        work / "citation.json",
        work / "candidate.pdf",
    )
    preview.write_text("{}", encoding="utf-8")
    citation.write_text("{}", encoding="utf-8")
    export.write_bytes(b"%PDF-1.4\n")
    r2_args = [
        "round-create",
        "--study-root",
        str(study),
        "--round-id",
        "R2-REPORT-EXPORT",
        "--binding-status",
        "verified",
        "--target-is-disposable-external-fixture",
        "false",
        "--material-manifest",
        str(_manifest()),
        "--result",
        str(case["result"]),
        "--preview",
        str(preview),
        "--citation",
        str(citation),
        "--pdf-export",
        str(export),
    ]
    assert _run(capsys, r2_args + _human(work, "r2", export))[0] == 0
    assert _run(capsys, ["summary-create", "--study-root", str(study)])[0] == 0
    user = work / "user.json"
    user.write_text("{}", encoding="utf-8")
    code, state = _run(
        capsys,
        [
            "user-rc-decision-create",
            "--study-root",
            str(study),
            "--decision",
            "ACCEPT",
            "--user-timestamp",
            "2026-08-31T10:02:00+08:00",
            "--evidence-path",
            str(user),
            "--notes",
            "user decision",
        ],
    )
    assert code == 0 and state["outcome_gate"] == "rc_accepted_pending_version_gate"
    assert (
        _run(
            capsys,
            [
                "check",
                "--study-root",
                str(study),
                "--record",
                "round",
                "--round-id",
                "R1-CONTROLLED-APPLY",
            ],
        )[0]
        == 0
    )
    assert (
        _run(capsys, ["check", "--study-root", str(study), "--record", "summary"])[0]
        == 0
    )
    assert (
        _run(
            capsys,
            ["check", "--study-root", str(study), "--record", "user-rc-decision"],
        )[0]
        == 0
    )
    original = export.read_bytes()
    export.write_bytes(original + b"drift")
    assert (
        _run(
            capsys,
            [
                "check",
                "--study-root",
                str(study),
                "--record",
                "round",
                "--round-id",
                "R2-REPORT-EXPORT",
            ],
        )[0]
        == 2
    )
    export.write_bytes(original)
    assert (
        _run(
            capsys,
            [
                "check",
                "--study-root",
                str(study),
                "--record",
                "round",
                "--round-id",
                "R2-REPORT-EXPORT",
            ],
        )[0]
        == 0
    )


def test_invalid_arguments_wrong_kind_and_duplicate_are_json_rejections(
    tmp_path, capsys
) -> None:
    case, study, work, _ = _prepared(tmp_path)
    assert _run(capsys, _study(study, work))[0] == 0
    assert _run(capsys, _study(study, work))[0] == 2
    assert _run(capsys, ["unknown"])[0] == 2
    assert _run(capsys, ["status", "--study-root", "relative"])[0] == 2
    assert (
        _run(
            capsys,
            [
                "user-rc-decision-create",
                "--study-root",
                str(study),
                "--decision",
                "ACCEPT",
                "--user-timestamp",
                "2026-08-31T10:02:00+08:00",
                "--evidence-path",
                str(work / "missing.json"),
                "--notes",
                "premature",
            ],
        )[0]
        == 2
    )
    assert (
        _run(
            capsys,
            [
                "round-create",
                "--study-root",
                str(study),
                "--round-id",
                "R2-REPORT-EXPORT",
                "--binding-status",
                "missing",
                "--target-is-disposable-external-fixture",
                "false",
                "--apply-plan",
                str(case["plan"]),
                "--engineering-only",
            ],
        )[0]
        == 2
    )
    (study / "summary.json").write_bytes((study / "study.json").read_bytes())
    assert (
        _run(capsys, ["check", "--study-root", str(study), "--record", "summary"])[0]
        == 2
    )
    assert case["result"].exists()


def test_v2_cli_study_binds_external_source_snapshot_and_rejects_drift(
    tmp_path, capsys
) -> None:
    repository = Path(__file__).parents[2]
    source_paths = (
        "docs/v3-current-code-audit-2026-08-31.md",
        "docs/v3-phase-3.md",
        "docs/v3-phase-3e.md",
        "docs/v3-product-direction.md",
    )
    with tempfile.TemporaryDirectory(prefix="projecttown-phase3e-v2-cli-") as temporary:
        source_root = Path(temporary)
        for relative in source_paths:
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / relative, destination)
        study, work = tmp_path / "v2-study", tmp_path / "v2-work"
        study.mkdir()
        work.mkdir()
        manifest = (
            repository / "examples/v3-phase-3/projecttown-phase3e-manifest-v2.json"
        )
        code, status = _run(
            capsys,
            [
                "study-create",
                "--study-root",
                str(study),
                "--work-root",
                str(work),
                "--study-id",
                "phase3e-v2-cli-fixture",
                "--manifest",
                str(manifest),
                "--control-rating-threshold",
                "4",
                "--participant-arrangement",
                "same participant completes both rounds",
                "--participant-count",
                "1",
                "--backup-retention",
                "retain until explicit cleanup authorization",
                "--release-evidence-format",
                "canonical create-only json sha256 list",
                "--material-source-root",
                str(source_root),
                "--expected-participant-identity",
                "participant-user-01",
                "--expected-reviewer-identity",
                "independent-reviewer-01",
            ],
        )
        assert code == 0 and status["candidate_profile"] == "projecttown-phase3e-rc-v2"
        assert (work / "source-set-manifest.json").is_file()
        assert (
            _run(capsys, ["check", "--study-root", str(study), "--record", "study"])[0]
            == 0
        )
        (source_root / source_paths[0]).write_text("source drift", encoding="utf-8")
        assert (
            _run(capsys, ["check", "--study-root", str(study), "--record", "study"])[0]
            == 2
        )


def test_v2_study_create_recovers_from_matching_precommitted_source_set(
    tmp_path, capsys
) -> None:
    repository = Path(__file__).parents[2]
    with tempfile.TemporaryDirectory(
        prefix="projecttown-phase3e-v2-recovery-"
    ) as temporary:
        external = Path(temporary)
        source_root = external / "source-recovery"
        for relative in (
            "docs/v3-current-code-audit-2026-08-31.md",
            "docs/v3-phase-3.md",
            "docs/v3-phase-3e.md",
            "docs/v3-product-direction.md",
        ):
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / relative, destination)
        study, work = external / "v2-study-recovery", external / "v2-work-recovery"
        study.mkdir()
        work.mkdir()
        from backend.app.phase3e_release_candidate import (
            create_source_set_manifest,
            publish_record,
        )

        publish_record(
            work, "source-set-manifest.json", create_source_set_manifest(source_root)
        )
        code, state = _run(
            capsys,
            [
                "study-create",
                "--study-root",
                str(study),
                "--work-root",
                str(work),
                "--study-id",
                "phase3e-v2-recovery",
                "--manifest",
                str(
                    repository
                    / "examples/v3-phase-3/projecttown-phase3e-manifest-v2.json"
                ),
                "--control-rating-threshold",
                "4",
                "--participant-arrangement",
                "same participant completes both rounds",
                "--participant-count",
                "1",
                "--backup-retention",
                "retain until explicit cleanup authorization",
                "--release-evidence-format",
                "canonical create-only json sha256 list",
                "--material-source-root",
                str(source_root),
                "--expected-participant-identity",
                "participant-user-01",
                "--expected-reviewer-identity",
                "independent-reviewer-01",
            ],
        )
        assert code == 0 and state["code"] == "STUDY_CREATED"


def test_v4_cli_full_participant_engineering_chain(tmp_path, capsys) -> None:
    """CLI v4 records both instance rounds without a reviewer identity or fields."""
    case = _v4_case()
    try:
        root = case["root"]
        study_root, work_root = root / "cli-study", root / "cli-work"
        study_root.mkdir()
        work_root.mkdir()
        predecessor = case["predecessor_paths"]
        study_args = [
            "study-create",
            "--study-root",
            str(study_root),
            "--work-root",
            str(work_root),
            "--study-id",
            "phase3e-v4-cli",
            "--manifest",
            str(_manifest().with_name("projecttown-phase3e-manifest-v4.json")),
            "--control-rating-threshold",
            "4",
            "--participant-arrangement",
            "same participant completes both rounds",
            "--participant-count",
            "1",
            "--backup-retention",
            "retain until explicit cleanup authorization",
            "--release-evidence-format",
            "canonical create-only json sha256 list",
            "--material-source-root",
            str(case["source_root"]),
            "--expected-participant-identity",
            "participant-user-01",
            "--protocol-v4",
            "--round1-material-root",
            str(case["study"].round1_contract.material_root),
            "--round1-exact-task",
            "Add grounded details",
            "--round1-target",
            str(case["study"].round1_contract.target_path),
        ]
        for role, flag in (
            ("v3_study", "v3-study"),
            ("result", "result"),
            ("apply_plan", "apply-plan"),
            ("executable_proposal", "executable-proposal"),
            ("apply_authorization", "apply-authorization"),
            ("restore_authorization", "restore-authorization"),
            ("apply_receipt", "apply-receipt"),
            ("restore_receipt", "restore-receipt"),
            ("apply_backup", "apply-backup"),
            ("restore_backup", "restore-backup"),
        ):
            study_args.extend(("--predecessor-" + flag, str(predecessor[role])))
        code, state = _run(capsys, study_args)
        assert code == 0 and state["record_schema_version"] == "v3-phase3e-study-v4"

        def human(tag: str, pdf: Path) -> list[str]:
            participant = work_root / f"participant-{tag}.txt"
            participant.write_text("participant evidence", encoding="utf-8")
            if tag.startswith("r2"):
                participant = pdf
            engineering = work_root / f"engineering-{tag}.txt"
            engineering.write_text("engineering evidence", encoding="utf-8")
            return [
                "--participant-disposition",
                "retained",
                "--participant-identity",
                "participant-user-01",
                "--participant-elapsed-seconds",
                "120",
                "--participant-action",
                "open_task",
                "--participant-notes",
                "participant notes",
                "--participant-timestamp",
                "2026-09-01T10:00:00+08:00",
                "--participant-evidence-path",
                str(participant),
                "--participant-control-rating",
                "4",
                "--participant-citation-usable",
                "true",
                "--participant-structural-rewrite",
                "false",
                "--engineering-outcome",
                "PASS",
                "--engineering-verifier-identity",
                "sol-engineering-01",
                "--engineering-check",
                "canonical binding",
                "--engineering-notes",
                "engineering notes",
                "--engineering-action",
                "check",
                "--engineering-timestamp",
                "2026-09-01T10:01:00+08:00",
                "--engineering-evidence-path",
                str(engineering),
                "--engineering-citation-traceable",
                "true",
                "--engineering-citation-usable",
                "true",
                "--engineering-blocking-defect",
                "false",
            ]

        r1_args = [
            "round-create",
            "--study-root",
            str(study_root),
            "--round-id",
            "R1-CONTROLLED-APPLY",
            "--binding-status",
            "verified",
            "--target-is-disposable-external-fixture",
            "true",
            "--result",
            str(predecessor["result"]),
            "--apply-plan",
            str(predecessor["apply_plan"]),
            "--executable-proposal",
            str(predecessor["executable_proposal"]),
            "--user-authorization",
            str(predecessor["apply_authorization"]),
            "--restore-authorization",
            str(predecessor["restore_authorization"]),
            "--apply-receipt",
            str(predecessor["apply_receipt"]),
            "--restore-receipt",
            str(predecessor["restore_receipt"]),
            "--backup",
            str(predecessor["apply_backup"]),
            "--restore-backup",
            str(predecessor["restore_backup"]),
        ]
        r1_pdf = work_root / "r1-not-pdf.txt"
        r1_pdf.write_text("r1", encoding="utf-8")
        code, state = _run(capsys, [*r1_args, "--engineering-only"])
        assert code == 2 and state["code"] == "V4_INSTANCE_EVIDENCE_REQUIRED"
        code, state = _run(
            capsys,
            [*r1_args, *human("r1-review", r1_pdf), "--reviewer-identity", "forbidden"],
        )
        assert code == 2 and state["code"] == "V4_REVIEWER_FIELDS_FORBIDDEN"
        code, state = _run(capsys, [*r1_args, *human("r1", r1_pdf)])
        assert code == 0 and state["record_schema_version"] == "v3-phase3e-round-v4"

        prior_r2_result = next(
            item.path for item in case["r2"].evidence if item.kind == "result"
        )
        result = work_root / "r2-result.json"
        result.write_bytes(Path(prior_r2_result).read_bytes())
        good_result = parse_session_bytes(result.read_bytes())
        wrong_draft = create_draft(
            case["source_root"],
            good_result.draft.selections,
            task="wrong v4 fixed task",
            artifact_kind="report",
        )
        wrong_result = generate_result(
            case["source_root"], wrong_draft, wrong_draft.contract_hash
        )
        wrong_result_path = work_root / "wrong-r2-result.json"
        wrong_result_path.write_bytes(serialize_session(wrong_result))
        preview = work_root / "preview.json"
        preview.write_text("{}", encoding="utf-8")
        citation = work_root / "citation.json"
        citation.write_text("{}", encoding="utf-8")
        pdf = work_root / "candidate.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        r2_args = [
            "round-create",
            "--study-root",
            str(study_root),
            "--round-id",
            "R2-REPORT-EXPORT",
            "--binding-status",
            "verified",
            "--target-is-disposable-external-fixture",
            "false",
            "--material-manifest",
            str(_manifest().with_name("projecttown-phase3e-manifest-v4.json")),
            "--result",
            str(result),
            "--preview",
            str(preview),
            "--citation",
            str(citation),
            "--pdf-export",
            str(pdf),
        ]
        rejected_args = [
            *r2_args[: r2_args.index("--result") + 1],
            str(wrong_result_path),
            *r2_args[r2_args.index("--result") + 2 :],
        ]
        code, state = _run(capsys, [*rejected_args, *human("r2-rejected", pdf)])
        assert code == 2 and state["code"] == "UNPROVEN_VERIFIED_BINDING"
        assert not (study_root / "R2-REPORT-EXPORT.json").exists()
        code, state = _run(capsys, [*r2_args, *human("r2", pdf)])
        assert code == 0 and state["participant_present"] is True
        r2_path = study_root / "R2-REPORT-EXPORT.json"
        original_r2_bytes = r2_path.read_bytes()
        r2_record = load_record(r2_path)
        assert isinstance(r2_record, Phase3ERoundV4)
        wrong_evidence = tuple(
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
            for item in r2_record.evidence
        )
        pre_fix_values = r2_record.model_dump()
        pre_fix_values["evidence"] = wrong_evidence
        pre_fix_values.pop("round_hash")
        pre_fix_round = _make(
            Phase3ERoundV4,
            ROUND_HASH_DOMAIN_V4,
            "round_hash",
            pre_fix_values,
        )
        r2_path.write_bytes(serialize_record(pre_fix_round))
        code, state = _run(
            capsys,
            [
                "check",
                "--study-root",
                str(study_root),
                "--record",
                "round",
                "--round-id",
                "R2-REPORT-EXPORT",
            ],
        )
        assert code == 2 and state["code"] == "ROUND_CROSS_BINDING_MISMATCH"
        r2_path.write_bytes(original_r2_bytes)
        code, state = _run(capsys, ["summary-create", "--study-root", str(study_root)])
        assert (
            code == 0
            and state["gate_state"] == "criteria_met_awaiting_user_rc_acceptance"
        )
        assert (
            _run(
                capsys,
                ["check", "--study-root", str(study_root), "--record", "summary"],
            )[0]
            == 0
        )
        assert (
            _run(capsys, ["status", "--study-root", str(study_root)])[1][
                "blocker_count"
            ]
            == 1
        )
    finally:
        shutil.rmtree(case["root"], ignore_errors=True)
