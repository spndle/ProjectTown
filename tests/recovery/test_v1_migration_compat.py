from __future__ import annotations

import sqlite3

from backend.app.database import Database
from backend.app.v1.storage import V1Storage


def _legacy_snapshot(path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
            for table in ("quests", "milestones", "traces")
        }


def test_v1_migration_preserves_and_explicitly_imports_legacy_data(tmp_path) -> None:
    path = tmp_path / "upgrade.db"
    legacy = Database(path)
    legacy.create_quest(
        quest_id="legacy-quest",
        goal="Preserve this v0.1 quest",
        template_id="project_brief",
        workspace="quests/legacy-quest",
        milestones=[
            {
                "id": "legacy-step",
                "title": "Legacy step",
                "description": "Existing v0.1 milestone",
                "tool_name": "write_text",
                "tool_args": {"path": "legacy.txt", "content": "legacy"},
            }
        ],
    )
    legacy.add_trace(
        "legacy-quest",
        trace_type="migration_fixture",
        message="Must survive v1 schema installation",
    )
    original = _legacy_snapshot(path)

    v1 = V1Storage(path)
    assert len(v1.schema_versions()) >= 1
    assert _legacy_snapshot(path) == original
    assert legacy.get_quest("legacy-quest")["status"] == "planned"

    assert v1.import_legacy(legacy.list_quests()) == 1
    imported = v1.get_quest("legacy-quest")
    assert imported is not None
    assert imported["legacy_unverified"] is True
    assert imported["status"] == "planned"
    assert v1.import_legacy(legacy.list_quests()) == 0
    assert _legacy_snapshot(path) == original

    v1.close()
    reopened = V1Storage(path)
    assert reopened.get_quest("legacy-quest")["legacy_unverified"] is True
    assert _legacy_snapshot(path) == original
    reopened.close()
    legacy.close()
