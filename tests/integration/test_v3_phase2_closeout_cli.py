from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from backend.app import phase2_closeout as closeout
from scripts import run_v3_phase2_closeout as cli


def test_cli_check_hides_paths_and_reports_only_receipt_fields(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    sentinel = SimpleNamespace(
        receipt_hash="a" * 64,
        schema_version=closeout.SCHEMA_VERSION,
        legacy_limitation="does_not_authorize_apply",
    )
    monkeypatch.setattr(cli, "load_closeout", lambda _root: sentinel)
    monkeypatch.setattr(cli, "verify_closeout", lambda *_args: True)
    roots = [str(tmp_path.resolve())] * 5
    assert (
        cli.main(
            [
                "check",
                "--receipt-root",
                roots[0],
                "--t001-study-root",
                roots[1],
                "--t001-work-root",
                roots[2],
                "--t002-study-root",
                roots[3],
                "--t002-work-root",
                roots[4],
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["paths_disclosed"] is False
    assert output["offline_calls"]["egress"] == 0
    assert str(tmp_path) not in json.dumps(output)


def test_cli_rejects_relative_paths(capsys) -> None:
    assert (
        cli.main(
            [
                "check",
                "--receipt-root",
                "relative",
                "--t001-study-root",
                "relative",
                "--t001-work-root",
                "relative",
                "--t002-study-root",
                "relative",
                "--t002-work-root",
                "relative",
            ]
        )
        == 2
    )
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "INVALID_ARGUMENTS"
    assert "publication_state" not in output


def test_cli_preserves_attention_and_rollback_exit_semantics(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    roots = [str(tmp_path.resolve())] * 5
    sentinel = SimpleNamespace(
        receipt_hash="a" * 64,
        schema_version=closeout.SCHEMA_VERSION,
        legacy_limitation="does_not_authorize_apply",
    )
    monkeypatch.setattr(cli, "create_closeout", lambda *_args, **_kwargs: sentinel)
    monkeypatch.setattr(
        cli,
        "publish_closeout",
        lambda *_args: (_ for _ in ()).throw(
            closeout.Phase2CloseoutError("COMMITTED_NEEDS_ATTENTION")
        ),
    )
    args = [
        "create",
        "--receipt-root",
        roots[0],
        "--t001-study-root",
        roots[1],
        "--t001-work-root",
        roots[2],
        "--t002-study-root",
        roots[3],
        "--t002-work-root",
        roots[4],
        "--record-created-on",
        "2026-08-30",
    ]
    assert cli.main(args) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["outcome"] == "attention_required"
    assert output["publication_state"] == "committed_needs_attention"
    monkeypatch.setattr(
        cli,
        "publish_closeout",
        lambda *_args: (_ for _ in ()).throw(
            closeout.Phase2CloseoutError("PUBLICATION_ROLLED_BACK")
        ),
    )
    assert cli.main(args) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "PUBLICATION_ROLLED_BACK"
    assert output["publication_state"] == "rolled_back"


def test_cli_value_error_is_rejected_without_publication_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    roots = [str(tmp_path.resolve())] * 5
    monkeypatch.setattr(
        cli,
        "create_closeout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid date")),
    )
    assert (
        cli.main(
            [
                "create",
                "--receipt-root",
                roots[0],
                "--t001-study-root",
                roots[1],
                "--t001-work-root",
                roots[2],
                "--t002-study-root",
                roots[3],
                "--t002-work-root",
                roots[4],
                "--record-created-on",
                "2026-02-30",
            ]
        )
        == 2
    )
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "REJECTED"
    assert "publication_state" not in output
