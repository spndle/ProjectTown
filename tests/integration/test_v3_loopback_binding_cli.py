from __future__ import annotations

import json

from scripts.run_v3_loopback_binding import main
from tests.v3_loopback_support import OPERATION_ID, loopback_ready


def test_binding_cli_load_and_check_redact_paths_and_nonce(tmp_path, capsys):
    value = loopback_ready(tmp_path)
    work = value["work"]
    assert (
        main(["load", "--work-root", str(work), "--web-operation-id", OPERATION_ID])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "binding_id": OPERATION_ID,
        "status": "LOADED",
    }
    assert (
        main(["check", "--work-root", str(work), "--web-operation-id", OPERATION_ID])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "binding_id": OPERATION_ID,
        "status": "CHECKED",
    }


def test_binding_cli_create_only_rejects_duplicate(tmp_path, capsys):
    value = loopback_ready(tmp_path)
    assert (
        main(
            [
                "create",
                "--work-root",
                str(value["work"]),
                "--authorization",
                str(value["authorization_path"]),
                "--web-operation-id",
                OPERATION_ID,
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"


def test_binding_cli_creates_and_checks_a_new_redacted_binding(tmp_path, capsys):
    value = loopback_ready(tmp_path)
    new_id = "d" * 64
    arguments = [
        "--work-root",
        str(value["work"]),
        "--web-operation-id",
        new_id,
    ]
    assert (
        main(
            [
                "create",
                *arguments,
                "--authorization",
                str(value["authorization_path"]),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert json.loads(output) == {"binding_id": new_id, "status": "CREATED"}
    assert str(value["authorization_path"]) not in output
    assert value["authorization"].nonce not in output
    assert main(["check", *arguments]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "binding_id": new_id,
        "status": "CHECKED",
    }
