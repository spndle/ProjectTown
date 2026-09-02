from __future__ import annotations

from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1.verifier import Verifier


def test_list_directory_absent_path_is_empty_without_creating_workspace(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    sandbox = Sandbox(tmp_path / "sandbox")
    registry = build_default_registry(sandbox)
    result = registry.execute(
        "list_directory", "missing-workspace", {"path": "nested/absent"}
    )
    assert result == {"path": "nested/absent", "entries": [], "count": 0}
    assert not (tmp_path / "sandbox" / "missing-workspace").exists()


def test_list_directory_existing_path_keeps_deterministic_entries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sandbox = Sandbox(tmp_path / "sandbox")
    directory = sandbox.workspace_path("ws", create=True) / "items"
    directory.mkdir()
    (directory / "b.txt").write_text("b", encoding="utf-8")
    (directory / "A.txt").write_text("a", encoding="utf-8")
    result = build_default_registry(sandbox).execute(
        "list_directory", "ws", {"path": "items"}
    )
    assert result["path"] == "items"
    assert [entry["name"] for entry in result["entries"]] == ["A.txt", "b.txt"]
    assert result["count"] == 2


def test_verifier_read_only_listing_does_not_create_a_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sandbox = Sandbox(tmp_path / "sandbox")
    verifier = Verifier(sandbox, build_default_registry(sandbox))
    result = verifier.verify_read_only_tool(
        criterion_id="listing",
        tool_name="list_directory",
        workspace="missing",
        arguments={"path": "uncreated"},
        quest_id="quest",
        milestone_id="step",
        action_attempt="attempt",
        event_sequence=1,
    )
    assert result.passed
    assert not (tmp_path / "sandbox" / "missing").exists()
