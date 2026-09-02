from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.app.controlled_apply import ControlledApplyError, prepare_apply_plan
from backend.app.executable_proposal import create_executable_proposal
from backend.app.local_workspace_task_authoring import (
    build_catalog,
    confirm_and_generate,
    create_authoring_draft,
    initialize_work_root,
    publish_catalog,
)
from backend.app.local_workspace_task_controlled_handoff import (
    HANDOFF_HASH_DOMAIN,
    ControlledHandoffError,
    create_controlled_handoff,
    load_controlled_handoff,
    parse_controlled_handoff_bytes,
    serialize_controlled_handoff,
    verify_controlled_handoff,
)


def ready(tmp_path: Path):
    material, work, evidence = (
        tmp_path / "material",
        tmp_path / "work",
        tmp_path / "evidence",
    )
    material.mkdir()
    work.mkdir()
    evidence.mkdir()
    target = material / "README.md"
    target.write_text("# Existing\n", encoding="utf-8")
    initialize_work_root(work, material)
    catalog = build_catalog(material)
    publish_catalog(work, catalog)
    source = catalog.entries[0].source_id
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="readme",
        task="Update README.",
        artifact_kind="readme",
        source_ids=[source],
        idempotency_key="draft",
        readme_target_id=source,
    )
    confirm_and_generate(
        work,
        material,
        task_id="readme",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="generate",
    )
    result, plan, proposal = (
        work / "results" / "readme-result.json",
        evidence / "plan.json",
        evidence / "proposal.json",
    )
    prepare_apply_plan(material, result, target, plan)
    create_executable_proposal(material, result, target, plan, proposal)
    return material, work, evidence, target, plan, proposal


def create(tmp_path: Path):
    material, work, evidence, target, plan, proposal = ready(tmp_path)
    output = evidence / "controlled-handoffs" / "readme.json"
    value = create_controlled_handoff(
        work,
        material,
        evidence,
        task_id="readme",
        binding_path=work / "authoring-bindings" / "readme.json",
        plan_path=plan,
        proposal_path=proposal,
        output=output,
    )
    return material, work, evidence, target, plan, proposal, output, value


def rehashed(value, **changes: object) -> bytes:
    raw = value.model_dump(mode="json")
    raw.update(changes)
    raw.pop("handoff_hash")
    raw["handoff_hash"] = hashlib.sha256(
        HANDOFF_HASH_DOMAIN.encode("ascii")
        + b"\0"
        + json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_create_check_canonical_hash_and_no_target_write(tmp_path: Path) -> None:
    material, work, evidence, target, _plan, _proposal, output, value = create(tmp_path)
    assert value.write_performed is False and value.authorization_included is False
    assert verify_controlled_handoff(work, material, evidence, output)
    assert load_controlled_handoff(output) == value
    assert serialize_controlled_handoff(value) == output.read_bytes()
    assert target.read_bytes().replace(b"\r\n", b"\n") == b"# Existing\n"


def test_duplicate_extra_and_rehashed_tamper_fail_closed(tmp_path: Path) -> None:
    *_rest, _output, value = create(tmp_path)
    data = serialize_controlled_handoff(value)
    with pytest.raises(ControlledHandoffError):
        parse_controlled_handoff_bytes(data[:-1] + b',"task_id":"other"}')
    raw = value.model_dump(mode="json")
    raw["handoff_semantics"] = "tampered"
    raw.pop("handoff_hash")
    raw["handoff_hash"] = hashlib.sha256(
        HANDOFF_HASH_DOMAIN.encode()
        + b"\0"
        + json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ControlledHandoffError):
        parse_controlled_handoff_bytes(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("work_root", "relative"),
        ("material_root", "relative"),
        ("evidence_root", "relative"),
        ("authoring_binding_path", "relative.json"),
        ("result_path", "relative.json"),
        ("target_path", "README.md"),
        ("apply_plan_path", "plan.json"),
        ("proposal_path", "proposal.json"),
        ("target_relative_path", "../README.md"),
        ("target_relative_path", "folder\\README.md"),
        ("target_relative_path", "/README.md"),
    ],
)
def test_record_parse_rejects_relative_or_unsafe_path_syntax(
    tmp_path: Path, field: str, bad: str
) -> None:
    *_rest, value = create(tmp_path)
    with pytest.raises(ControlledHandoffError):
        parse_controlled_handoff_bytes(rehashed(value, **{field: bad}))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("schema_version", "unknown"),
        ("hash_domain", "unknown"),
        ("handoff_semantics", "wrong"),
        ("state", "wrong"),
    ],
)
def test_record_parse_rejects_unknown_identity_or_rehashed_contract(
    tmp_path: Path, field: str, bad: str
) -> None:
    *_rest, value = create(tmp_path)
    with pytest.raises(ControlledHandoffError):
        parse_controlled_handoff_bytes(rehashed(value, **{field: bad}))


def test_record_parse_rejects_noncanonical_bytes(tmp_path: Path) -> None:
    *_rest, value = create(tmp_path)
    assert parse_controlled_handoff_bytes(serialize_controlled_handoff(value)) == value
    with pytest.raises(ControlledHandoffError):
        parse_controlled_handoff_bytes(
            json.dumps(value.model_dump(mode="json"), indent=2).encode("ascii")
        )


def test_input_and_target_drift_preserves_published_record(tmp_path: Path) -> None:
    material, work, evidence, target, _plan, _proposal, output, _ = create(tmp_path)
    target.write_text("# changed\n", encoding="utf-8")
    assert not verify_controlled_handoff(work, material, evidence, output)
    assert output.exists()


def test_source_catalog_drift_fails_closed(tmp_path: Path) -> None:
    material, work, evidence, target, _plan, _proposal, output, _ = create(tmp_path)
    target.write_text("# Source changed\n", encoding="utf-8")
    assert not verify_controlled_handoff(work, material, evidence, output)
    assert output.exists()


@pytest.mark.parametrize(
    "category",
    ["authoring-bindings", "results", "requests", "intents", "receipts"],
)
def test_authoring_chain_tamper_fails_closed_and_preserves_handoff(
    tmp_path: Path, category: str
) -> None:
    material, work, evidence, _target, _plan, _proposal, output, _ = create(tmp_path)
    path = next((work / category).glob("*.json"))
    path.write_bytes(path.read_bytes() + b" ")
    assert not verify_controlled_handoff(work, material, evidence, output)
    assert output.exists()


@pytest.mark.parametrize("relative", ["plan.json", "proposal.json"])
def test_upstream_record_tamper_fails_closed(tmp_path: Path, relative: str) -> None:
    material, work, evidence, _target, _plan, _proposal, output, _ = create(tmp_path)
    path = evidence / relative
    path.write_bytes(path.read_bytes() + b" ")
    assert not verify_controlled_handoff(work, material, evidence, output)
    assert output.exists()


def test_renamed_copy_is_not_a_valid_handoff_path(tmp_path: Path) -> None:
    material, work, evidence, _target, _plan, _proposal, output, _ = create(tmp_path)
    copied = evidence / "controlled-handoffs" / "copied.json"
    copied.write_bytes(output.read_bytes())
    assert not verify_controlled_handoff(work, material, evidence, copied)


def test_invalid_upstream_does_not_create_handoff_directory(tmp_path: Path) -> None:
    material, work, evidence, _target, plan, proposal = ready(tmp_path)
    output = evidence / "controlled-handoffs" / "readme.json"
    with pytest.raises(ControlledHandoffError):
        create_controlled_handoff(
            work,
            material,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=proposal,
            proposal_path=plan,
            output=output,
        )
    assert not output.parent.exists()


def test_nested_or_renamed_output_is_rejected(tmp_path: Path) -> None:
    material, work, evidence, _target, plan, proposal = ready(tmp_path)
    with pytest.raises(ControlledHandoffError, match="INVALID_OUTPUT_PATH"):
        create_controlled_handoff(
            work,
            material,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=plan,
            proposal_path=proposal,
            output=evidence / "controlled-handoffs" / "nested" / "readme.json",
        )


def test_plan_result_is_stopped_at_existing_3a_boundary(tmp_path: Path) -> None:
    material, work, evidence = (
        tmp_path / "material",
        tmp_path / "work",
        tmp_path / "evidence",
    )
    material.mkdir()
    work.mkdir()
    evidence.mkdir()
    (material / "source.md").write_text("# Source\n", encoding="utf-8")
    initialize_work_root(work, material)
    catalog = build_catalog(material)
    publish_catalog(work, catalog)
    source = catalog.entries[0].source_id
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="plan",
        task="Plan.",
        artifact_kind="plan",
        source_ids=[source],
        idempotency_key="plan-draft",
    )
    confirm_and_generate(
        work,
        material,
        task_id="plan",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="plan-generate",
    )
    with pytest.raises(ControlledApplyError):
        prepare_apply_plan(
            material,
            work / "results" / "plan-result.json",
            material / "source.md",
            evidence / "plan.json",
        )


def test_hardlink_target_is_rejected_when_supported(tmp_path: Path) -> None:
    material, work, evidence, target, _plan, _proposal, output, _ = create(tmp_path)
    alternate = material / "alternate.md"
    alternate.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(alternate, target)
    except OSError as error:
        pytest.skip(f"hardlink unsupported on this filesystem: {error}")
    assert not verify_controlled_handoff(work, material, evidence, output)


def test_symlink_target_is_rejected_when_supported(tmp_path: Path) -> None:
    material, work, evidence, target, _plan, _proposal, output, _ = create(tmp_path)
    alternate = material / "alternate.md"
    alternate.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(alternate)
    except OSError as error:
        pytest.skip(f"symlink privilege unavailable: {error}")
    assert not verify_controlled_handoff(work, material, evidence, output)


def test_wrong_kind_mixed_paths_roots_and_duplicate_output_rejected(
    tmp_path: Path,
) -> None:
    material, work, evidence, _target, plan, proposal, output, _ = create(tmp_path)
    with pytest.raises(ControlledHandoffError):
        create_controlled_handoff(
            work,
            material,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=proposal,
            proposal_path=plan,
            output=evidence / "controlled-handoffs" / "other.json",
        )
    with pytest.raises(ControlledHandoffError, match="PUBLICATION_CONFLICT"):
        create_controlled_handoff(
            work,
            material,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=plan,
            proposal_path=proposal,
            output=output,
        )
    with pytest.raises(ControlledHandoffError):
        create_controlled_handoff(
            work,
            work,
            evidence,
            task_id="readme",
            binding_path=work / "authoring-bindings" / "readme.json",
            plan_path=plan,
            proposal_path=proposal,
            output=evidence / "controlled-handoffs" / "again.json",
        )
