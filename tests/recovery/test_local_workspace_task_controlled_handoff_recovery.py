from __future__ import annotations

import pytest

from backend.app import local_workspace_task_controlled_handoff as handoff
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
)
from tests.unit.test_local_workspace_task_controlled_handoff import ready


def _args(tmp_path):
    material, work, evidence, _target, plan, proposal = ready(tmp_path)
    return (
        material,
        work,
        evidence,
        plan,
        proposal,
        evidence / "controlled-handoffs" / "readme.json",
    )


def test_precommit_rollback_is_rejected_and_no_record(tmp_path, monkeypatch) -> None:
    material, work, evidence, plan, proposal, output = _args(tmp_path)

    def fail(*_args, **_kwargs):
        raise PublicationRollbackError()

    monkeypatch.setattr(handoff, "publish_new_file", fail)
    with pytest.raises(handoff.ControlledHandoffError, match="PUBLICATION_ROLLED_BACK"):
        handoff.create_controlled_handoff(
            work,
            material,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=plan,
            proposal_path=proposal,
            output=output,
        )
    assert not output.exists()


def test_committed_attention_is_distinct_and_read_only_check_recovers(
    tmp_path, monkeypatch
) -> None:
    material, work, evidence, plan, proposal, output = _args(tmp_path)
    original = handoff.publish_new_file

    def committed(*args, **kwargs):
        original(*args, **kwargs)
        raise PublicationAttentionError()

    monkeypatch.setattr(handoff, "publish_new_file", committed)
    with pytest.raises(
        handoff.ControlledHandoffError, match="COMMITTED_NEEDS_ATTENTION"
    ):
        handoff.create_controlled_handoff(
            work,
            material,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=plan,
            proposal_path=proposal,
            output=output,
        )
    assert output.exists()
    assert handoff.verify_controlled_handoff(work, material, evidence, output)


def test_post_publication_drift_fails_check_and_preserves_final(tmp_path) -> None:
    material, work, evidence, plan, proposal, output = _args(tmp_path)
    handoff.create_controlled_handoff(
        work,
        material,
        evidence,
        task_id="readme",
        binding_path=work / "authoring-bindings" / "readme.json",
        plan_path=plan,
        proposal_path=proposal,
        output=output,
    )
    plan.write_bytes(plan.read_bytes() + b" ")
    assert output.exists() and not handoff.verify_controlled_handoff(
        work, material, evidence, output
    )
