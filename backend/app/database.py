from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .errors import AppError
from .models import MilestoneStatus, QuestStatus
from .utils import utc_now


class Database:
    """Small, thread-safe SQLite repository used by the v0.1 runtime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=10.0, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 10000")
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quests (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                template_id TEXT,
                workspace TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step INTEGER,
                current_milestone_id TEXT,
                progress REAL NOT NULL DEFAULT 0,
                error_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS milestones (
                id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(quest_id, position)
            );

            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quest_id TEXT NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                trace_type TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                data_json TEXT NOT NULL,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(quest_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_milestones_quest
                ON milestones(quest_id, position);
            CREATE INDEX IF NOT EXISTS idx_traces_quest
                ON traces(quest_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_quests_created
                ON quests(created_at DESC);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1").fetchone()[0] == 1

    def create_quest(
        self,
        *,
        quest_id: str,
        goal: str,
        template_id: str | None,
        workspace: str,
        milestones: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO quests(
                    id, goal, template_id, workspace, status, progress,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    quest_id,
                    goal,
                    template_id,
                    workspace,
                    QuestStatus.PLANNED.value,
                    now,
                    now,
                ),
            )
            for position, item in enumerate(milestones, start=1):
                self._conn.execute(
                    """
                    INSERT INTO milestones(
                        id, quest_id, position, title, description, status,
                        tool_name, tool_args_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        quest_id,
                        position,
                        item["title"],
                        item["description"],
                        MilestoneStatus.PENDING.value,
                        item["tool_name"],
                        _dumps(item.get("tool_args", {})),
                    ),
                )
        quest = self.get_quest(quest_id)
        assert quest is not None
        return quest

    def get_quest(self, quest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM quests WHERE id = ?", (quest_id,)
            ).fetchone()
            if row is None:
                return None
            milestones = self._conn.execute(
                "SELECT * FROM milestones WHERE quest_id = ? ORDER BY position",
                (quest_id,),
            ).fetchall()
            return self._quest_from_rows(row, milestones)

    def require_quest(self, quest_id: str) -> dict[str, Any]:
        quest = self.get_quest(quest_id)
        if quest is None:
            raise AppError(
                "QUEST_NOT_FOUND",
                f"Quest '{quest_id}' was not found",
                status_code=404,
                details={"quest_id": quest_id},
            )
        return quest

    def list_quests(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM quests ORDER BY created_at DESC"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                milestones = self._conn.execute(
                    "SELECT * FROM milestones WHERE quest_id = ? ORDER BY position",
                    (row["id"],),
                ).fetchall()
                result.append(self._quest_from_rows(row, milestones))
            return result

    def claim_for_run(self, quest_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT status FROM quests WHERE id = ?", (quest_id,)
            ).fetchone()
            if row is None:
                raise AppError(
                    "QUEST_NOT_FOUND",
                    f"Quest '{quest_id}' was not found",
                    status_code=404,
                    details={"quest_id": quest_id},
                )
            if row["status"] != QuestStatus.PLANNED.value:
                raise AppError(
                    "QUEST_NOT_RUNNABLE",
                    f"Quest in '{row['status']}' state cannot be started",
                    status_code=409,
                    details={"quest_id": quest_id, "status": row["status"]},
                )
            self._conn.execute(
                """
                UPDATE quests
                SET status = ?, started_at = ?, updated_at = ?, error_json = NULL
                WHERE id = ?
                """,
                (QuestStatus.RUNNING.value, now, now, quest_id),
            )
        return self.require_quest(quest_id)

    def start_milestone(self, quest_id: str, milestone_id: str, position: int) -> None:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE milestones SET status = ?, started_at = ?, error_json = NULL
                WHERE id = ? AND quest_id = ?
                """,
                (MilestoneStatus.RUNNING.value, now, milestone_id, quest_id),
            )
            self._conn.execute(
                """
                UPDATE quests
                SET current_step = ?, current_milestone_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (position, milestone_id, now, quest_id),
            )

    def complete_milestone(
        self, quest_id: str, milestone_id: str, result: dict[str, Any]
    ) -> None:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE milestones
                SET status = ?, result_json = ?, finished_at = ?
                WHERE id = ? AND quest_id = ?
                """,
                (
                    MilestoneStatus.COMPLETED.value,
                    _dumps(result),
                    now,
                    milestone_id,
                    quest_id,
                ),
            )
            counts = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS done
                FROM milestones WHERE quest_id = ?
                """,
                (MilestoneStatus.COMPLETED.value, quest_id),
            ).fetchone()
            progress = float(counts["done"] or 0) / max(int(counts["total"]), 1)
            self._conn.execute(
                "UPDATE quests SET progress = ?, updated_at = ? WHERE id = ?",
                (progress, now, quest_id),
            )

    def fail_milestone(
        self, quest_id: str, milestone_id: str, error: dict[str, Any]
    ) -> None:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE milestones
                SET status = ?, error_json = ?, finished_at = ?
                WHERE id = ? AND quest_id = ?
                """,
                (
                    MilestoneStatus.FAILED.value,
                    _dumps(error),
                    now,
                    milestone_id,
                    quest_id,
                ),
            )

    def complete_quest(self, quest_id: str) -> None:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE quests
                SET status = ?, progress = 1, current_step = NULL,
                    current_milestone_id = NULL, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (QuestStatus.COMPLETED.value, now, now, quest_id),
            )

    def fail_quest(self, quest_id: str, error: dict[str, Any]) -> None:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE quests
                SET status = ?, error_json = ?, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (QuestStatus.FAILED.value, _dumps(error), now, now, quest_id),
            )

    def add_trace(
        self,
        quest_id: str,
        *,
        trace_type: str,
        message: str,
        level: str = "info",
        data: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._conn:
            next_sequence = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM traces WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()[0]
            cursor = self._conn.execute(
                """
                INSERT INTO traces(
                    quest_id, sequence, trace_type, level, message,
                    data_json, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quest_id,
                    next_sequence,
                    trace_type,
                    level,
                    message,
                    _dumps(data or {}),
                    duration_ms,
                    now,
                ),
            )
            trace_id = int(cursor.lastrowid)
            row = self._conn.execute(
                "SELECT * FROM traces WHERE id = ?", (trace_id,)
            ).fetchone()
            return self._trace_from_row(row)

    def list_traces(self, quest_id: str) -> list[dict[str, Any]]:
        self.require_quest(quest_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM traces WHERE quest_id = ? ORDER BY sequence",
                (quest_id,),
            ).fetchall()
            return [self._trace_from_row(row) for row in rows]

    @staticmethod
    def _quest_from_rows(
        row: sqlite3.Row, milestone_rows: Iterable[sqlite3.Row]
    ) -> dict[str, Any]:
        milestones = [Database._milestone_from_row(item) for item in milestone_rows]
        progress = min(max(float(row["progress"]), 0.0), 1.0)
        return {
            "id": row["id"],
            "goal": row["goal"],
            "template_id": row["template_id"],
            "workspace": row["workspace"],
            "status": row["status"],
            "current_step": row["current_step"],
            "current_milestone_id": row["current_milestone_id"],
            "progress": progress,
            "progress_percent": round(progress * 100),
            "milestones": milestones,
            "error": _loads(row["error_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _milestone_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "position": row["position"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "tool_name": row["tool_name"],
            "tool_args": _loads(row["tool_args_json"]) or {},
            "result": _loads(row["result_json"]),
            "error": _loads(row["error_json"]),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _trace_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "quest_id": row["quest_id"],
            "sequence": row["sequence"],
            "trace_type": row["trace_type"],
            "level": row["level"],
            "message": row["message"],
            "data": _loads(row["data_json"]) or {},
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
        }


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None) -> Any:
    return json.loads(value) if value else None
