from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tests.unit.test_local_workspace_task_controlled_handoff import ready

SCRIPT = (
    Path(__file__).parents[2]
    / "scripts"
    / "run_v3_local_workspace_task_controlled_handoff.py"
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def create_args_for(
    material: Path, work: Path, evidence: Path, plan: Path, proposal: Path
) -> tuple[str, ...]:
    return (
        "--work-root",
        str(work),
        "--material-root",
        str(material),
        "--evidence-root",
        str(evidence),
        "--task-id",
        "readme",
        "--binding",
        str(work / "authoring-bindings" / "readme.json"),
        "--plan",
        str(plan),
        "--proposal",
        str(proposal),
    )


def status(value: subprocess.CompletedProcess[str], code: int) -> dict[str, object]:
    assert value.returncode == code and value.stderr == ""
    result = json.loads(value.stdout)
    assert (
        result["write_performed"] is False and result["authorization_included"] is False
    )
    assert result["offline_calls"] == {
        "provider": 0,
        "image": 0,
        "embedding": 0,
        "mcp": 0,
        "network": 0,
        "egress": 0,
        "paid": 0,
    }
    return result


def test_cli_create_check_duplicate_drift_and_no_target_write(tmp_path: Path) -> None:
    material, work, evidence, target, plan, proposal = ready(tmp_path)
    output = evidence / "controlled-handoffs" / "readme.json"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    args = create_args_for(material, work, evidence, plan, proposal)
    created = status(run("create", *args, "--handoff-out", str(output)), 0)
    assert created["code"] == "HANDOFF_CREATED"
    assert str(material) not in json.dumps(created)
    assert (
        status(
            run(
                "check",
                "--work-root",
                str(work),
                "--material-root",
                str(material),
                "--evidence-root",
                str(evidence),
                "--handoff",
                str(output),
            ),
            0,
        )["code"]
        == "HANDOFF_VERIFIED"
    )
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert (
        status(run("create", *args, "--handoff-out", str(output)), 2)["outcome"]
        == "rejected"
    )
    target.write_text("# stale\n", encoding="utf-8")
    assert (
        status(
            run(
                "check",
                "--work-root",
                str(work),
                "--material-root",
                str(material),
                "--evidence-root",
                str(evidence),
                "--handoff",
                str(output),
            ),
            2,
        )["code"]
        == "HANDOFF_BLOCKED"
    )


def test_help_and_mixed_paths_are_path_free(tmp_path: Path) -> None:
    assert run("--help").returncode == 0
    check_help = run("check", "--help")
    assert check_help.returncode == 0
    for required in ("--work-root", "--material-root", "--evidence-root", "--handoff"):
        assert required in check_help.stdout
    for forbidden in ("--task-id", "--binding", "--plan", "--proposal"):
        assert forbidden not in check_help.stdout
    material, work, evidence, _, plan, proposal = ready(tmp_path)
    bad = status(
        run(
            "create",
            *create_args_for(material, work, evidence, proposal, plan),
            "--handoff-out",
            str(evidence / "controlled-handoffs" / "bad.json"),
        ),
        2,
    )
    rendered = json.dumps(bad)
    assert (
        str(material) not in rendered
        and str(work) not in rendered
        and str(evidence) not in rendered
    )
