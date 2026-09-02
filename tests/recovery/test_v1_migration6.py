from __future__ import annotations

from backend.app.runtime import stable_hash
from backend.app.v1.storage import MIGRATIONS, V1Storage

V1_TO_V5_CHECKSUMS = [
    "a5cf0cc34069eb682302ddfb7fc73dc512813b58d1ce1a805d5dcc66a98e0404",
    "64ba14a8f3d5b7d33083d5567b43f8096b80b7d15d72bfdc12a12d92d06e0d44",
    "5790cbf8ea42f8eca1d7b56e2ee5ea073c3a423ac1daf086d655baf54e4fbdf3",
    "a13b4bdb679de98545c0427aa66e4de7f9fb1e808eb2af11deb1b35ae3bb888c",
    "35e9cdedf7b779368ccfe2a7dca520adf27c07e689dfbb40860a7338e90c1e36",
]


def _migration_checksum(version: int) -> str:
    statements = next(
        statements for number, statements in MIGRATIONS if number == version
    )
    return stable_hash(list(statements))


def test_migration6_upgrades_a_migration5_database_and_is_reopen_safe(tmp_path) -> None:
    path = tmp_path / "migration5.db"
    # Simulate a pre-1C deployed database: schema 1--5 are present and their
    # recorded checksums are untouched, then migration 6 is absent at restart.
    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE v1_schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    for version, statements in MIGRATIONS[:5]:
        for statement in statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO v1_schema_migrations VALUES (?, ?, 'before-1c')",
            (version, stable_hash(list(statements))),
        )
    connection.commit()
    connection.close()

    upgraded = V1Storage(path)
    versions = upgraded.schema_versions()
    assert [entry["version"] for entry in versions] == [1, 2, 3, 4, 5, 6, 7]
    assert [entry["checksum"] for entry in versions[:5]] == V1_TO_V5_CHECKSUMS
    assert versions[5]["checksum"] == _migration_checksum(6)
    assert versions[6]["checksum"] == _migration_checksum(7)
    assert upgraded._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert upgraded._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()

    reopened = V1Storage(path)
    assert [entry["checksum"] for entry in reopened.schema_versions()] == [
        *V1_TO_V5_CHECKSUMS,
        _migration_checksum(6),
        _migration_checksum(7),
    ]
    reopened.close()
