from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from backend.app import controlled_apply
from backend.app.controlled_apply import (
    ControlledApplyError,
    load_apply_plan,
    parse_apply_plan_bytes,
    prepare_apply_plan,
    serialize_apply_plan,
    verify_apply_plan,
)
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
    create_draft,
    generate_result,
    publish_new_file,
    serialize_session,
)


def _prepared(tmp_path: Path, *, extra_source: bool = False):
    root, outside = tmp_path / "materials", tmp_path / "evidence"
    root.mkdir()
    outside.mkdir()
    target = root / "README.md"
    target.write_text("# Existing README\n", encoding="utf-8")
    selected = ["README.md"]
    if extra_source:
        (root / "notes.md").write_text("stable source\n", encoding="utf-8")
        selected.append("notes.md")
    draft = create_draft(
        root,
        selected,
        task="Improve the local README using the selected material.",
        artifact_kind="readme",
        readme_target="README.md",
    )
    result = generate_result(root, draft, draft.contract_hash)
    result_path = outside / "result.json"
    publish_new_file(root, result_path, serialize_session(result))
    return root, outside, target, result_path


def test_prepare_and_check_are_deterministic_and_never_write_target(
    tmp_path: Path,
) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    first_dir, second_dir = outside / "first", outside / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_path, second_path = first_dir / "plan.json", second_dir / "plan.json"
    first = prepare_apply_plan(root, result_path, target, first_path)
    second = prepare_apply_plan(root, result_path, target, second_path)

    assert serialize_apply_plan(first) == serialize_apply_plan(second)
    assert verify_apply_plan(root, first, result_path, target)
    assert load_apply_plan(first_path) == first
    assert first.target_relative_path == "README.md"
    assert first.write_performed is False
    assert first.proposal_semantics == "human_readable_suggestion_not_executable_patch"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_load_apply_plan_rejects_material_root_storage_when_context_is_supplied(
    tmp_path: Path,
) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    target_before = hashlib.sha256(target.read_bytes()).hexdigest()
    external_path = outside / "plan.json"
    plan = prepare_apply_plan(root, result_path, target, external_path)
    internal_path = root / "plan.json"
    internal_path.write_bytes(external_path.read_bytes())

    # Existing single-argument callers retain canonical parsing compatibility.
    assert load_apply_plan(external_path) == plan
    with pytest.raises(ControlledApplyError) as rejected:
        load_apply_plan(internal_path, material_root=root)
    assert rejected.value.code == "INVALID_PLAN_PATH"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_before


def test_source_freshness_and_same_bytes_inode_replacement_fail_closed(
    tmp_path: Path,
) -> None:
    root, outside, target, result_path = _prepared(tmp_path, extra_source=True)
    plan = prepare_apply_plan(root, result_path, target, outside / "plan.json")
    (root / "notes.md").write_text("drifted source\n", encoding="utf-8")
    assert not verify_apply_plan(root, plan, result_path, target)

    # Restore the frozen source bytes so source freshness no longer masks identity.
    (root / "notes.md").write_text("stable source\n", encoding="utf-8")
    target.unlink()
    target.write_text("# Existing README\n", encoding="utf-8")
    assert not verify_apply_plan(root, plan, result_path, target)


def test_rejects_non_readme_scope_and_unsafe_target_shapes(tmp_path: Path) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    plan_output = outside / "plan.json"
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, root / "other.md", plan_output)
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, root, plan_output)
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, Path("README.md"), plan_output)
    outside_target = outside / "README.md"
    outside_target.write_text("outside", encoding="utf-8")
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, outside_target, plan_output)
    missing = root / "missing.md"
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, missing, plan_output)
    linked = root / "README-link.md"
    try:
        os.link(target, linked)
    except OSError:
        pytest.skip("hard links unavailable in this environment")
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, plan_output)


def test_rejects_plan_result_even_when_the_target_is_a_selected_markdown(
    tmp_path: Path,
) -> None:
    root, outside, target, _result_path = _prepared(tmp_path)
    draft = create_draft(
        root,
        ["README.md"],
        task="Produce a plan rather than an editable README proposal.",
        artifact_kind="plan",
    )
    result = generate_result(root, draft, draft.contract_hash)
    result_path = outside / "plan-result.json"
    publish_new_file(root, result_path, serialize_session(result))
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, outside / "plan.json")


@pytest.mark.parametrize("artifact_kind", ["plan", "report"])
def test_rejects_non_readme_result_kinds(tmp_path: Path, artifact_kind: str) -> None:
    root, outside, target, _result_path = _prepared(tmp_path)
    draft = create_draft(
        root,
        ["README.md"],
        task="Produce a different artifact type.",
        artifact_kind=artifact_kind,
    )
    result = generate_result(root, draft, draft.contract_hash)
    result_path = outside / f"{artifact_kind}-result.json"
    publish_new_file(root, result_path, serialize_session(result))
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, outside / f"{artifact_kind}.json")


def test_rejects_generated_unresolved_conflict_result(tmp_path: Path) -> None:
    root, outside = tmp_path / "materials", tmp_path / "evidence"
    root.mkdir()
    outside.mkdir()
    target = root / "README.md"
    target.write_text("constraint: mode=one\nconstraint: mode=two\n", encoding="utf-8")
    draft = create_draft(
        root,
        ["README.md"],
        task="Resolve local README constraints.",
        artifact_kind="readme",
        readme_target="README.md",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert result.state == "needs_user_decision" and result.conflicts
    result_path = outside / "conflicted-result.json"
    publish_new_file(root, result_path, serialize_session(result))
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, outside / "plan.json")


def test_rejects_reparse_target_when_supported(tmp_path: Path) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    replacement = root / "replacement.md"
    replacement.write_text("replacement", encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(replacement)
    except OSError:
        pytest.skip("symlink creation unavailable in this environment")
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, outside / "plan.json")


def test_check_fails_closed_for_target_source_result_and_plan_drift(
    tmp_path: Path,
) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    plan_path = outside / "plan.json"
    plan = prepare_apply_plan(root, result_path, target, plan_path)
    assert verify_apply_plan(root, plan, result_path, target)

    target.write_text("# Drifted README\n", encoding="utf-8")
    assert not verify_apply_plan(root, plan, result_path, target)
    target.write_text("# Existing README\n", encoding="utf-8")
    assert verify_apply_plan(root, plan, result_path, target)

    tampered = bytearray(plan_path.read_bytes())
    tampered[-2] = ord("0") if tampered[-2] != ord("0") else ord("1")
    tampered_path = outside / "tampered-plan.json"
    tampered_path.write_bytes(bytes(tampered))
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        load_apply_plan(tampered_path)

    result_path.write_bytes(result_path.read_bytes() + b" ")
    assert not verify_apply_plan(root, plan, result_path, target)


def test_parse_rejects_extra_fields_and_duplicate_output(tmp_path: Path) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    output = outside / "plan.json"
    plan = prepare_apply_plan(root, result_path, target, output)
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, output)
    payload = serialize_apply_plan(plan).replace(b"}", b',"extra":true}', 1)
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        parse_apply_plan_bytes(payload)


def test_prepare_fails_before_publication_when_result_changes_mid_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    target_before = hashlib.sha256(target.read_bytes()).hexdigest()
    original_hash = controlled_apply._result_bytes_hash
    original_bytes = result_path.read_bytes()
    calls = 0

    def replace_before_hash(path: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            # Simulate another actor replacing Result after parse but before hash.
            path.write_bytes(original_bytes + b" ")
        return original_hash(path)

    monkeypatch.setattr(controlled_apply, "_result_bytes_hash", replace_before_hash)
    plan_path = outside / "race-result-plan.json"
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, plan_path)
    assert not plan_path.exists()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_before


def test_prepare_fails_before_publication_when_target_observation_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    target_before = hashlib.sha256(target.read_bytes()).hexdigest()
    original_target = controlled_apply._stable_target
    calls = 0

    def drift_on_final_observation(
        observed_root: Path, observed_target: Path, expected: str
    ) -> tuple[str, int, int, int]:
        nonlocal calls
        calls += 1
        value = original_target(observed_root, observed_target, expected)
        if calls == 2:
            return ("0" * 64, value[1], value[2], value[3])
        return value

    monkeypatch.setattr(controlled_apply, "_stable_target", drift_on_final_observation)
    plan_path = outside / "race-target-plan.json"
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, result_path, target, plan_path)
    assert not plan_path.exists()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_before


def test_rejects_invalid_result_file_shapes(tmp_path: Path) -> None:
    root, outside, target, result_path = _prepared(tmp_path)
    directory = outside / "result-directory"
    directory.mkdir()
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, directory, target, outside / "plan.json")
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, Path("result.json"), target, outside / "plan.json")
    linked = outside / "linked-result.json"
    try:
        linked.symlink_to(result_path)
    except OSError:
        pytest.skip("symlink creation unavailable in this environment")
    with pytest.raises(ControlledApplyError, match="controlled apply rejected"):
        prepare_apply_plan(root, linked, target, outside / "plan.json")


@pytest.mark.parametrize(
    "failure", [PublicationAttentionError, PublicationRollbackError]
)
def test_prepare_preserves_safe_publication_recovery_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    root, outside, target, result_path = _prepared(tmp_path)

    def raise_publication(*_args: object, **_kwargs: object) -> None:
        raise failure()

    monkeypatch.setattr(controlled_apply, "publish_new_file", raise_publication)
    with pytest.raises(failure):
        prepare_apply_plan(root, result_path, target, outside / "plan.json")
