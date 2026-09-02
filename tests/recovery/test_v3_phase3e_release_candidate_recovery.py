from __future__ import annotations

import json
from pathlib import Path

from backend.app.phase3e_release_candidate import Phase3EError
from scripts import run_v3_phase3e_release_candidate as cli


def _study_args(root: Path, work: Path) -> list[str]:
    manifest = (
        Path(__file__).parents[2]
        / "examples"
        / "v3-phase-3"
        / "projecttown-phase3e-manifest-v1.json"
    )
    return [
        "study-create",
        "--study-root",
        str(root),
        "--work-root",
        str(work),
        "--study-id",
        "phase3e-recovery",
        "--manifest",
        str(manifest),
        "--control-rating-threshold",
        "4",
        "--participant-arrangement",
        "reviewers",
        "--participant-count",
        "2",
        "--backup-retention",
        "retain",
        "--release-evidence-format",
        "json",
    ]


def test_missing_record_is_rejected_then_focused_status_retries(
    tmp_path, capsys
) -> None:
    root = tmp_path / "missing-study"
    root.mkdir()
    first = cli.main(["status", "--study-root", str(root)])
    first_status = json.loads(capsys.readouterr().out)
    second = cli.main(["status", "--study-root", str(root)])
    second_status = json.loads(capsys.readouterr().out)
    assert first == second == 2
    assert first_status["code"] == second_status["code"] == "RECORD_UNAVAILABLE"
    assert first_status["publication_state"] == "not_applicable"


def test_publication_rollback_and_attention_are_stable(
    tmp_path, capsys, monkeypatch
) -> None:
    root, work = tmp_path / "study", tmp_path / "work"
    root.mkdir()
    work.mkdir()

    def rollback(*_args, **_kwargs):
        raise Phase3EError("PUBLICATION_ROLLED_BACK")

    monkeypatch.setattr(cli, "publish_record", rollback)
    assert cli.main(_study_args(root, work)) == 2
    rolled = json.loads(capsys.readouterr().out)
    assert rolled["publication_state"] == "rolled_back"

    def attention(*_args, **_kwargs):
        raise Phase3EError("COMMITTED_NEEDS_ATTENTION")

    monkeypatch.setattr(cli, "publish_record", attention)
    assert cli.main(_study_args(root, work)) == 3
    alerted = json.loads(capsys.readouterr().out)
    assert alerted["publication_state"] == "committed_needs_attention"


def test_create_only_publication_failure_recovers_without_false_success(
    tmp_path, capsys, monkeypatch
) -> None:
    root, work = tmp_path / "recover-study", tmp_path / "recover-work"
    root.mkdir()
    work.mkdir()
    original = cli.publish_record

    def rollback(*_args, **_kwargs):
        raise Phase3EError("PUBLICATION_ROLLED_BACK")

    monkeypatch.setattr(cli, "publish_record", rollback)
    assert cli.main(_study_args(root, work)) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["code"] == "PUBLICATION_ROLLED_BACK"
    assert not (root / "study.json").exists()
    monkeypatch.setattr(cli, "publish_record", original)
    assert cli.main(_study_args(root, work)) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["code"] == "STUDY_CREATED"
    assert recovered["record_schema_version"] == "v3-phase3e-study-v1"
