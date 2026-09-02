from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import scripts.run_v3_controlled_apply as cli
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    publish_new_file,
    serialize_session,
)

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_v3_controlled_apply.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _status(
    completed: subprocess.CompletedProcess[str], expected: int
) -> dict[str, object]:
    assert completed.returncode == expected
    assert completed.stderr == ""
    value = json.loads(completed.stdout)
    assert value["schema_version"] == "v3-controlled-apply-cli-status-v1"
    assert value["offline_calls"] == {
        "embedding": 0,
        "egress": 0,
        "mcp": 0,
        "network": 0,
        "paid": 0,
        "provider": 0,
    }
    assert value["write_performed"] is False
    return value


def _prepared(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root, evidence = tmp_path / "materials", tmp_path / "evidence"
    root.mkdir()
    evidence.mkdir()
    target = root / "README.md"
    target.write_text("# Existing README\n", encoding="utf-8")
    draft = create_draft(
        root,
        ["README.md"],
        task="Improve README.",
        artifact_kind="readme",
        readme_target="README.md",
    )
    result = generate_result(root, draft, draft.contract_hash)
    result_path = evidence / "result.json"
    publish_new_file(root, result_path, serialize_session(result))
    return root, evidence, target, result_path


def test_help_only_exposes_prepare_and_check() -> None:
    help_text = _run("--help")
    assert help_text.returncode == 0
    assert "prepare" in help_text.stdout and "check" in help_text.stdout
    assert set(cli._build_parser()._subparsers._group_actions[0].choices) == {
        "prepare",
        "check",
    }


def test_prepare_check_rejection_and_target_immutability(tmp_path: Path) -> None:
    root, evidence, target, result_path = _prepared(tmp_path)
    plan_path = evidence / "plan.json"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    prepared = _status(
        _run(
            "prepare",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan-out",
            str(plan_path),
        ),
        0,
    )
    assert prepared["code"] == "PLAN_PREPARED"
    assert str(root) not in json.dumps(prepared)
    checked = _status(
        _run(
            "check",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan",
            str(plan_path),
        ),
        0,
    )
    assert checked["code"] == "PREFLIGHT_VERIFIED"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    duplicate = _status(
        _run(
            "prepare",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan-out",
            str(plan_path),
        ),
        2,
    )
    assert duplicate["code"] == "INVALID_OUTPUT_PATH"
    target.write_text("# Changed\n", encoding="utf-8")
    drift = _status(
        _run(
            "check",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan",
            str(plan_path),
        ),
        2,
    )
    assert drift["code"] == "PREFLIGHT_BLOCKED"


def test_check_rejects_plan_stored_inside_material_root(tmp_path: Path) -> None:
    root, evidence, target, result_path = _prepared(tmp_path)
    external_plan = evidence / "plan.json"
    target_before = hashlib.sha256(target.read_bytes()).hexdigest()
    _status(
        _run(
            "prepare",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan-out",
            str(external_plan),
        ),
        0,
    )
    internal_plan = root / "plan.json"
    internal_plan.write_bytes(external_plan.read_bytes())

    rejected = _status(
        _run(
            "check",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan",
            str(internal_plan),
        ),
        2,
    )
    assert rejected["code"] == "INVALID_PLAN_PATH"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_before


def test_cli_rejects_relative_mismatched_and_plan_tamper(tmp_path: Path) -> None:
    root, evidence, target, result_path = _prepared(tmp_path)
    bad_target = _status(
        _run(
            "prepare",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            "README.md",
            "--plan-out",
            str(evidence / "x.json"),
        ),
        2,
    )
    assert bad_target["code"] == "INVALID_TARGET_PATH"
    plan_path = evidence / "plan.json"
    _status(
        _run(
            "prepare",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan-out",
            str(plan_path),
        ),
        0,
    )
    plan_path.write_bytes(
        plan_path.read_bytes().replace(
            b"awaiting_user_confirmation", b"tampered_user_confirmation"
        )
    )
    rejected = _status(
        _run(
            "check",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--target",
            str(target),
            "--plan",
            str(plan_path),
        ),
        2,
    )
    assert rejected["code"] in {"INVALID_PLAN", "PREFLIGHT_BLOCKED"}
