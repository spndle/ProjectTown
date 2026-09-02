from __future__ import annotations

import sqlite3

import pytest

from backend.app.runtime import stable_hash
from backend.app.v1 import storage as storage_module
from backend.app.v1.storage import MIGRATIONS, V1Storage

V1_TO_V6_CHECKSUMS = [
    "a5cf0cc34069eb682302ddfb7fc73dc512813b58d1ce1a805d5dcc66a98e0404",
    "64ba14a8f3d5b7d33083d5567b43f8096b80b7d15d72bfdc12a12d92d06e0d44",
    "5790cbf8ea42f8eca1d7b56e2ee5ea073c3a423ac1daf086d655baf54e4fbdf3",
    "a13b4bdb679de98545c0427aa66e4de7f9fb1e808eb2af11deb1b35ae3bb888c",
    "35e9cdedf7b779368ccfe2a7dca520adf27c07e689dfbb40860a7338e90c1e36",
    "a00417e01f234239c4f7715750c4b9f34fc1ea5385828b1fc895d49b4ebd936c",
]


def _checksum(version: int) -> str:
    statements = next(
        statements for number, statements in MIGRATIONS if number == version
    )
    return stable_hash(list(statements))


def _prefix_database(path, prefix: int) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE v1_schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    for version, statements in MIGRATIONS[:prefix]:
        for statement in statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO v1_schema_migrations VALUES (?, ?, 'before-provenance')",
            (version, stable_hash(list(statements))),
        )
    connection.commit()
    connection.close()


@pytest.mark.parametrize("prefix", [0, 4, 5, 6])
def test_migration7_upgrades_fresh_and_supported_prefixes_and_reopens(
    tmp_path, prefix: int
) -> None:
    path = tmp_path / f"prefix-{prefix}.db"
    if prefix:
        _prefix_database(path, prefix)
    storage = V1Storage(path)
    versions = storage.schema_versions()
    assert [row["version"] for row in versions] == list(range(1, 8))
    assert [row["checksum"] for row in versions[:6]] == V1_TO_V6_CHECKSUMS
    assert versions[6]["checksum"] == _checksum(7)
    assert storage._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert storage._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()
    reopened = V1Storage(path)
    assert [row["checksum"] for row in reopened.schema_versions()] == [
        *V1_TO_V6_CHECKSUMS,
        _checksum(7),
    ]
    reopened.close()


def test_migration7_rejects_unknown_future_version(tmp_path) -> None:
    path = tmp_path / "future.db"
    storage = V1Storage(path)
    storage.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO v1_schema_migrations VALUES (8, 'future', 'future')"
        )
    with pytest.raises(RuntimeError, match="unsupported future migration 8"):
        V1Storage(path)


def test_migration7_rejects_gaps_before_applying_anything(tmp_path) -> None:
    path = tmp_path / "gap.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE v1_schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO v1_schema_migrations VALUES (1, ?, 'old')", (_checksum(1),)
    )
    connection.execute(
        "INSERT INTO v1_schema_migrations VALUES (3, ?, 'old')", (_checksum(3),)
    )
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="strictly continuous prefix"):
        V1Storage(path)


def test_migration7_rejects_known_checksum_conflict(tmp_path) -> None:
    path = tmp_path / "bad-checksum.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE v1_schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO v1_schema_migrations VALUES (1, 'tampered', 'old')")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="migration 1 checksum"):
        V1Storage(path)


def test_migration7_provenance_ledgers_are_immutable(tmp_path) -> None:
    storage = V1Storage(tmp_path / "immutable.db")
    tables = (
        "v1_workspace_snapshots",
        "v1_workspace_snapshot_entries",
        "v1_tool_file_observations",
        "v1_artifact_provenance",
    )
    names = {
        row[0]
        for row in storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    for table in tables:
        assert f"{table}_immutable_update" in names
        assert f"{table}_immutable_delete" in names
    columns = {
        row[1]: row
        for row in storage._conn.execute(
            "PRAGMA table_info(v1_tool_file_observations)"
        ).fetchall()
    }
    assert "after_size_bytes" in columns
    assert columns["committed_event_id"][3] == 1
    storage.close()


def test_migration7_provenance_rejects_verified_status(tmp_path) -> None:
    storage = V1Storage(tmp_path / "verified-status.db")
    ddl = storage._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'v1_artifact_provenance'"
    ).fetchone()[0]
    assert "'verified'" not in ddl
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        storage._conn.execute(
            """
            INSERT INTO v1_artifact_provenance(
                provenance_id, artifact_id, quest_id, status, created_at
            ) VALUES ('provenance-1', 'artifact-1', 'quest-1', 'verified', 'now')
            """
        )
    storage.close()


def test_migration7_statement_failure_rolls_back_its_tables_and_ledger(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "broken-migration7.db"
    replacement = (
        7,
        (
            "CREATE TABLE v1_migration7_should_rollback (id INTEGER PRIMARY KEY)",
            "THIS IS INTENTIONALLY INVALID SQL",
        ),
    )
    monkeypatch.setattr(storage_module, "MIGRATIONS", (*MIGRATIONS[:6], replacement))
    with pytest.raises(sqlite3.OperationalError):
        storage_module.V1Storage(path)
    with sqlite3.connect(path) as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM v1_schema_migrations ORDER BY version"
            )
        ]
        assert versions == [1, 2, 3, 4, 5, 6]
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v1_migration7_should_rollback'"
            ).fetchone()
            is None
        )
