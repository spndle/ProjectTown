from __future__ import annotations

from pathlib import Path

from backend.app.controlled_apply import prepare_apply_plan
from backend.app.controlled_write import create_authorization
from backend.app.executable_proposal import create_executable_proposal
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    publish_new_file,
    serialize_session,
)


def ready(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "materials"
    evidence = tmp_path / "evidence"
    root.mkdir()
    evidence.mkdir()
    target = root / "README.md"
    target.write_bytes(b"# Existing\n")
    draft = create_draft(
        root,
        ["README.md"],
        task="Add grounded details",
        artifact_kind="readme",
        readme_target="README.md",
    )
    result = generate_result(root, draft, draft.contract_hash)
    result_path = evidence / "result.json"
    publish_new_file(root, result_path, serialize_session(result))
    plan_path = evidence / "plan.json"
    prepare_apply_plan(root, result_path, target, plan_path)
    proposal_path = evidence / "proposal.json"
    proposal = create_executable_proposal(
        root, result_path, target, plan_path, proposal_path
    )
    ledger_root = evidence / "ledger"
    ledger_root.mkdir()
    authorization_path = evidence / "authorization.json"
    authorization = create_authorization(
        root,
        result_path,
        target,
        plan_path,
        proposal_path,
        ledger_root,
        authorization_path,
        "operation-001",
        "a" * 32,
    )
    return {
        "root": root,
        "evidence": evidence,
        "target": target,
        "result": result_path,
        "plan": plan_path,
        "proposal_path": proposal_path,
        "proposal": proposal,
        "ledger": ledger_root,
        "auth_path": authorization_path,
        "auth": authorization,
    }
