from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.local_workspace_task import (
    LocalWorkspaceTaskError,
    load_binding,
    make_binding,
    parse_binding_bytes,
    publish_binding,
    serialize_binding,
    sha256,
    verify_binding,
)
from backend.app.material_workflow import (
    create_draft,
    generate_result,
    publish_new_file,
    serialize_session,
)


def _ready(tmp_path: Path) -> dict[str, object]:
    material = tmp_path / "material"
    evidence = tmp_path / "evidence"
    work = tmp_path / "ui-work"
    material.mkdir()
    evidence.mkdir()
    (work / "bindings").mkdir(parents=True)
    (material / "notes.md").write_text("# Source\nEvidence", encoding="utf-8")
    draft = create_draft(
        material, ["notes.md"], task="Summarize", artifact_kind="report"
    )
    result = generate_result(material, draft, draft.contract_hash)
    draft_path, result_path = evidence / "draft.json", evidence / "result.json"
    publish_new_file(material, draft_path, serialize_session(draft))
    publish_new_file(material, result_path, serialize_session(result))
    metadata = work.lstat()
    binding = make_binding(
        task_id="a" * 64,
        ui_work_root=str(work),
        ui_work_root_device=int(metadata.st_dev),
        ui_work_root_inode=int(metadata.st_ino),
        material_root=str(material),
        draft_path=str(draft_path),
        draft_sha256=sha256(draft_path.read_bytes()),
        result_path=str(result_path),
        result_sha256=sha256(result_path.read_bytes()),
        artifact_kind="report",
        task_label="Summary",
    )
    return {
        "material": material,
        "work": work,
        "binding": binding,
        "result_path": result_path,
    }


def test_binding_is_canonical_create_only_and_verifies_fresh_result(tmp_path):
    value = _ready(tmp_path)
    binding = value["binding"]
    work = value["work"]
    assert publish_binding(work, binding) is None
    loaded = load_binding(work / "bindings" / f"{binding.task_id}.json")
    assert verify_binding(loaded, work).draft.task == "Summarize"
    with pytest.raises(LocalWorkspaceTaskError, match="CREATE_ONLY_CONFLICT"):
        publish_binding(work, binding)


def test_binding_hash_duplicate_unknown_and_tampered_result_fail_closed(tmp_path):
    value = _ready(tmp_path)
    binding, work, result_path = value["binding"], value["work"], value["result_path"]
    raw = serialize_binding(binding)
    assert raw.startswith(b'{"artifact_kind"')
    with pytest.raises(LocalWorkspaceTaskError):
        parse_binding_bytes(raw.replace(b'"task_id"', b'"task_id":"x","task_id"'))
    with pytest.raises(LocalWorkspaceTaskError):
        parse_binding_bytes(raw[:-1] + b',"extra":true}')
    result_path.write_bytes(result_path.read_bytes() + b" ")
    with pytest.raises(LocalWorkspaceTaskError, match="BINDING_TAMPERED"):
        verify_binding(binding, work)


def test_root_overlap_binding_fails_closed(tmp_path):
    value = _ready(tmp_path)
    binding, work = value["binding"], value["work"]
    overlapping = make_binding(
        **{
            **binding.model_dump(mode="json"),
            "material_root": str(work),
        }
    )
    with pytest.raises(LocalWorkspaceTaskError, match="ROOT_SEPARATION_REQUIRED"):
        verify_binding(overlapping, work)
