from __future__ import annotations

from pathlib import Path

from backend.app.local_workspace_task_authoring import (
    build_catalog,
    confirm_and_generate,
    create_authoring_draft,
    initialize_work_root,
    publish_catalog,
    recover_authoring_state,
)


def test_unreceipted_output_is_attention_after_restart(tmp_path: Path) -> None:
    material, work = tmp_path / "m", tmp_path / "w"
    material.mkdir()
    work.mkdir()
    (material / "source.md").write_text("# Source\ntext\n", encoding="utf-8")
    initialize_work_root(work, material)
    catalog = build_catalog(material)
    publish_catalog(work, catalog)
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="recovery",
        task="Create report",
        artifact_kind="report",
        source_ids=[catalog.entries[0].source_id],
        idempotency_key="d",
    )
    confirm_and_generate(
        work,
        material,
        task_id="recovery",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="g",
    )
    receipt = next((work / "receipts").glob("*.json"))
    receipt.unlink()
    assert recover_authoring_state(work, task_id="recovery").state == "attention"


def test_unreceipted_result_recovers_only_with_current_material(tmp_path: Path) -> None:
    material, work = tmp_path / "m2", tmp_path / "w2"
    material.mkdir()
    work.mkdir()
    (material / "source.md").write_text("# Source\ntext\n", encoding="utf-8")
    initialize_work_root(work, material)
    catalog = build_catalog(material)
    publish_catalog(work, catalog)
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="resume",
        task="Create report",
        artifact_kind="report",
        source_ids=[catalog.entries[0].source_id],
        idempotency_key="d",
    )
    confirm_and_generate(
        work,
        material,
        task_id="resume",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="g",
    )
    for receipt in (work / "receipts").glob("*.json"):
        if b'"operation":"generate"' in receipt.read_bytes():
            receipt.unlink()
            break
    assert recover_authoring_state(work, task_id="resume").state == "attention"
    assert (
        recover_authoring_state(work, task_id="resume", material_root=material).state
        == "generated"
    )


def test_receipted_result_repairs_missing_binding(tmp_path: Path) -> None:
    material, work = tmp_path / "m3", tmp_path / "w3"
    material.mkdir()
    work.mkdir()
    (material / "source.md").write_text("# Source\ntext\n", encoding="utf-8")
    initialize_work_root(work, material)
    catalog = build_catalog(material)
    publish_catalog(work, catalog)
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="binding-repair",
        task="Create report",
        artifact_kind="report",
        source_ids=[catalog.entries[0].source_id],
        idempotency_key="d",
    )
    confirm_and_generate(
        work,
        material,
        task_id="binding-repair",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="g",
    )
    (work / "authoring-bindings" / "binding-repair.json").unlink()
    assert recover_authoring_state(work, task_id="binding-repair").state == "generated"
    assert (work / "authoring-bindings" / "binding-repair.json").is_file()
