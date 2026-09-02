from __future__ import annotations

import hashlib
import sqlite3

from backend.app.runtime import stable_hash
from backend.app.utils import utc_now
from backend.app.v1.storage import MIGRATIONS, V1Storage


def test_migrations5_through_latest_are_additive_and_old_v1_rows_are_unchanged(
    tmp_path,
) -> None:
    path = tmp_path / "v1-through-4.db"
    expected = [stable_hash(list(statements)) for _, statements in MIGRATIONS[:4]]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE v1_schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for version, statements in MIGRATIONS[:4]:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO v1_schema_migrations VALUES (?, ?, ?)",
                (version, stable_hash(list(statements)), utc_now()),
            )
        connection.execute(
            "INSERT INTO v1_quests VALUES ('q1', '{}', 0, 'draft', 'then', 'then')"
        )
        connection.execute(
            "INSERT INTO v1_goal_contracts VALUES ('c1', 1, 'q1', '{}', 'then')"
        )
        connection.execute(
            "INSERT INTO v1_plan_versions VALUES ('p1', 1, 'q1', '{}', 'then')"
        )
        connection.execute(
            "INSERT INTO v1_plan_milestones VALUES ('p1', 1, 'm1', 'one', '{}')"
        )
        connection.execute(
            "INSERT INTO v1_events(quest_id, sequence, event_type, event_schema_version, state_version_before, state_version_after, payload_json, state_hash, created_at) VALUES ('q1', 1, 'QuestDrafted', 1, 0, 1, '{}', 'hash', 'then')"
        )
        connection.execute(
            "INSERT INTO v1_checkpoints VALUES ('q1', 1, '{}', 'hash', 'then')"
        )
        connection.execute(
            "INSERT INTO v1_artifact_reviews VALUES ('q1', 'r1', 'manifest', 'idem', 'keep', NULL, 'then', NULL)"
        )
        tables = (
            "v1_quests",
            "v1_goal_contracts",
            "v1_plan_versions",
            "v1_events",
            "v1_checkpoints",
            "v1_artifact_reviews",
        )
        old_hash = hashlib.sha256(
            repr(
                {
                    table: connection.execute(f"SELECT * FROM {table}").fetchall()
                    for table in tables
                }
            ).encode()
        ).hexdigest()
    storage = V1Storage(path)
    versions = storage.schema_versions()
    assert [row["version"] for row in versions] == [1, 2, 3, 4, 5, 6, 7]
    assert [row["checksum"] for row in versions[:4]] == expected
    assert versions[4]["checksum"] == stable_hash(list(MIGRATIONS[4][1]))
    assert versions[5]["checksum"] == stable_hash(list(MIGRATIONS[5][1]))
    assert versions[6]["checksum"] == stable_hash(list(MIGRATIONS[6][1]))
    with sqlite3.connect(path) as connection:
        assert (
            hashlib.sha256(
                repr(
                    {
                        table: connection.execute(f"SELECT * FROM {table}").fetchall()
                        for table in tables
                    }
                ).encode()
            ).hexdigest()
            == old_hash
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    storage.close()
    reopened = V1Storage(path)
    assert reopened.schema_versions() == versions
    reopened.close()
