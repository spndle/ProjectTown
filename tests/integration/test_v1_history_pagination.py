from __future__ import annotations

from backend.app.v1 import storage as storage_module
from backend.app.v1.storage import V1Storage


def _draft(storage: V1Storage, quest_id: str, goal: str, status: str = "draft") -> None:
    state = storage.create_draft(
        quest_id,
        {"id": f"contract-{quest_id}", "version": 1, "goal": goal},
        {"id": f"plan-{quest_id}", "version": 1, "milestones": [{"id": "one"}]},
    )
    if status != "draft":
        storage.append_event(
            quest_id, "HistoryStatus", {"status": status}, state["state_version"]
        )


def test_history_sql_pagination_casefold_and_literal_wildcards(
    tmp_path, monkeypatch
) -> None:
    storage = V1Storage(tmp_path / "history.db")
    _draft(storage, "quest-straße", "Build STRASSE guide")
    _draft(storage, "quest-percent%_", "literal %_ marker")
    _draft(storage, "quest-third", "other", status="planned")
    loads = 0
    original_loads = storage_module.json.loads

    def counting_loads(value, *args, **kwargs):
        nonlocal loads
        loads += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(storage_module.json, "loads", counting_loads)
    page, total = storage.search_quests(
        q="STRASSE", statuses=["draft"], offset=0, limit=1
    )
    assert total == 1
    assert [item["id"] for item in page] == ["quest-straße"]
    assert loads == 1
    percent, percent_total = storage.search_quests(
        q="%_", statuses=[], offset=0, limit=10
    )
    assert percent_total == 1
    assert [item["id"] for item in percent] == ["quest-percent%_"]
    paged, paged_total = storage.search_quests(
        q=None, statuses=["draft"], offset=1, limit=1
    )
    assert paged_total == 2
    assert len(paged) == 1
    storage.close()


def test_history_pagination_is_stable_when_created_at_ties(tmp_path) -> None:
    storage = V1Storage(tmp_path / "ties.db")
    for quest_id in ("tie-a", "tie-c", "tie-b"):
        _draft(storage, quest_id, "same timestamp")
    storage._conn.execute(
        "UPDATE v1_quests SET created_at='2026-08-20T00:00:00+00:00' WHERE quest_id LIKE 'tie-%'"
    )
    first, total = storage.search_quests(q=None, statuses=[], offset=0, limit=2)
    second, repeated_total = storage.search_quests(
        q=None, statuses=[], offset=2, limit=2
    )
    repeated, _ = storage.search_quests(q=None, statuses=[], offset=0, limit=2)
    unfiltered = storage.list_quests()
    assert total == repeated_total == 3
    assert [item["id"] for item in first] == ["tie-c", "tie-b"]
    assert [item["id"] for item in second] == ["tie-a"]
    assert first == repeated
    assert unfiltered == [*first, *second]
    assert {item["id"] for item in [*first, *second]} == {"tie-a", "tie-b", "tie-c"}
    storage.close()
