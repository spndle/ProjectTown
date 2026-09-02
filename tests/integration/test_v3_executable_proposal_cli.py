from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from backend.app.controlled_apply import prepare_apply_plan
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    publish_new_file,
    serialize_session,
)

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_v3_executable_proposal.py"


def _run(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _ready(tmp_path: Path):
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
    plan = evidence / "plan.json"
    prepare_apply_plan(root, result_path, target, plan)
    return root, evidence, target, result_path, plan


def _status(value, code):
    assert value.returncode == code and value.stderr == ""
    data = json.loads(value.stdout)
    assert data["write_performed"] is False
    assert data["offline_calls"] == {
        "provider": 0,
        "image": 0,
        "embedding": 0,
        "mcp": 0,
        "network": 0,
        "egress": 0,
        "paid": 0,
    }
    return data


def test_help_only_create_check():
    value = _run("--help")
    assert (
        value.returncode == 0 and "create" in value.stdout and "check" in value.stdout
    )


def test_create_check_tamper_and_no_path_leak(tmp_path: Path):
    root, evidence, target, result, plan = _ready(tmp_path)
    output = evidence / "proposal.json"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    args = (
        "--root",
        str(root),
        "--result",
        str(result),
        "--target",
        str(target),
        "--plan",
        str(plan),
    )
    created = _status(_run("create", *args, "--proposal-out", str(output)), 0)
    assert created["code"] == "PROPOSAL_CREATED" and str(root) not in json.dumps(
        created
    )
    assert (
        _status(_run("check", *args, "--proposal", str(output)), 0)["code"]
        == "PROPOSAL_VERIFIED"
    )
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    output.write_bytes(output.read_bytes() + b" ")
    assert _status(_run("check", *args, "--proposal", str(output)), 2)["code"] in {
        "INVALID_PROPOSAL",
        "PROPOSAL_BLOCKED",
    }


def test_cli_duplicate_stale_relative_and_content_leakage(tmp_path: Path):
    root, evidence, target, result, plan = _ready(tmp_path)
    output = evidence / "proposal.json"
    args = (
        "--root",
        str(root),
        "--result",
        str(result),
        "--target",
        str(target),
        "--plan",
        str(plan),
    )
    created = _run("create", *args, "--proposal-out", str(output))
    payload = _status(created, 0)
    rendered = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        str(root),
        str(evidence),
        "Improve README.",
        "post_image_base64",
        "Task-driven",
    ):
        assert forbidden not in rendered
    duplicate = _status(_run("create", *args, "--proposal-out", str(output)), 2)
    assert duplicate["code"] in {
        "INVALID_OUTPUT_PATH",
        "OUTPUT_EXISTS",
        "PUBLICATION_CONFLICT",
        "REJECTED",
    }
    assert str(root) not in json.dumps(duplicate)

    target.write_text("# stale\n", encoding="utf-8")
    stale = _status(_run("check", *args, "--proposal", str(output)), 2)
    assert stale["code"] in {"PROPOSAL_BLOCKED", "TARGET_BINDING_CHANGED", "REJECTED"}
    assert str(root) not in json.dumps(stale)

    relative = _status(
        _run(
            "create",
            "--root",
            "relative-root",
            "--result",
            str(result),
            "--target",
            str(target),
            "--plan",
            str(plan),
            "--proposal-out",
            str(evidence / "relative.json"),
        ),
        2,
    )
    assert relative["code"] == "INVALID_ROOT"
    assert "relative-root" not in json.dumps(relative)
