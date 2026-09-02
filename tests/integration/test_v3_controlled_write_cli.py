from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.controlled_write import ControlledWriteAttention, apply
from scripts.run_v3_controlled_write import main
from tests.controlled_write_support import ready


def _result(capsys) -> dict[str, object]:
    return json.loads(capsys.readouterr().out.strip())


def test_help_authorize_check_apply_check(tmp_path: Path, capsys) -> None:
    assert main(["--help"]) == 0
    capsys.readouterr()
    case = ready(tmp_path)
    authorization = case["evidence"] / "cli-auth.json"
    assert (
        main(
            [
                "authorize",
                "--root",
                str(case["root"]),
                "--result",
                str(case["result"]),
                "--target",
                str(case["target"]),
                "--plan",
                str(case["plan"]),
                "--proposal",
                str(case["proposal_path"]),
                "--ledger-root",
                str(case["ledger"]),
                "--authorization-out",
                str(authorization),
                "--operation-id",
                "operation-002",
                "--nonce",
                "b" * 32,
            ]
        )
        == 0
    )
    output = _result(capsys)
    assert output["code"] == "AUTHORIZED"
    assert str(case["target"]) not in json.dumps(output)
    assert (
        main(
            [
                "check",
                "--authorization",
                str(authorization),
                "--ledger-root",
                str(case["ledger"]),
            ]
        )
        == 0
    )
    assert _result(capsys)["code"] == "AUTHORIZED_NOT_DISPATCHED"
    assert (
        main(
            [
                "apply",
                "--root",
                str(case["root"]),
                "--authorization",
                str(authorization),
                "--result",
                str(case["result"]),
                "--proposal",
                str(case["proposal_path"]),
                "--target",
                str(case["target"]),
                "--plan",
                str(case["plan"]),
                "--ledger-root",
                str(case["ledger"]),
            ]
        )
        == 0
    )
    output = _result(capsys)
    assert output["code"] == "COMMITTED"
    assert output["offline_calls"] == {
        "provider": 0,
        "image": 0,
        "embedding": 0,
        "mcp": 0,
        "network": 0,
        "egress": 0,
        "paid": 0,
    }
    assert (
        main(
            [
                "check",
                "--authorization",
                str(authorization),
                "--ledger-root",
                str(case["ledger"]),
            ]
        )
        == 0
    )
    assert _result(capsys)["code"] == "COMMITTED"


def test_cli_rejection_and_attention_are_redacted(tmp_path: Path, capsys) -> None:
    case = ready(tmp_path)
    assert (
        main(
            [
                "apply",
                "--root",
                str(case["root"]),
                "--authorization",
                str(case["auth_path"]),
                "--result",
                str(case["result"]),
                "--proposal",
                str(case["proposal_path"]),
                "--target",
                str(case["target"]),
                "--plan",
                str(case["plan"]),
                "--ledger-root",
                str(case["evidence"] / "wrong"),
            ]
        )
        == 2
    )
    rejected = _result(capsys)
    assert rejected["outcome"] == "rejected"
    assert rejected["write_performed"] is False
    assert str(case["target"]) not in json.dumps(rejected)
    assert str(case["evidence"]) not in json.dumps(rejected)
    auth = case["auth"]
    with pytest.raises(ControlledWriteAttention):
        apply(
            case["root"],
            case["auth_path"],
            case["result"],
            case["proposal_path"],
            case["target"],
            case["plan"],
            case["ledger"],
            Path(auth.backup_path),
            Path(auth.receipt_path),
            fail_at="after_replace",
        )
    assert (
        main(
            [
                "apply",
                "--root",
                str(case["root"]),
                "--authorization",
                str(case["auth_path"]),
                "--result",
                str(case["result"]),
                "--proposal",
                str(case["proposal_path"]),
                "--target",
                str(case["target"]),
                "--plan",
                str(case["plan"]),
                "--ledger-root",
                str(case["ledger"]),
            ]
        )
        == 3
    )
    attention = _result(capsys)
    assert attention["outcome"] == "attention_required"
    assert attention["write_performed"] == "unknown"
    assert str(case["target"]) not in json.dumps(attention)


def test_cli_restore_flow(tmp_path: Path, capsys) -> None:
    case = ready(tmp_path)
    assert (
        main(
            [
                "apply",
                "--root",
                str(case["root"]),
                "--authorization",
                str(case["auth_path"]),
                "--result",
                str(case["result"]),
                "--proposal",
                str(case["proposal_path"]),
                "--target",
                str(case["target"]),
                "--plan",
                str(case["plan"]),
                "--ledger-root",
                str(case["ledger"]),
            ]
        )
        == 0
    )
    _result(capsys)
    restore_ledger = case["evidence"] / "restore-ledger"
    restore_ledger.mkdir()
    restore_authorization = case["evidence"] / "restore-auth.json"
    assert (
        main(
            [
                "restore-authorize",
                "--root",
                str(case["root"]),
                "--receipt",
                case["auth"].receipt_path,
                "--target",
                str(case["target"]),
                "--ledger-root",
                str(restore_ledger),
                "--authorization-out",
                str(restore_authorization),
                "--operation-id",
                "restore-001",
                "--nonce",
                "c" * 32,
            ]
        )
        == 0
    )
    assert _result(capsys)["code"] == "RESTORE_AUTHORIZED"
    assert (
        main(
            [
                "restore",
                "--root",
                str(case["root"]),
                "--authorization",
                str(restore_authorization),
                "--target",
                str(case["target"]),
                "--ledger-root",
                str(restore_ledger),
            ]
        )
        == 0
    )
    assert _result(capsys)["code"] == "COMMITTED"
