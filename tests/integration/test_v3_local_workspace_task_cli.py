from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.run_v3_local_workspace_task import main
from tests.unit.test_local_workspace_task import _ready
from tests.unit.test_local_workspace_task_authoring import _roots


def test_cli_create_check_and_create_only(tmp_path, capsys):
    value = _ready(tmp_path)
    args = ["--ui-work-root", str(value["work"]), "--task-id", "a" * 64]
    assert (
        main(
            [
                "create",
                *args,
                "--material-root",
                str(value["material"]),
                "--draft",
                str(value["binding"].draft_path),
                "--result",
                str(value["result_path"]),
                "--task-label",
                "Summary",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "status": "CREATED",
        "task_id": "a" * 64,
    }
    assert main(["check", *args]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "CHECKED"
    assert (
        main(
            [
                "create",
                *args,
                "--material-root",
                str(value["material"]),
                "--draft",
                str(value["binding"].draft_path),
                "--result",
                str(value["result_path"]),
                "--task-label",
                "Summary",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "CREATE_ONLY_CONFLICT"


def test_cli_script_entrypoint_loads_project_package() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "scripts/run_v3_local_workspace_task.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert (
        "Create or verify a read-only local workspace task binding" in completed.stdout
    )


def test_cli_authoring_init_check_and_safe_repeat(tmp_path, capsys) -> None:
    material, work = _roots(tmp_path)
    args = ["--ui-work-root", str(work), "--material-root", str(material)]
    assert main(["authoring-init", *args]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "AUTHORING_INITIALIZED"}
    assert (work / "bindings").is_dir()
    assert (work / "authoring-bindings").is_dir()
    assert main(["authoring-init", *args]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "AUTHORING_INITIALIZED"}
    assert main(["authoring-check", *args]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "AUTHORING_CHECKED"}
    assert (
        main(
            [
                "authoring-init",
                "--ui-work-root",
                str(work),
                "--material-root",
                str(work),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "ROOT_SEPARATION_REQUIRED"
