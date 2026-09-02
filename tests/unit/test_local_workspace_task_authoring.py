from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.local_workspace_task_authoring import (
    AuthoringError,
    authoring_projection,
    build_catalog,
    confirm_and_generate,
    create_authoring_draft,
    export_result,
    initialize_work_root,
    load_catalog,
    parse_authoring_binding_bytes,
    parse_catalog_bytes,
    publish_catalog,
    publish_or_load_catalog,
    recover_authoring_state,
    serialize_catalog,
    verify_authoring_binding,
)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    material, work = tmp_path / "material", tmp_path / "work"
    material.mkdir()
    work.mkdir()
    (material / "nested").mkdir()
    (material / "notes.md").write_text("# Notes\nA useful source.\n", encoding="utf-8")
    (material / "nested" / "facts.txt").write_text("Useful facts.\n", encoding="utf-8")
    initialize_work_root(work, material)
    return material, work


def _catalog(material: Path, work: Path):
    catalog = build_catalog(material)
    publish_catalog(work, catalog)
    return catalog


def _ids(catalog):
    return [entry.source_id for entry in catalog.entries]


def test_catalog_is_canonical_nested_and_safe(tmp_path: Path) -> None:
    material, work = _roots(tmp_path)
    catalog = _catalog(material, work)
    assert len(catalog.entries) == 2
    assert parse_catalog_bytes(serialize_catalog(catalog)) == catalog
    assert tuple(entry.relative_path for entry in catalog.entries) == (
        "nested/facts.txt",
        "notes.md",
    )
    assert load_catalog(work, catalog.catalog_id) == catalog
    assert publish_or_load_catalog(work, catalog) == catalog
    assert build_catalog(material) == catalog
    (material / ".env.local").write_text("no", encoding="utf-8")
    with pytest.raises(AuthoringError, match="UNSAFE_SOURCE"):
        build_catalog(material)


@pytest.mark.parametrize("kind", ["plan", "report", "readme"])
def test_authoring_plan_report_and_readme(tmp_path: Path, kind: str) -> None:
    material, work = _roots(tmp_path)
    catalog = _catalog(material, work)
    ids = _ids(catalog)
    markdown_id = next(
        entry.source_id for entry in catalog.entries if entry.suffix == ".md"
    )
    kwargs = {"readme_target_id": markdown_id} if kind == "readme" else {}
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id=f"task-{kind}",
        task="Produce a grounded artifact",
        artifact_kind=kind,
        source_ids=ids,
        idempotency_key="draft-1",
        **kwargs,
    )
    result = confirm_and_generate(
        work,
        material,
        task_id=f"task-{kind}",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="generate-1",
    )
    assert result.draft.session_hash == draft.session_hash
    assert recover_authoring_state(work, task_id=f"task-{kind}").state == "generated"


def test_confirmation_stale_idempotency_and_exports(tmp_path: Path) -> None:
    material, work = _roots(tmp_path)
    catalog = _catalog(material, work)
    ids = _ids(catalog)
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="task-one",
        task="Produce report",
        artifact_kind="report",
        source_ids=ids,
        idempotency_key="same",
    )
    replay = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="task-one",
        task="Produce report",
        artifact_kind="report",
        source_ids=ids,
        idempotency_key="same",
    )
    assert replay == draft
    with pytest.raises(AuthoringError, match="IDEMPOTENCY_CONFLICT"):
        create_authoring_draft(
            work,
            material,
            catalog,
            task_id="task-one",
            task="Different",
            artifact_kind="report",
            source_ids=ids,
            idempotency_key="same",
        )
    with pytest.raises(AuthoringError, match="INVALID_CONFIRMATION"):
        confirm_and_generate(
            work,
            material,
            task_id="task-one",
            confirmation_phrase="CONFIRM nope",
            idempotency_key="g",
        )
    confirm_and_generate(
        work,
        material,
        task_id="task-one",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="g",
    )
    assert (
        export_result(
            work, material, task_id="task-one", format="markdown", idempotency_key="md"
        ).suffix
        == ".md"
    )
    assert (
        export_result(
            work, material, task_id="task-one", format="pdf", idempotency_key="pdf"
        ).suffix
        == ".pdf"
    )
    assert recover_authoring_state(work, task_id="task-one").state == "exported"
    markdown = work / "exports" / "task-one.md"
    markdown.write_bytes(markdown.read_bytes() + b"x")
    with pytest.raises(AuthoringError, match="ATTENTION"):
        export_result(
            work, material, task_id="task-one", format="markdown", idempotency_key="md"
        )
    assert recover_authoring_state(work, task_id="task-one").state == "attention"
    (material / "notes.md").write_text("changed", encoding="utf-8")
    with pytest.raises(AuthoringError, match="MATERIAL_STALE_OR_MISMATCH"):
        export_result(
            work, material, task_id="task-one", format="pdf", idempotency_key="pdf-2"
        )


def test_binding_uses_published_catalog_and_verifies_full_chain(tmp_path: Path) -> None:
    material, work = _roots(tmp_path)
    catalog = _catalog(material, work)
    draft = create_authoring_draft(
        work,
        material,
        catalog,
        task_id="bound",
        task="Produce report",
        artifact_kind="report",
        source_ids=_ids(catalog),
        idempotency_key="draft",
    )
    confirm_and_generate(
        work,
        material,
        task_id="bound",
        confirmation_phrase=f"CONFIRM {draft.contract_hash}",
        idempotency_key="generate",
    )
    binding = parse_authoring_binding_bytes(
        (work / "authoring-bindings" / "bound.json").read_bytes()
    )
    assert binding.catalog_hash == catalog.catalog_hash
    assert (
        verify_authoring_binding(binding, work, material).session_hash
        == binding.result_hash
    )
    receipt = next((work / "receipts").glob("*.json"))
    receipt.write_bytes(receipt.read_bytes() + b" ")
    assert recover_authoring_state(work, task_id="bound").state == "attention"


def test_unsafe_and_invalid_source_ids_are_rejected(tmp_path: Path) -> None:
    material, work = _roots(tmp_path)
    catalog = _catalog(material, work)
    with pytest.raises(AuthoringError, match="INVALID_SOURCE_ID"):
        create_authoring_draft(
            work,
            material,
            catalog,
            task_id="bad",
            task="task",
            artifact_kind="plan",
            source_ids=["0" * 64],
            idempotency_key="x",
        )
    (material / "bad.bin").write_bytes(b"x")
    with pytest.raises(AuthoringError, match="UNSAFE_SOURCE"):
        build_catalog(material)


def test_projection_has_no_paths(tmp_path: Path) -> None:
    material, work = _roots(tmp_path)
    _catalog(material, work)
    state = recover_authoring_state(work, task_id="not-created")
    assert all("path" not in key for key in authoring_projection(state))


def test_same_task_concurrent_replay_is_serialized(tmp_path: Path) -> None:
    material, work = _roots(tmp_path)
    catalog = _catalog(material, work)
    ids = _ids(catalog)

    def submit():
        return create_authoring_draft(
            work,
            material,
            catalog,
            task_id="concurrent",
            task="Produce report",
            artifact_kind="report",
            source_ids=ids,
            idempotency_key="one",
        ).session_hash

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert len(set(executor.map(lambda _unused: submit(), range(2)))) == 1
