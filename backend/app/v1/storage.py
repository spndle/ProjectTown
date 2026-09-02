from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..runtime import Event, EventReducer, stable_hash
from ..utils import utc_now

EVENT_SCHEMA_VERSION = 1
MODEL_MAX_ATTEMPTS = 3
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SHADOW_PROVENANCE_STATUSES = {
    "shadow_observed_created",
    "shadow_observed_modified",
    "shadow_observed_unchanged",
    "shadow_observed_restored",
    "shadow_existing_unchanged",
    "shadow_external_drift",
    "shadow_unobserved_created",
}
_UNRECOVERABLE_PROVENANCE_STATUSES = {
    "unrecoverable_invalid_input",
    "unrecoverable_invalid_artifact",
    "unrecoverable_final_unstable",
    "unrecoverable_final_unsupported",
    "unrecoverable_final_limit_exceeded",
    "unrecoverable_final_legacy_unobserved",
    "unrecoverable_final_unrecoverable",
    "unrecoverable_final_invalid",
    "unrecoverable_final_entries",
    "unrecoverable_final_missing",
    "unrecoverable_final_hash_mismatch",
    "unrecoverable_final_size_mismatch",
    "unrecoverable_baseline_unstable",
    "unrecoverable_baseline_unsupported",
    "unrecoverable_baseline_limit_exceeded",
    "unrecoverable_baseline_unrecoverable",
    "unrecoverable_baseline_invalid",
    "unrecoverable_snapshot_binding",
    "unrecoverable_baseline_entries",
    "unrecoverable_invalid_actions",
    "unrecoverable_unresolved_effect",
    "unrecoverable_invalid_observations",
    "unrecoverable_duplicate_observation",
    "unrecoverable_event_binding",
    "unrecoverable_action_binding",
    "unrecoverable_missing_observation",
    "unrecoverable_observation_binding",
    "unrecoverable_chain_break",
    "unrecoverable_chain_terminal_mismatch",
}

# Phase 1C's provider authorization is deliberately fixed in the database
# schema rather than being a mutable runtime setting.  Amounts are integer
# micro-CNY, so no floating point arithmetic can expand the approved budget.
PHASE1C_COST_ACCOUNT_ID = "phase1c-openai-gpt5mini-cny-v1"
PHASE1C_PROVIDER = "openai"
PHASE1C_MODEL_ALIAS = "gpt-5-mini"
PHASE1C_MODEL_SNAPSHOT = "gpt-5-mini-2025-08-07"
PHASE1C_PRICING_VERSION = "openai-2026-08-20"
PHASE1C_FX_MICRO_CNY_PER_USD = 8_000_000
PHASE1C_INPUT_MICRO_CNY_PER_TOKEN = 2
PHASE1C_OUTPUT_MICRO_CNY_PER_TOKEN = 16
PHASE1C_MAX_CALL_MICRO_CNY = 500_000
PHASE1C_MAX_TOTAL_MICRO_CNY = 20_000_000

# Qwen's native CNY pricing is intentionally a distinct immutable account.
# Actual evaluation settlement is conservatively rounded up to 1/2 micro-CNY
# per input/output token; the reservation uses the published thinking-output
# rate (8 micro-CNY) even though the adapter disables thinking.
QWEN_COST_ACCOUNT_ID = "phase2-qwen-plus-cny-v1"
QWEN_PROVIDER = "qwen"
QWEN_MODEL_ALIAS = "qwen-plus"
QWEN_MODEL_SNAPSHOT = "qwen-plus"
QWEN_PRICING_VERSION = "dashscope-beijing-2026-08-21"
QWEN_FX_MICRO_CNY_PER_USD = 1
QWEN_INPUT_MICRO_CNY_PER_TOKEN = 1
QWEN_OUTPUT_MICRO_CNY_PER_TOKEN = 2
QWEN_RESERVED_OUTPUT_MICRO_CNY_PER_TOKEN = 8
QWEN_MAX_CALL_MICRO_CNY = 500_000
QWEN_MAX_TOTAL_MICRO_CNY = 20_000_000

_COST_PROFILES: dict[tuple[str, str], dict[str, int | str]] = {
    (PHASE1C_PROVIDER, PHASE1C_MODEL_SNAPSHOT): {
        "account_id": PHASE1C_COST_ACCOUNT_ID,
        "provider": PHASE1C_PROVIDER,
        "model_alias": PHASE1C_MODEL_ALIAS,
        "model_snapshot": PHASE1C_MODEL_SNAPSHOT,
        "pricing_version": PHASE1C_PRICING_VERSION,
        "fx_micro_cny_per_usd": PHASE1C_FX_MICRO_CNY_PER_USD,
        "input_rate": PHASE1C_INPUT_MICRO_CNY_PER_TOKEN,
        "output_rate": PHASE1C_OUTPUT_MICRO_CNY_PER_TOKEN,
        "reserve_output_rate": PHASE1C_OUTPUT_MICRO_CNY_PER_TOKEN,
        "max_call": PHASE1C_MAX_CALL_MICRO_CNY,
        "max_total": PHASE1C_MAX_TOTAL_MICRO_CNY,
    },
    (QWEN_PROVIDER, QWEN_MODEL_SNAPSHOT): {
        "account_id": QWEN_COST_ACCOUNT_ID,
        "provider": QWEN_PROVIDER,
        "model_alias": QWEN_MODEL_ALIAS,
        "model_snapshot": QWEN_MODEL_SNAPSHOT,
        "pricing_version": QWEN_PRICING_VERSION,
        "fx_micro_cny_per_usd": QWEN_FX_MICRO_CNY_PER_USD,
        "input_rate": QWEN_INPUT_MICRO_CNY_PER_TOKEN,
        "output_rate": QWEN_OUTPUT_MICRO_CNY_PER_TOKEN,
        "reserve_output_rate": QWEN_RESERVED_OUTPUT_MICRO_CNY_PER_TOKEN,
        "max_call": QWEN_MAX_CALL_MICRO_CNY,
        "max_total": QWEN_MAX_TOTAL_MICRO_CNY,
    },
}


def _json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE v1_quests (
                quest_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_goal_contracts (
                contract_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                contract_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(contract_id, version),
                UNIQUE(quest_id, version)
            )
            """,
            """
            CREATE TABLE v1_plan_versions (
                plan_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(plan_id, version),
                UNIQUE(quest_id, version)
            )
            """,
            """
            CREATE TABLE v1_plan_milestones (
                plan_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                milestone_id TEXT NOT NULL,
                title TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY(plan_id, version, milestone_id),
                FOREIGN KEY(plan_id, version)
                    REFERENCES v1_plan_versions(plan_id, version)
            )
            """,
            """
            CREATE TABLE v1_plan_dependencies (
                plan_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                milestone_id TEXT NOT NULL,
                depends_on TEXT NOT NULL,
                PRIMARY KEY(plan_id, version, milestone_id, depends_on),
                FOREIGN KEY(plan_id, version, milestone_id)
                    REFERENCES v1_plan_milestones(plan_id, version, milestone_id),
                FOREIGN KEY(plan_id, version, depends_on)
                    REFERENCES v1_plan_milestones(plan_id, version, milestone_id)
            )
            """,
            """
            CREATE TABLE v1_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_schema_version INTEGER NOT NULL,
                state_version_before INTEGER NOT NULL,
                state_version_after INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(quest_id, sequence)
            )
            """,
            """
            CREATE TABLE v1_checkpoints (
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                state_version INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(quest_id, state_version)
            )
            """,
            """
            CREATE TABLE v1_tool_actions (
                action_id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                milestone_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                pre_effect_hash TEXT,
                expected_state_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(quest_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE v1_evidence (
                id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_verification_results (
                id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_progress_entries (
                id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_decisions (
                id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_leases (
                quest_id TEXT PRIMARY KEY REFERENCES v1_quests(quest_id),
                owner TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE v1_resource_leases (
                resource_key TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                owner TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE v1_benchmark_runs (
                run_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_benchmark_results (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES v1_benchmark_runs(run_id),
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX v1_idx_events_quest ON v1_events(quest_id, sequence)",
            "CREATE INDEX v1_idx_evidence_quest ON v1_evidence(quest_id, created_at)",
            "CREATE INDEX v1_idx_results_run ON v1_benchmark_results(run_id)",
            "CREATE INDEX v1_idx_actions_quest ON v1_tool_actions(quest_id, status)",
        ),
    ),
    (
        2,
        tuple(
            statement
            for table in (
                "v1_events",
                "v1_goal_contracts",
                "v1_plan_versions",
                "v1_plan_milestones",
                "v1_plan_dependencies",
                "v1_evidence",
                "v1_verification_results",
            )
            for statement in (
                f"""
                CREATE TRIGGER {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} is immutable');
                END
                """,
                f"""
                CREATE TRIGGER {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} is immutable');
                END
                """,
            )
        ),
    ),
    (
        3,
        (
            "ALTER TABLE v1_tool_actions ADD COLUMN committed_event_id INTEGER REFERENCES v1_events(event_id)",
            "CREATE UNIQUE INDEX v1_idx_actions_committed_event ON v1_tool_actions(committed_event_id) WHERE committed_event_id IS NOT NULL",
        ),
    ),
    (
        4,
        (
            """
            CREATE TABLE v1_artifact_reviews (
                quest_id TEXT PRIMARY KEY REFERENCES v1_quests(quest_id),
                review_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
        ),
    ),
    (
        5,
        (
            """
            CREATE TABLE v1_model_calls (
                call_id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                purpose TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_schema_version INTEGER NOT NULL CHECK(request_schema_version > 0),
                candidate_schema_version INTEGER NOT NULL CHECK(candidate_schema_version > 0),
                input_hash TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_version INTEGER NOT NULL CHECK(contract_version > 0),
                contract_hash TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL CHECK(plan_version > 0),
                plan_hash TEXT NOT NULL,
                expected_state_version INTEGER NOT NULL CHECK(expected_state_version >= 0),
                max_tokens INTEGER NOT NULL CHECK(max_tokens >= 0),
                status TEXT NOT NULL CHECK(status IN ('prepared', 'dispatched', 'succeeded', 'failed', 'unknown_outcome')),
                winning_attempt_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(quest_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE v1_model_attempts (
                attempt_id TEXT PRIMARY KEY,
                call_id TEXT NOT NULL REFERENCES v1_model_calls(call_id),
                attempt_no INTEGER NOT NULL CHECK(attempt_no > 0),
                dispatch_token TEXT NOT NULL UNIQUE,
                adapter TEXT NOT NULL,
                model TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('prepared', 'dispatched', 'succeeded', 'failed', 'unknown_outcome')),
                validation_status TEXT NOT NULL CHECK(validation_status IN ('pending', 'validated_current', 'invalid', 'stale', 'conflict', 'cancelled_before_dispatch')),
                reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens >= 0),
                settled_tokens INTEGER NOT NULL DEFAULT 0 CHECK(settled_tokens >= 0),
                input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
                output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
                candidate_json TEXT CHECK(candidate_json IS NULL OR length(candidate_json) <= 262144),
                candidate_hash TEXT,
                response_hash TEXT,
                usage_json TEXT,
                cost_json TEXT,
                error_json TEXT,
                created_at TEXT NOT NULL,
                dispatched_at TEXT,
                settled_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(call_id, attempt_no)
            )
            """,
            "CREATE INDEX v1_idx_model_calls_quest ON v1_model_calls(quest_id, created_at)",
            "CREATE INDEX v1_idx_model_attempts_call ON v1_model_attempts(call_id, attempt_no)",
            "CREATE UNIQUE INDEX v1_idx_model_attempts_current_winner ON v1_model_attempts(call_id) WHERE validation_status = 'validated_current'",
            """
            CREATE TRIGGER v1_model_calls_binding_immutable
            BEFORE UPDATE OF call_id, quest_id, purpose, idempotency_key,
                request_schema_version, candidate_schema_version, input_hash,
                prompt_version, contract_id, contract_version, contract_hash,
                plan_id, plan_version, plan_hash, expected_state_version, max_tokens
            ON v1_model_calls
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_calls binding is immutable');
            END
            """,
            """
            CREATE TRIGGER v1_model_attempts_binding_immutable
            BEFORE UPDATE OF attempt_id, call_id, attempt_no, dispatch_token,
                adapter, model, parameters_json, reserved_tokens, created_at
            ON v1_model_attempts
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_attempts binding is immutable');
            END
            """,
            """
            CREATE TRIGGER v1_model_calls_immutable_delete
            BEFORE DELETE ON v1_model_calls
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_calls are immutable');
            END
            """,
            """
            CREATE TRIGGER v1_model_attempts_immutable_delete
            BEFORE DELETE ON v1_model_attempts
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_attempts are immutable');
            END
            """,
        ),
    ),
    (
        6,
        (
            """
            CREATE TABLE v1_model_cost_accounts (
                account_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model_alias TEXT NOT NULL,
                model_snapshot TEXT NOT NULL,
                pricing_version TEXT NOT NULL,
                fx_micro_cny_per_usd INTEGER NOT NULL CHECK(fx_micro_cny_per_usd > 0),
                max_call_micro_cny INTEGER NOT NULL CHECK(max_call_micro_cny > 0),
                max_total_micro_cny INTEGER NOT NULL CHECK(max_total_micro_cny > 0),
                breached INTEGER NOT NULL DEFAULT 0 CHECK(breached IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_model_cost_reservations (
                attempt_id TEXT PRIMARY KEY REFERENCES v1_model_attempts(attempt_id),
                account_id TEXT NOT NULL REFERENCES v1_model_cost_accounts(account_id),
                provider TEXT NOT NULL,
                model_alias TEXT NOT NULL,
                model_snapshot TEXT NOT NULL,
                pricing_version TEXT NOT NULL,
                fx_micro_cny_per_usd INTEGER NOT NULL CHECK(fx_micro_cny_per_usd > 0),
                estimated_input_tokens INTEGER NOT NULL CHECK(estimated_input_tokens >= 0),
                max_output_tokens INTEGER NOT NULL CHECK(max_output_tokens >= 0),
                reserved_micro_cny INTEGER NOT NULL CHECK(reserved_micro_cny >= 0),
                settled_micro_cny INTEGER,
                status TEXT NOT NULL CHECK(status IN ('held', 'settled', 'unknown', 'released')),
                created_at TEXT NOT NULL,
                settled_at TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX v1_idx_model_cost_reservations_account ON v1_model_cost_reservations(account_id, status)",
            """
            CREATE TRIGGER v1_model_cost_accounts_binding_immutable
            BEFORE UPDATE OF account_id, provider, model_alias, model_snapshot,
                pricing_version, fx_micro_cny_per_usd, max_call_micro_cny,
                max_total_micro_cny
            ON v1_model_cost_accounts
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_cost_accounts binding is immutable');
            END
            """,
            """
            CREATE TRIGGER v1_model_cost_reservations_binding_immutable
            BEFORE UPDATE OF attempt_id, account_id, provider, model_alias,
                model_snapshot, pricing_version, fx_micro_cny_per_usd,
                estimated_input_tokens, max_output_tokens, reserved_micro_cny,
                created_at
            ON v1_model_cost_reservations
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_cost_reservations binding is immutable');
            END
            """,
            """
            CREATE TRIGGER v1_model_cost_accounts_immutable_delete
            BEFORE DELETE ON v1_model_cost_accounts
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_cost_accounts are immutable');
            END
            """,
            """
            CREATE TRIGGER v1_model_cost_reservations_immutable_delete
            BEFORE DELETE ON v1_model_cost_reservations
            BEGIN
                SELECT RAISE(ABORT, 'v1_model_cost_reservations are immutable');
            END
            """,
        ),
    ),
    (
        7,
        (
            """
            CREATE TABLE v1_workspace_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                kind TEXT NOT NULL CHECK(kind IN ('baseline', 'final')),
                policy_version TEXT NOT NULL,
                workspace TEXT NOT NULL,
                root_hash TEXT,
                file_count INTEGER NOT NULL CHECK(file_count >= 0),
                total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
                status TEXT NOT NULL CHECK(status IN (
                    'complete', 'unstable', 'unsupported', 'limit_exceeded',
                    'legacy_unobserved', 'unrecoverable'
                )),
                state_version INTEGER,
                event_sequence INTEGER,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE v1_workspace_snapshot_entries (
                snapshot_id TEXT NOT NULL REFERENCES v1_workspace_snapshots(snapshot_id),
                relative_path TEXT NOT NULL,
                file_type TEXT NOT NULL CHECK(file_type = 'regular'),
                size INTEGER NOT NULL CHECK(size >= 0),
                sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, relative_path),
                CHECK(relative_path <> '' AND relative_path NOT LIKE '/%' AND relative_path NOT LIKE '%\\%'),
                CHECK(sha256 NOT GLOB '*[^0-9a-f]*' AND length(sha256) = 64)
            )
            """,
            """
            CREATE TABLE v1_tool_file_observations (
                observation_id TEXT PRIMARY KEY,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                action_id TEXT NOT NULL REFERENCES v1_tool_actions(action_id),
                committed_event_id INTEGER NOT NULL REFERENCES v1_events(event_id),
                relative_path TEXT NOT NULL,
                before_sha256 TEXT,
                after_sha256 TEXT,
                after_size_bytes INTEGER NOT NULL CHECK(after_size_bytes >= 0),
                change_kind TEXT NOT NULL CHECK(change_kind IN ('created', 'modified', 'deleted', 'unchanged', 'unsupported')),
                status TEXT NOT NULL CHECK(status IN ('observed', 'unstable', 'unsupported', 'unrecoverable')),
                created_at TEXT NOT NULL,
                CHECK(relative_path <> '' AND relative_path NOT LIKE '/%' AND relative_path NOT LIKE '%\\%'),
                CHECK(before_sha256 IS NULL OR (before_sha256 NOT GLOB '*[^0-9a-f]*' AND length(before_sha256) = 64)),
                CHECK(after_sha256 IS NULL OR (after_sha256 NOT GLOB '*[^0-9a-f]*' AND length(after_sha256) = 64))
            )
            """,
            """
            CREATE TABLE v1_artifact_provenance (
                provenance_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                quest_id TEXT NOT NULL REFERENCES v1_quests(quest_id),
                baseline_snapshot_id TEXT REFERENCES v1_workspace_snapshots(snapshot_id),
                final_snapshot_id TEXT REFERENCES v1_workspace_snapshots(snapshot_id),
                action_id TEXT REFERENCES v1_tool_actions(action_id),
                committed_event_id INTEGER REFERENCES v1_events(event_id),
                evidence_id TEXT REFERENCES v1_evidence(id),
                artifact_hash TEXT,
                status TEXT NOT NULL CHECK(status IN ('shadow', 'unrecoverable', 'legacy_unobserved')),
                created_at TEXT NOT NULL,
                UNIQUE(artifact_id, quest_id),
                CHECK(artifact_hash IS NULL OR (artifact_hash NOT GLOB '*[^0-9a-f]*' AND length(artifact_hash) = 64))
            )
            """,
            "CREATE INDEX v1_idx_workspace_snapshots_quest ON v1_workspace_snapshots(quest_id, kind, created_at)",
            "CREATE UNIQUE INDEX v1_idx_workspace_snapshots_one_baseline ON v1_workspace_snapshots(quest_id) WHERE kind = 'baseline'",
            "CREATE INDEX v1_idx_workspace_snapshot_entries_snapshot ON v1_workspace_snapshot_entries(snapshot_id, relative_path)",
            "CREATE INDEX v1_idx_tool_file_observations_action ON v1_tool_file_observations(action_id, committed_event_id)",
            "CREATE UNIQUE INDEX v1_idx_tool_file_observations_one_per_action ON v1_tool_file_observations(action_id)",
            "CREATE INDEX v1_idx_artifact_provenance_quest ON v1_artifact_provenance(quest_id, artifact_id)",
            *(
                statement
                for table in (
                    "v1_workspace_snapshots",
                    "v1_workspace_snapshot_entries",
                    "v1_tool_file_observations",
                    "v1_artifact_provenance",
                )
                for statement in (
                    f"""
                    CREATE TRIGGER {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is immutable');
                    END
                    """,
                    f"""
                    CREATE TRIGGER {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is immutable');
                    END
                    """,
                )
            ),
        ),
    ),
)


class V1Storage:
    """Single-node SQLite Event Store and v1 read projections.

    The legacy v0.1 tables are never changed. All v1 Quest state mutations go
    through :meth:`append_event`, which appends an event and reduces the read
    projection in the same short SQLite transaction.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function(
            "projecttown_casefold",
            1,
            lambda value: "" if value is None else str(value).casefold(),
            deterministic=True,
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 10000")
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1").fetchone()[0] == 1

    def schema_versions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT version, checksum, applied_at
                FROM v1_schema_migrations ORDER BY version
                """
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _normalise_snapshot(
        snapshot: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raw = dict(snapshot)
        entries = [dict(item) for item in raw.pop("entries", [])]
        status = str(raw.get("status", ""))
        if status not in {
            "complete",
            "unstable",
            "unsupported",
            "limit_exceeded",
            "legacy_unobserved",
            "unrecoverable",
        }:
            raise ValueError("invalid workspace snapshot status")
        workspace = str(raw.get("workspace", ""))
        policy_version = str(raw.get("policy_version", ""))
        if not workspace or not policy_version or "\x00" in workspace:
            raise ValueError("workspace snapshot binding is invalid")
        root_hash = raw.get("root_hash")
        if root_hash is not None and (
            not isinstance(root_hash, str) or not _SHA256_HEX.fullmatch(root_hash)
        ):
            raise ValueError("workspace snapshot root hash is invalid")
        normalised_entries: list[dict[str, Any]] = []
        total_bytes = 0
        for entry in entries:
            relative_path = str(entry.get("relative_path", ""))
            if (
                not relative_path
                or relative_path.startswith("/")
                or "\\" in relative_path
                or any(part in {"", ".", ".."} for part in relative_path.split("/"))
            ):
                raise ValueError("workspace snapshot path is invalid")
            digest = str(entry.get("sha256", ""))
            size = entry.get("size")
            if entry.get("file_type") != "regular" or not _SHA256_HEX.fullmatch(digest):
                raise ValueError("workspace snapshot entry is invalid")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError("workspace snapshot entry size is invalid")
            normalised_entries.append(
                {
                    "relative_path": relative_path,
                    "file_type": "regular",
                    "size": size,
                    "sha256": digest,
                }
            )
            total_bytes += size
        normalised_entries.sort(key=lambda item: item["relative_path"])
        if len({entry["relative_path"] for entry in normalised_entries}) != len(
            normalised_entries
        ):
            raise ValueError("workspace snapshot paths must be unique")
        file_count = raw.get("file_count")
        declared_total = raw.get("total_bytes")
        if (
            isinstance(file_count, bool)
            or not isinstance(file_count, int)
            or file_count < 0
        ):
            raise ValueError("workspace snapshot file count is invalid")
        if (
            isinstance(declared_total, bool)
            or not isinstance(declared_total, int)
            or declared_total < 0
        ):
            raise ValueError("workspace snapshot total bytes is invalid")
        if status == "complete":
            root_payload = {
                "policy_version": policy_version,
                "entries": normalised_entries,
            }
            expected_root_hash = hashlib.sha256(
                json.dumps(
                    root_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                root_hash != expected_root_hash
                or file_count != len(normalised_entries)
                or declared_total != total_bytes
            ):
                raise ValueError("complete workspace snapshot is inconsistent")
        elif (
            normalised_entries
            or root_hash is not None
            or file_count != 0
            or declared_total != 0
        ):
            raise ValueError("incomplete workspace snapshot must not contain entries")
        return {
            "workspace": workspace,
            "policy_version": policy_version,
            "root_hash": root_hash,
            "file_count": file_count,
            "total_bytes": declared_total,
            "status": status,
        }, normalised_entries

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "snapshot_id": row["snapshot_id"],
            "quest_id": row["quest_id"],
            "kind": row["kind"],
            "policy_version": row["policy_version"],
            "workspace": row["workspace"],
            "root_hash": row["root_hash"],
            "file_count": int(row["file_count"]),
            "total_bytes": int(row["total_bytes"]),
            "status": row["status"],
            "state_version": row["state_version"],
            "event_sequence": row["event_sequence"],
            "created_at": row["created_at"],
        }

    def _insert_workspace_snapshot_locked(
        self,
        snapshot_id: str,
        quest_id: str,
        kind: str,
        snapshot: Mapping[str, Any],
        *,
        state_version: int | None,
        event_sequence: int | None,
    ) -> dict[str, Any]:
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or kind not in {"baseline", "final"}
        ):
            raise ValueError("workspace snapshot binding is invalid")
        payload, entries = self._normalise_snapshot(snapshot)
        quest = self._conn.execute(
            "SELECT state_json FROM v1_quests WHERE quest_id = ?", (quest_id,)
        ).fetchone()
        if quest is None:
            raise KeyError(quest_id)
        workspace = str(json.loads(quest["state_json"]).get("workspace", ""))
        if payload["workspace"] != workspace:
            raise ValueError("workspace snapshot does not match quest workspace")
        now = utc_now()
        self._conn.execute(
            """
            INSERT INTO v1_workspace_snapshots(
                snapshot_id, quest_id, kind, policy_version, workspace, root_hash,
                file_count, total_bytes, status, state_version, event_sequence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                quest_id,
                kind,
                payload["policy_version"],
                payload["workspace"],
                payload["root_hash"],
                payload["file_count"],
                payload["total_bytes"],
                payload["status"],
                state_version,
                event_sequence,
                now,
            ),
        )
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO v1_workspace_snapshot_entries(
                    snapshot_id, relative_path, file_type, size, sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    entry["relative_path"],
                    entry["file_type"],
                    entry["size"],
                    entry["sha256"],
                ),
            )
        row = self._conn.execute(
            "SELECT * FROM v1_workspace_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        return self._snapshot_from_row(row)

    def _require_execution_admitted_event_locked(
        self,
        quest_id: str,
        expected_state_version: int,
        event_sequence: int | None,
    ) -> None:
        if (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or event_sequence <= 0
        ):
            raise ValueError("baseline snapshot event sequence is invalid")
        event = self._conn.execute(
            """
            SELECT quest_id, event_type, sequence, state_version_after
            FROM v1_events WHERE quest_id = ? AND sequence = ?
            """,
            (quest_id, event_sequence),
        ).fetchone()
        if (
            event is None
            or event["quest_id"] != quest_id
            or event["event_type"] != "ExecutionAdmitted"
            or int(event["sequence"]) != event_sequence
            or int(event["state_version_after"]) != expected_state_version
        ):
            raise ValueError("baseline snapshot execution admission is invalid")

    def save_baseline_snapshot(
        self,
        snapshot_id: str,
        quest_id: str,
        owner: str,
        expected_state_version: int,
        snapshot: Mapping[str, Any],
        *,
        event_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Atomically save a pre-tool snapshot while its execution lease is live."""

        now = time.time()
        with self._transaction():
            quest = self._conn.execute(
                "SELECT state_version FROM v1_quests WHERE quest_id = ?", (quest_id,)
            ).fetchone()
            if quest is None:
                raise KeyError(quest_id)
            if int(quest["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            lease = self._conn.execute(
                "SELECT owner FROM v1_leases WHERE quest_id = ? AND expires_at > ?",
                (quest_id, now),
            ).fetchone()
            if lease is None or lease["owner"] != owner:
                raise ValueError("live execution lease is not held by owner")
            action = self._conn.execute(
                "SELECT 1 FROM v1_tool_actions WHERE quest_id = ? LIMIT 1", (quest_id,)
            ).fetchone()
            if action is not None:
                raise ValueError("baseline snapshot must precede every tool action")
            existing = self._conn.execute(
                "SELECT snapshot_id FROM v1_workspace_snapshots WHERE quest_id = ? AND kind = 'baseline'",
                (quest_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError("baseline snapshot already exists for quest")
            self._require_execution_admitted_event_locked(
                quest_id, expected_state_version, event_sequence
            )
            return self._insert_workspace_snapshot_locked(
                snapshot_id,
                quest_id,
                "baseline",
                snapshot,
                state_version=expected_state_version,
                event_sequence=event_sequence,
            )

    def save_final_snapshot(
        self,
        snapshot_id: str,
        quest_id: str,
        snapshot: Mapping[str, Any],
        *,
        state_version: int | None = None,
        event_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Persist a final snapshot as an immutable shadow-provenance record."""

        with self._transaction():
            if (
                self._conn.execute(
                    "SELECT 1 FROM v1_quests WHERE quest_id = ?", (quest_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(quest_id)
            return self._insert_workspace_snapshot_locked(
                snapshot_id,
                quest_id,
                "final",
                snapshot,
                state_version=state_version,
                event_sequence=event_sequence,
            )

    def get_workspace_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v1_workspace_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            return self._snapshot_from_row(row) if row is not None else None

    def list_workspace_snapshots(self, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM v1_workspace_snapshots WHERE quest_id = ? ORDER BY created_at, snapshot_id",
                (quest_id,),
            ).fetchall()
            return [self._snapshot_from_row(row) for row in rows]

    def list_workspace_snapshot_entries(self, snapshot_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT relative_path, file_type, size, sha256
                FROM v1_workspace_snapshot_entries
                WHERE snapshot_id = ? ORDER BY relative_path
                """,
                (snapshot_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_baseline_snapshot(self, quest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM v1_workspace_snapshots
                WHERE quest_id = ? AND kind = 'baseline'
                """,
                (quest_id,),
            ).fetchone()
            return self._snapshot_from_row(row) if row is not None else None

    def record_legacy_unobserved_baseline(
        self,
        snapshot_id: str,
        quest_id: str,
        owner: str,
        expected_state_version: int,
        *,
        event_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Record that a pre-provenance action has no reconstructable baseline."""

        now = time.time()
        with self._transaction():
            quest = self._conn.execute(
                "SELECT state_version, state_json FROM v1_quests WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()
            if quest is None:
                raise KeyError(quest_id)
            if int(quest["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            lease = self._conn.execute(
                "SELECT owner FROM v1_leases WHERE quest_id = ? AND expires_at > ?",
                (quest_id, now),
            ).fetchone()
            if lease is None or lease["owner"] != owner:
                raise ValueError("live execution lease is not held by owner")
            if self.get_baseline_snapshot(quest_id) is not None:
                raise ValueError("baseline snapshot already exists for quest")
            if (
                self._conn.execute(
                    "SELECT 1 FROM v1_tool_actions WHERE quest_id = ? LIMIT 1",
                    (quest_id,),
                ).fetchone()
                is None
            ):
                raise ValueError("legacy baseline requires an existing tool action")
            self._require_execution_admitted_event_locked(
                quest_id, expected_state_version, event_sequence
            )
            workspace = str(json.loads(quest["state_json"]).get("workspace", ""))
            return self._insert_workspace_snapshot_locked(
                snapshot_id,
                quest_id,
                "baseline",
                {
                    "workspace": workspace,
                    "policy_version": "legacy-unobserved-v1",
                    "status": "legacy_unobserved",
                    "root_hash": None,
                    "file_count": 0,
                    "total_bytes": 0,
                    "entries": [],
                },
                state_version=expected_state_version,
                event_sequence=event_sequence,
            )

    def list_tool_actions(self, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE quest_id = ? ORDER BY created_at, action_id",
                (quest_id,),
            ).fetchall()
            return [self._action_from_row(row) for row in rows]

    def list_tool_file_observations(self, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT o.*, e.sequence AS committed_event_sequence
                FROM v1_tool_file_observations AS o
                JOIN v1_tool_actions AS a ON a.action_id = o.action_id AND a.quest_id = o.quest_id
                JOIN v1_events AS e ON e.event_id = o.committed_event_id
                    AND e.quest_id = o.quest_id AND e.event_type = 'ToolCommitted'
                WHERE o.quest_id = ?
                ORDER BY e.sequence, o.observation_id
                """,
                (quest_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _artifact_provenance_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def get_artifact_provenance(self, provenance_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v1_artifact_provenance WHERE provenance_id = ?",
                (provenance_id,),
            ).fetchone()
            return self._artifact_provenance_from_row(row) if row is not None else None

    def list_artifact_provenance(self, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM v1_artifact_provenance
                WHERE quest_id = ? ORDER BY artifact_id, provenance_id
                """,
                (quest_id,),
            ).fetchall()
            return [self._artifact_provenance_from_row(row) for row in rows]

    def _insert_artifact_provenance_locked(
        self, quest_id: str, item: Mapping[str, Any], final_snapshot_id: str
    ) -> None:
        provenance_id = item.get("provenance_id")
        artifact_id = item.get("artifact_id")
        status = item.get("status")
        provenance_status = item.get("provenance_status")
        provenance_mode = item.get("provenance_mode")
        baseline_snapshot_id = item.get("baseline_snapshot_id")
        evidence_id = item.get("evidence_id")
        artifact_hash = item.get("artifact_hash")
        action_id = item.get("action_id")
        committed_event_id = item.get("committed_event_id")
        if (
            not all(
                isinstance(value, str) and value
                for value in (
                    provenance_id,
                    artifact_id,
                    status,
                    provenance_status,
                    baseline_snapshot_id,
                    evidence_id,
                    artifact_hash,
                )
            )
            or status not in {"shadow", "legacy_unobserved", "unrecoverable"}
            or not _SHA256_HEX.fullmatch(str(artifact_hash))
        ):
            raise ValueError("artifact provenance is invalid")
        if provenance_mode != "compatibility_shadow":
            raise ValueError("artifact provenance mode is invalid")
        if (
            (
                status == "shadow"
                and provenance_status not in _SHADOW_PROVENANCE_STATUSES
            )
            or (
                status == "legacy_unobserved"
                and provenance_status != "legacy_unobserved"
            )
            or (
                status == "unrecoverable"
                and provenance_status not in _UNRECOVERABLE_PROVENANCE_STATUSES
            )
        ):
            raise ValueError("artifact provenance status mapping is invalid")
        quest = self._conn.execute(
            "SELECT state_json FROM v1_quests WHERE quest_id = ?", (quest_id,)
        ).fetchone()
        baseline = self._conn.execute(
            "SELECT quest_id, kind, workspace, status FROM v1_workspace_snapshots WHERE snapshot_id = ?",
            (baseline_snapshot_id,),
        ).fetchone()
        evidence = self._conn.execute(
            "SELECT quest_id FROM v1_evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        final = self._conn.execute(
            "SELECT quest_id, kind, workspace, status FROM v1_workspace_snapshots WHERE snapshot_id = ?",
            (final_snapshot_id,),
        ).fetchone()
        if (
            quest is None
            or baseline is None
            or baseline["quest_id"] != quest_id
            or baseline["kind"] != "baseline"
            or evidence is None
            or evidence["quest_id"] != quest_id
            or final is None
            or final["quest_id"] != quest_id
            or final["kind"] != "final"
        ):
            raise ValueError("artifact provenance cross-quest binding is invalid")
        workspace = str(json.loads(quest["state_json"]).get("workspace", ""))
        if baseline["workspace"] != workspace or final["workspace"] != workspace:
            raise ValueError("artifact provenance workspace binding is invalid")
        if status == "shadow" and (
            baseline["status"] != "complete" or final["status"] != "complete"
        ):
            raise ValueError("shadow artifact provenance requires complete snapshots")
        if status == "legacy_unobserved" and baseline["status"] != "legacy_unobserved":
            raise ValueError("legacy artifact provenance requires legacy baseline")
        observed = str(provenance_status).startswith("shadow_observed_")
        if observed:
            if (
                status != "shadow"
                or not isinstance(action_id, str)
                or not action_id
                or isinstance(committed_event_id, bool)
                or not isinstance(committed_event_id, int)
            ):
                raise ValueError("observed artifact provenance requires action receipt")
            action = self._conn.execute(
                "SELECT quest_id, status, committed_event_id FROM v1_tool_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            event = self._conn.execute(
                "SELECT quest_id, event_type FROM v1_events WHERE event_id = ?",
                (committed_event_id,),
            ).fetchone()
            observation = self._conn.execute(
                """
                SELECT 1 FROM v1_tool_file_observations
                WHERE quest_id = ? AND action_id = ? AND committed_event_id = ?
                    AND relative_path = ? AND after_sha256 = ?
                """,
                (
                    quest_id,
                    action_id,
                    committed_event_id,
                    item.get("path"),
                    artifact_hash,
                ),
            ).fetchone()
            if (
                action is None
                or action["quest_id"] != quest_id
                or action["status"] != "committed"
                or action["committed_event_id"] != committed_event_id
                or event is None
                or event["quest_id"] != quest_id
                or event["event_type"] != "ToolCommitted"
                or observation is None
            ):
                raise ValueError("observed artifact provenance receipt is invalid")
        elif action_id is not None or committed_event_id is not None:
            raise ValueError(
                "non-observed artifact provenance must not claim an action"
            )
        if final["status"] != "complete" and status != "unrecoverable":
            raise ValueError(
                "incomplete final snapshot requires unrecoverable provenance"
            )
        self._conn.execute(
            """
            INSERT INTO v1_artifact_provenance(
                provenance_id, artifact_id, quest_id, baseline_snapshot_id, final_snapshot_id,
                action_id, committed_event_id, evidence_id, artifact_hash, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provenance_id,
                artifact_id,
                quest_id,
                baseline_snapshot_id,
                final_snapshot_id,
                action_id,
                committed_event_id,
                evidence_id,
                artifact_hash,
                status,
                utc_now(),
            ),
        )

    def request_artifact_review_with_provenance(
        self,
        quest_id: str,
        *,
        owner: str,
        review_id: str,
        manifest: Sequence[Mapping[str, Any]],
        manifest_hash: str,
        final_snapshot_id: str,
        final_snapshot: Mapping[str, Any],
        provenance: Sequence[Mapping[str, Any]],
        expected_state_version: int,
    ) -> dict[str, Any]:
        """Atomically freeze provenance and request an artifact review."""

        materialized_manifest = [dict(item) for item in manifest]
        materialized_provenance = [dict(item) for item in provenance]
        if (
            not isinstance(owner, str)
            or not owner
            or not isinstance(review_id, str)
            or not review_id
            or not isinstance(manifest_hash, str)
            or not _SHA256_HEX.fullmatch(manifest_hash)
            or not materialized_manifest
            or stable_hash(materialized_manifest) != manifest_hash
            or len(materialized_manifest) != len(materialized_provenance)
        ):
            raise ValueError("artifact manifest binding is invalid")
        artifact_ids = [item.get("artifact_id") for item in materialized_manifest]
        provenance_ids = [item.get("provenance_id") for item in materialized_provenance]
        manifest_paths = [item.get("path") for item in materialized_manifest]
        manifest_provenance_ids = [
            item.get("provenance_id") for item in materialized_manifest
        ]
        if (
            any(not isinstance(value, str) or not value for value in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)
            or any(not isinstance(value, str) or not value for value in provenance_ids)
            or len(set(provenance_ids)) != len(provenance_ids)
            or {item.get("artifact_id") for item in materialized_provenance}
            != set(artifact_ids)
            or any(
                not isinstance(path, str)
                or not path
                or path.startswith("/")
                or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                for path in manifest_paths
            )
            or len(set(manifest_paths)) != len(manifest_paths)
            or any(
                not isinstance(value, str) or not value
                for value in manifest_provenance_ids
            )
            or len(set(manifest_provenance_ids)) != len(manifest_provenance_ids)
        ):
            raise ValueError("artifact/provenance identity is invalid")
        now = utc_now()
        with self._transaction():
            quest = self._conn.execute(
                "SELECT state_version FROM v1_quests WHERE quest_id = ?", (quest_id,)
            ).fetchone()
            if quest is None:
                raise KeyError(quest_id)
            if int(quest["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            lease = self._conn.execute(
                "SELECT owner FROM v1_leases WHERE quest_id = ? AND expires_at > ?",
                (quest_id, time.time()),
            ).fetchone()
            if lease is None or lease["owner"] != owner:
                raise ValueError("live execution lease is not held by owner")
            if (
                self._conn.execute(
                    "SELECT 1 FROM v1_events WHERE quest_id = ? AND event_type = 'ArtifactReviewRequested' LIMIT 1",
                    (quest_id,),
                ).fetchone()
                is not None
            ):
                raise ValueError("artifact review was already requested")
            self._insert_workspace_snapshot_locked(
                final_snapshot_id,
                quest_id,
                "final",
                final_snapshot,
                state_version=expected_state_version,
                event_sequence=expected_state_version + 1,
            )
            final = self.get_workspace_snapshot(final_snapshot_id)
            assert final is not None
            entries = {
                entry["relative_path"]: entry
                for entry in self.list_workspace_snapshot_entries(final_snapshot_id)
            }
            provenance_by_artifact_id = {
                str(item.get("artifact_id")): item for item in materialized_provenance
            }
            for item in materialized_manifest:
                if (
                    not _SHA256_HEX.fullmatch(str(item.get("hash", "")))
                    or isinstance(item.get("size"), bool)
                    or not isinstance(item.get("size"), int)
                    or item["size"] < 0
                    or not isinstance(item.get("evidence_id"), str)
                    or not item["evidence_id"]
                    or item.get("provenance_mode") != "compatibility_shadow"
                    or item.get("final_snapshot_id") != final_snapshot_id
                    or not isinstance(item.get("baseline_snapshot_id"), str)
                    or not item["baseline_snapshot_id"]
                    or not isinstance(item.get("provenance_status"), str)
                ):
                    raise ValueError("artifact manifest item is invalid")
                if final["status"] == "complete":
                    entry = entries.get(item["path"])
                    mismatch_status = None
                    if entry is None:
                        mismatch_status = "unrecoverable_final_missing"
                    elif entry["sha256"] != item["hash"]:
                        mismatch_status = "unrecoverable_final_hash_mismatch"
                    elif int(entry["size"]) != item["size"]:
                        mismatch_status = "unrecoverable_final_size_mismatch"
                    if mismatch_status is not None:
                        provenance_item = provenance_by_artifact_id.get(
                            str(item["artifact_id"])
                        )
                        if (
                            item.get("provenance_status") != mismatch_status
                            or provenance_item is None
                            or provenance_item.get("status") != "unrecoverable"
                            or provenance_item.get("provenance_status")
                            != mismatch_status
                        ):
                            raise ValueError(
                                "artifact manifest does not match final snapshot"
                            )
            manifest_by_id = {
                item["artifact_id"]: item for item in materialized_manifest
            }
            for item in materialized_provenance:
                manifest_item = manifest_by_id[str(item["artifact_id"])]
                if (
                    item.get("provenance_id") != manifest_item.get("provenance_id")
                    or item.get("artifact_hash") != manifest_item.get("hash")
                    or item.get("evidence_id") != manifest_item.get("evidence_id")
                    or item.get("path") != manifest_item.get("path")
                    or item.get("provenance_status")
                    != manifest_item.get("provenance_status")
                    or item.get("provenance_mode")
                    != manifest_item.get("provenance_mode")
                    or item.get("baseline_snapshot_id")
                    != manifest_item.get("baseline_snapshot_id")
                    or item.get("final_snapshot_id")
                    != manifest_item.get("final_snapshot_id")
                ):
                    raise ValueError("artifact provenance does not match manifest")
                self._insert_artifact_provenance_locked(
                    quest_id, item, final_snapshot_id
                )
            patch = {
                "status": "waiting_user",
                "artifact_manifest": materialized_manifest,
                "artifact_disposition": "pending",
                "pending_artifact_review": {
                    "review_id": review_id,
                    "manifest_hash": manifest_hash,
                    "requested_at": now,
                    "item_count": len(materialized_manifest),
                },
            }
            return self._append_locked(
                quest_id,
                "ArtifactReviewRequested",
                patch,
                expected_state_version=expected_state_version,
                event_schema_version=EVENT_SCHEMA_VERSION,
                now=now,
            )

    def _insert_tool_file_observation_locked(
        self,
        observation: Mapping[str, Any],
        *,
        action_id: str,
        quest_id: str,
        committed_event_id: int | None,
    ) -> None:
        observation_id = observation.get("observation_id")
        relative_path = observation.get("relative_path")
        before_sha256 = observation.get("before_sha256")
        after_sha256 = observation.get("after_sha256")
        after_size_bytes = observation.get("after_size_bytes")
        change_kind = observation.get("change_kind")
        status = observation.get("status")
        if (
            not isinstance(observation_id, str)
            or not observation_id
            or not isinstance(relative_path, str)
            or not relative_path
            or relative_path.startswith("/")
            or "\\" in relative_path
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
            or before_sha256 is not None
            and (
                not isinstance(before_sha256, str)
                or not _SHA256_HEX.fullmatch(before_sha256)
            )
            or not isinstance(after_sha256, str)
            or not _SHA256_HEX.fullmatch(after_sha256)
            or isinstance(after_size_bytes, bool)
            or not isinstance(after_size_bytes, int)
            or after_size_bytes < 0
            or change_kind
            not in {"created", "modified", "deleted", "unchanged", "unsupported"}
            or status not in {"observed", "unstable", "unsupported", "unrecoverable"}
            or isinstance(committed_event_id, bool)
            or not isinstance(committed_event_id, int)
        ):
            raise ValueError("tool file observation is invalid")
        action = self._conn.execute(
            """
            SELECT quest_id, status, committed_event_id
            FROM v1_tool_actions WHERE action_id = ?
            """,
            (action_id,),
        ).fetchone()
        if (
            action is None
            or action["quest_id"] != quest_id
            or action["status"] != "committed"
            or action["committed_event_id"] != committed_event_id
        ):
            raise ValueError("tool file observation action/event binding is invalid")
        event = self._conn.execute(
            "SELECT quest_id, event_type FROM v1_events WHERE event_id = ?",
            (committed_event_id,),
        ).fetchone()
        if (
            event is None
            or event["quest_id"] != quest_id
            or event["event_type"] != "ToolCommitted"
        ):
            raise ValueError("tool file observation receipt event is invalid")
        self._conn.execute(
            """
            INSERT INTO v1_tool_file_observations(
                observation_id, quest_id, action_id, committed_event_id, relative_path,
                before_sha256, after_sha256, after_size_bytes, change_kind, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                quest_id,
                action_id,
                committed_event_id,
                relative_path,
                before_sha256,
                after_sha256,
                after_size_bytes,
                change_kind,
                status,
                utc_now(),
            ),
        )

    def append_tool_file_observation(
        self,
        observation_id: str,
        quest_id: str,
        action_id: str,
        relative_path: str,
        *,
        committed_event_id: int,
        before_sha256: str | None,
        after_sha256: str | None,
        after_size_bytes: int,
        change_kind: str,
        status: str,
    ) -> None:
        """Store one immutable observation outside the Gateway commit path."""

        with self._transaction():
            action = self._conn.execute(
                "SELECT quest_id FROM v1_tool_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if action is None or action["quest_id"] != quest_id:
                raise ValueError(
                    "tool file observation action does not belong to quest"
                )
            self._insert_tool_file_observation_locked(
                {
                    "observation_id": observation_id,
                    "relative_path": relative_path,
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                    "after_size_bytes": after_size_bytes,
                    "change_kind": change_kind,
                    "status": status,
                },
                action_id=action_id,
                quest_id=quest_id,
                committed_event_id=committed_event_id,
            )

    def _migrate(self) -> None:
        # Bootstrap table only; every domain schema change is ledgered below.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS v1_schema_migrations (
                version INTEGER PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        installed = self._conn.execute(
            "SELECT version, checksum FROM v1_schema_migrations ORDER BY version"
        ).fetchall()
        known = {
            version: stable_hash(list(statements)) for version, statements in MIGRATIONS
        }
        installed_versions = [int(row["version"]) for row in installed]
        expected_prefix = list(range(1, len(installed_versions) + 1))
        if installed_versions != expected_prefix:
            raise RuntimeError("migration ledger must be a strictly continuous prefix")
        for row in installed:
            version = int(row["version"])
            expected_checksum = known.get(version)
            if expected_checksum is None:
                raise RuntimeError(f"unsupported future migration {version}")
            if row["checksum"] != expected_checksum:
                raise RuntimeError(
                    f"migration {version} checksum does not match installed schema"
                )
        for version, statements in MIGRATIONS:
            checksum = stable_hash(list(statements))
            row = self._conn.execute(
                "SELECT checksum FROM v1_schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if row is not None:
                continue
            with self._transaction():
                for statement in statements:
                    self._conn.execute(statement)
                self._conn.execute(
                    """
                    INSERT INTO v1_schema_migrations(version, checksum, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (version, checksum, utc_now()),
                )
        self._repair_committed_action_events()

    def _repair_committed_action_events(self) -> None:
        """Link legacy committed actions to their immutable receipt event.

        Old databases predate the explicit link.  Keep their history intact and
        repair only the missing forward reference; an orphan receives one
        additive receipt event so recovery never re-dispatches its side effect.
        """
        with self._transaction():
            rows = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE status = 'committed' AND committed_event_id IS NULL"
            ).fetchall()
            for row in rows:
                matches = self._conn.execute(
                    "SELECT event_id FROM v1_events WHERE quest_id = ? AND event_type = 'ToolCommitted' AND payload_json LIKE ? ORDER BY event_id",
                    (row["quest_id"], f'%"action_id":"{row["action_id"]}"%'),
                ).fetchall()
                if matches:
                    self._conn.execute(
                        "UPDATE v1_tool_actions SET committed_event_id = ? WHERE action_id = ?",
                        (matches[0]["event_id"], row["action_id"]),
                    )
                    continue
                projection = self._conn.execute(
                    "SELECT state_version FROM v1_quests WHERE quest_id = ?",
                    (row["quest_id"],),
                ).fetchone()
                receipt = {
                    "action_id": row["action_id"],
                    "idempotency_key": row["idempotency_key"],
                    "status": "committed",
                    "result": json.loads(row["result_json"]),
                    "error": None,
                }
                event = self._append_locked(
                    row["quest_id"],
                    "ToolCommitted",
                    {"last_receipt": receipt},
                    expected_state_version=int(projection["state_version"]),
                    event_schema_version=EVENT_SCHEMA_VERSION,
                )
                self._conn.execute(
                    "UPDATE v1_tool_actions SET committed_event_id = ? WHERE action_id = ?",
                    (event["id"], row["action_id"]),
                )

    class _Transaction:
        def __init__(self, storage: V1Storage) -> None:
            self.storage = storage

        def __enter__(self) -> None:
            self.storage._lock.acquire()
            try:
                self.storage._conn.execute("BEGIN IMMEDIATE")
            except Exception:
                self.storage._lock.release()
                raise

        def __exit__(self, exc_type, exc, _traceback) -> None:  # type: ignore[no-untyped-def]
            try:
                self.storage._conn.execute("ROLLBACK" if exc_type else "COMMIT")
            finally:
                self.storage._lock.release()

    def _transaction(self) -> V1Storage._Transaction:
        return self._Transaction(self)

    @staticmethod
    def _validate_plan(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_milestones = plan.get("milestones", [])
        if not isinstance(raw_milestones, Sequence) or isinstance(
            raw_milestones, (str, bytes)
        ):
            raise TypeError("plan milestones must be a list")
        milestones = [copy.deepcopy(dict(item)) for item in raw_milestones]
        identifiers = [str(item.get("id", "")) for item in milestones]
        if not identifiers or any(not item for item in identifiers):
            raise ValueError("plan must contain milestones with stable IDs")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate milestone ID")

        identifier_set = set(identifiers)
        dependencies: dict[str, set[str]] = {}
        for milestone in milestones:
            milestone_id = str(milestone["id"])
            raw_dependencies = milestone.get(
                "dependencies", milestone.get("depends_on", [])
            )
            dependency_set = {str(item) for item in raw_dependencies}
            if dependency_set - identifier_set:
                raise ValueError("missing dependency")
            if milestone_id in dependency_set:
                raise ValueError("dependency cycle")
            milestone["dependencies"] = sorted(dependency_set)
            milestone.pop("depends_on", None)
            dependencies[milestone_id] = dependency_set

        ready = sorted(
            identifier for identifier, items in dependencies.items() if not items
        )
        visited: list[str] = []
        while ready:
            current = ready.pop(0)
            visited.append(current)
            for identifier, items in dependencies.items():
                if current in items:
                    items.remove(current)
                    if (
                        not items
                        and identifier not in visited
                        and identifier not in ready
                    ):
                        ready.append(identifier)
                        ready.sort()
        if len(visited) != len(identifiers):
            raise ValueError("dependency cycle")
        return milestones

    def _insert_contract_locked(
        self,
        quest_id: str,
        contract: Mapping[str, Any],
        now: str,
    ) -> None:
        contract_id = str(contract.get("id") or f"contract_{quest_id}")
        version = int(contract.get("version", 1))
        self._conn.execute(
            """
            INSERT INTO v1_goal_contracts(
                contract_id, version, quest_id, contract_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (contract_id, version, quest_id, _json(contract), now),
        )

    def _insert_plan_locked(
        self,
        quest_id: str,
        plan: Mapping[str, Any],
        milestones: Sequence[Mapping[str, Any]],
        now: str,
    ) -> None:
        plan_id = str(plan.get("id") or f"plan_{quest_id}")
        version = int(plan.get("version", 1))
        normalized_plan = copy.deepcopy(dict(plan))
        normalized_plan["milestones"] = [dict(item) for item in milestones]
        self._conn.execute(
            """
            INSERT INTO v1_plan_versions(
                plan_id, version, quest_id, plan_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (plan_id, version, quest_id, _json(normalized_plan), now),
        )
        for milestone in milestones:
            milestone_id = str(milestone["id"])
            self._conn.execute(
                """
                INSERT INTO v1_plan_milestones(
                    plan_id, version, milestone_id, title, data_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    version,
                    milestone_id,
                    str(milestone.get("title", milestone_id)),
                    _json(milestone),
                ),
            )
        for milestone in milestones:
            for dependency in milestone.get("dependencies", []):
                self._conn.execute(
                    """
                    INSERT INTO v1_plan_dependencies(
                        plan_id, version, milestone_id, depends_on
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (plan_id, version, str(milestone["id"]), str(dependency)),
                )

    def create_draft(
        self,
        quest_id: str,
        goal_contract: Mapping[str, Any],
        plan: Mapping[str, Any],
        *,
        workspace: str | None = None,
        route: Sequence[str] | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        milestones = self._validate_plan(plan)
        now = utc_now()
        milestone_state = [
            {
                **dict(item),
                "status": str(item.get("status", "pending")),
                "evidence_ids": list(item.get("evidence_ids", [])),
                "attempt": int(item.get("attempt", 0)),
            }
            for item in milestones
        ]
        initial_state = {
            "id": quest_id,
            "goal": str(goal_contract.get("goal", "")),
            "workspace": workspace or str(goal_contract.get("workspace", "")),
            "status": status,
            "state_version": 0,
            "plan_id": str(plan.get("id", f"plan_{quest_id}")),
            "plan_version": int(plan.get("version", 1)),
            "plan_metadata": copy.deepcopy(dict(plan.get("metadata", {}))),
            "template_id": plan.get("metadata", {}).get("template_id"),
            "contract": copy.deepcopy(dict(goal_contract)),
            "milestones": milestone_state,
            "current_milestone_id": None,
            "progress": 0.0,
            "route": list(route or ["agent"]),
            "budget_usage": {
                "steps": 0,
                "tool_calls": 0,
                "messages": 0,
                "tokens": 0,
                "replans": 0,
            },
            "pause_requested": False,
            "recovery_required": False,
            "artifact_review_required": False,
            "artifact_disposition": "not_applicable",
            "pending_artifact_review": None,
            "legacy_unverified": False,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO v1_quests(
                    quest_id, state_json, state_version, status,
                    created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, ?)
                """,
                (quest_id, _json({}), status, now, now),
            )
            self._insert_contract_locked(quest_id, goal_contract, now)
            self._insert_plan_locked(quest_id, plan, milestones, now)
            self._append_locked(
                quest_id,
                "QuestDrafted",
                initial_state,
                expected_state_version=0,
                event_schema_version=EVENT_SCHEMA_VERSION,
                now=now,
            )
        return self.require_quest(quest_id)

    def _append_locked(
        self,
        quest_id: str,
        event_type: str,
        patch: Mapping[str, Any],
        *,
        expected_state_version: int,
        event_schema_version: int,
        now: str | None = None,
    ) -> dict[str, Any]:
        if event_schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("unknown event schema version")
        row = self._conn.execute(
            """
            SELECT state_json, state_version FROM v1_quests WHERE quest_id = ?
            """,
            (quest_id,),
        ).fetchone()
        if row is None:
            raise KeyError(quest_id)
        if int(row["state_version"]) != expected_state_version:
            raise ValueError("state version conflict")

        timestamp = now or utc_now()
        after = expected_state_version + 1
        event_patch = copy.deepcopy(dict(patch))
        event_patch["state_version"] = after
        event_patch["updated_at"] = timestamp
        payload = {"patch": event_patch}
        state = json.loads(row["state_json"])
        new_state = EventReducer().apply(state, Event(event_type, payload))
        state_hash = stable_hash(new_state)
        cursor = self._conn.execute(
            """
            INSERT INTO v1_events(
                quest_id, sequence, event_type, event_schema_version,
                state_version_before, state_version_after, payload_json,
                state_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quest_id,
                after,
                event_type,
                event_schema_version,
                expected_state_version,
                after,
                _json(payload),
                state_hash,
                timestamp,
            ),
        )
        self._conn.execute(
            """
            UPDATE v1_quests
            SET state_json = ?, state_version = ?, status = ?, updated_at = ?
            WHERE quest_id = ? AND state_version = ?
            """,
            (
                _json(new_state),
                after,
                str(new_state.get("status", "draft")),
                timestamp,
                quest_id,
                expected_state_version,
            ),
        )
        event_row = self._conn.execute(
            "SELECT * FROM v1_events WHERE event_id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._event_from_row(event_row)

    def append_event(
        self,
        quest_id: str,
        event_type: str,
        patch: Mapping[str, Any],
        expected_state_version: int,
        event_schema_version: int = EVENT_SCHEMA_VERSION,
        **legacy_kwargs: Any,
    ) -> dict[str, Any]:
        if "schema_version" in legacy_kwargs:
            event_schema_version = int(legacy_kwargs.pop("schema_version"))
        if legacy_kwargs:
            raise TypeError(f"unexpected arguments: {sorted(legacy_kwargs)}")
        with self._transaction():
            return self._append_locked(
                quest_id,
                event_type,
                patch,
                expected_state_version=expected_state_version,
                event_schema_version=event_schema_version,
            )

    def append_events(
        self,
        quest_id: str,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        expected_state_version: int,
    ) -> list[dict[str, Any]]:
        """Append an ordered group of projection events in one transaction."""
        with self._transaction():
            result = []
            version = expected_state_version
            for event_type, patch in events:
                event = self._append_locked(
                    quest_id,
                    event_type,
                    patch,
                    expected_state_version=version,
                    event_schema_version=EVENT_SCHEMA_VERSION,
                )
                result.append(event)
                version += 1
            return result

    def get_artifact_review_receipt(self, quest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v1_artifact_reviews WHERE quest_id = ?", (quest_id,)
            ).fetchone()
        return dict(row) if row else None

    def finalize_artifact_discard(
        self, quest_id: str, *, expected_state_version: int
    ) -> dict[str, Any]:
        """Atomically mark the durable discard receipt and projection complete."""

        now = utc_now()
        with self._transaction():
            receipt = self._conn.execute(
                "SELECT * FROM v1_artifact_reviews WHERE quest_id = ?", (quest_id,)
            ).fetchone()
            if receipt is None or receipt["decision"] != "discard":
                raise ValueError("artifact discard decision was not recorded")
            if receipt["completed_at"] is not None:
                row = self._conn.execute(
                    "SELECT state_json FROM v1_quests WHERE quest_id = ?", (quest_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(quest_id)
                return json.loads(row["state_json"])
            event = self._append_locked(
                quest_id,
                "ArtifactDiscarded",
                {
                    "status": "failed",
                    "artifact_disposition": "discarded",
                    "finished_at": now,
                    "error": {
                        "code": "USER_DISCARDED_ARTIFACTS",
                        "message": "User discarded frozen artifacts",
                    },
                },
                expected_state_version=expected_state_version,
                event_schema_version=EVENT_SCHEMA_VERSION,
                now=now,
            )
            self._conn.execute(
                "UPDATE v1_artifact_reviews SET completed_at = ? WHERE quest_id = ?",
                (now, quest_id),
            )
            return event

    def begin_artifact_review(
        self,
        quest_id: str,
        *,
        review_id: str,
        manifest_hash: str,
        idempotency_key: str,
        decision: str,
        note: str | None,
        expected_state_version: int,
        event_type: str,
        patch: Mapping[str, Any],
        final_event: tuple[str, Mapping[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Atomically register a decision and its first immutable event."""
        now = utc_now()
        with self._transaction():
            receipt = self._conn.execute(
                "SELECT * FROM v1_artifact_reviews WHERE quest_id = ?", (quest_id,)
            ).fetchone()
            if receipt is not None:
                return dict(receipt), None
            self._conn.execute(
                """
                INSERT INTO v1_artifact_reviews(
                    quest_id, review_id, manifest_hash, idempotency_key,
                    decision, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quest_id,
                    review_id,
                    manifest_hash,
                    idempotency_key,
                    decision,
                    note,
                    now,
                ),
            )
            event = self._append_locked(
                quest_id,
                event_type,
                patch,
                expected_state_version=expected_state_version,
                event_schema_version=EVENT_SCHEMA_VERSION,
                now=now,
            )
            if final_event is not None:
                final_event_type, final_patch = final_event
                self._append_locked(
                    quest_id,
                    final_event_type,
                    final_patch,
                    expected_state_version=expected_state_version + 1,
                    event_schema_version=EVENT_SCHEMA_VERSION,
                    now=now,
                )
            return {
                "quest_id": quest_id,
                "review_id": review_id,
                "manifest_hash": manifest_hash,
                "idempotency_key": idempotency_key,
                "decision": decision,
                "note": note,
                "created_at": now,
            }, event

    def get_projection_row(self, quest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v1_quests WHERE quest_id = ?", (quest_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_quest(self, quest_id: str) -> dict[str, Any] | None:
        row = self.get_projection_row(quest_id)
        return json.loads(row["state_json"]) if row else None

    def require_quest(self, quest_id: str) -> dict[str, Any]:
        state = self.get_quest(quest_id)
        if state is None:
            raise KeyError(quest_id)
        return state

    def list_quests(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state_json FROM v1_quests ORDER BY created_at DESC, quest_id DESC"
            ).fetchall()
            return [json.loads(row["state_json"]) for row in rows]

    def search_quests(
        self,
        *,
        q: str | None,
        statuses: Sequence[str],
        offset: int,
        limit: int | None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        parameters: list[str] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(statuses)
        if q is not None:
            needle = q.casefold()
            clauses.append(
                "(instr(projecttown_casefold(quest_id), ?) > 0 OR "
                "instr(projecttown_casefold(COALESCE(json_extract(state_json, '$.contract.goal'), '')), ?) > 0)"
            )
            parameters.extend((needle, needle))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM v1_quests{where}", parameters
                ).fetchone()[0]
            )
            page_parameters: list[str | int] = [*parameters]
            page_query = f"SELECT state_json FROM v1_quests{where} ORDER BY created_at DESC, quest_id DESC"
            if limit is None:
                page_query += " LIMIT -1 OFFSET ?"
                page_parameters.append(offset)
            else:
                page_query += " LIMIT ? OFFSET ?"
                page_parameters.extend((limit, offset))
            states = [
                json.loads(row["state_json"])
                for row in self._conn.execute(page_query, page_parameters).fetchall()
            ]
        return states, total

    def failure_navigation_inputs(self, quest_id: str) -> dict[str, Any] | None:
        """Load bounded, read-only references for the public failure projection."""
        with self._lock:
            quest = self._conn.execute(
                "SELECT quest_id, state_json, status, state_version, updated_at FROM v1_quests WHERE quest_id=?",
                (quest_id,),
            ).fetchone()
            if quest is None:
                return None
            state = json.loads(quest["state_json"])
            event = self._conn.execute(
                """SELECT event_id, sequence, event_type FROM v1_events
                WHERE quest_id=? ORDER BY event_id DESC LIMIT 1""",
                (quest_id,),
            ).fetchone()
            action = self._conn.execute(
                """SELECT action_id, status, milestone_id FROM v1_tool_actions
                WHERE quest_id=? AND status IN ('failed', 'unknown_effect')
                ORDER BY updated_at DESC, action_id DESC LIMIT 1""",
                (quest_id,),
            ).fetchone()
            evidence = self._conn.execute(
                "SELECT id FROM v1_evidence WHERE quest_id=? ORDER BY created_at DESC, id DESC LIMIT 20",
                (quest_id,),
            ).fetchall()
            decision = self._conn.execute(
                "SELECT id FROM v1_decisions WHERE quest_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (quest_id,),
            ).fetchone()
            checkpoint = self._conn.execute(
                """SELECT state_version, state_json, state_hash FROM v1_checkpoints
                WHERE quest_id=? ORDER BY state_version DESC LIMIT 1""",
                (quest_id,),
            ).fetchone()
            review = self._conn.execute(
                "SELECT review_id FROM v1_artifact_reviews WHERE quest_id=?",
                (quest_id,),
            ).fetchone()
        checkpoint_summary: dict[str, Any] = {
            "present": checkpoint is not None,
            "valid": False,
        }
        if checkpoint is not None:
            try:
                checkpoint_summary.update(
                    {
                        "valid": stable_hash(json.loads(checkpoint["state_json"]))
                        == checkpoint["state_hash"],
                        "state_version": int(checkpoint["state_version"]),
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return {
            "quest_id": quest["quest_id"],
            "status": quest["status"],
            "state_version": int(quest["state_version"]),
            "updated_at": quest["updated_at"],
            "error_code": str((state.get("error") or {}).get("code") or ""),
            "milestone_id": state.get("current_milestone_id"),
            "event": dict(event) if event else None,
            "action": dict(action) if action else None,
            "evidence_ids": [str(row["id"]) for row in evidence],
            "decision_id": str(decision["id"]) if decision else None,
            "checkpoint": checkpoint_summary,
            "artifact_review_required": bool(state.get("artifact_review_required")),
            "artifact_review_pending": bool(state.get("pending_artifact_review"))
            or (
                quest["status"] == "waiting_user"
                and bool(state.get("artifact_review_required"))
            ),
            "review_id": str(review["review_id"]) if review else None,
        }

    @staticmethod
    def _model_summary(
        value: Mapping[str, Any],
        label: str,
        *,
        max_bytes: int = 8_192,
        allow_public_counter_keys: bool = True,
    ) -> dict[str, Any]:
        """Persist only bounded public summaries, never credentials or raw traces."""
        forbidden = (
            "api_key",
            "apikey",
            "authorization",
            "password",
            "secret",
            "token",
            "credential",
        )
        public_counter_keys = {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reserved_tokens",
            "settled_tokens",
            "max_tokens",
        }

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    normalized = str(key).casefold().replace("-", "_")
                    if (
                        not allow_public_counter_keys
                        or normalized not in public_counter_keys
                    ) and any(part in normalized for part in forbidden):
                        raise ValueError(f"{label} must not contain secrets")
                    visit(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)

        result = copy.deepcopy(dict(value))
        visit(result)
        if len(_json(result).encode("utf-8")) > max_bytes:
            raise ValueError(f"{label} is too large")
        return result

    @staticmethod
    def _require_model_hash(value: str, label: str) -> str:
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError(f"{label} must be a lowercase SHA-256 hash")
        return value

    def _model_binding_locked(self, quest_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT state_json, state_version FROM v1_quests WHERE quest_id = ?",
            (quest_id,),
        ).fetchone()
        if row is None:
            raise KeyError(quest_id)
        state = json.loads(row["state_json"])
        contract = dict(state.get("contract") or {})
        plan_id = str(state.get("plan_id") or "")
        plan_version = int(state.get("plan_version") or 0)
        plan_row = self._conn.execute(
            "SELECT plan_json FROM v1_plan_versions WHERE plan_id = ? AND version = ?",
            (plan_id, plan_version),
        ).fetchone()
        if not contract or not plan_id or plan_version <= 0 or plan_row is None:
            raise ValueError("quest has no current contract/plan binding")
        budget = dict(contract.get("budget") or {})
        return {
            "quest_id": quest_id,
            "state_version": int(row["state_version"]),
            "contract_id": str(contract.get("id") or f"contract_{quest_id}"),
            "contract_version": int(contract.get("version") or 1),
            "contract_hash": stable_hash(contract),
            "plan_id": plan_id,
            "plan_version": plan_version,
            "plan_hash": stable_hash(json.loads(plan_row["plan_json"])),
            "max_tokens": int(budget.get("max_tokens", 20_000)),
        }

    def _model_usage_locked(self, quest_id: str) -> dict[str, int]:
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(a.settled_tokens), 0) AS settled,
                   COALESCE(SUM(CASE WHEN a.status IN ('prepared', 'dispatched', 'unknown_outcome')
                                     THEN a.reserved_tokens ELSE 0 END), 0) AS held
            FROM v1_model_attempts a JOIN v1_model_calls c ON c.call_id = a.call_id
            WHERE c.quest_id = ?
            """,
            (quest_id,),
        ).fetchone()
        return {"settled_tokens": int(row["settled"]), "held_tokens": int(row["held"])}

    @staticmethod
    def phase1c_cost_reservation(
        estimated_input_tokens: int,
        max_output_tokens: int,
        *,
        provider: str = PHASE1C_PROVIDER,
        model: str = PHASE1C_MODEL_SNAPSHOT,
    ) -> dict[str, int | str]:
        """Build an immutable authorized quote (legacy name retained for OpenAI)."""
        profile = _COST_PROFILES.get((provider, model))
        if profile is None:
            raise ValueError("model cost quote is not authorized for provider/model")
        if (
            isinstance(estimated_input_tokens, bool)
            or isinstance(max_output_tokens, bool)
            or not isinstance(estimated_input_tokens, int)
            or not isinstance(max_output_tokens, int)
            or estimated_input_tokens < 0
            or max_output_tokens < 0
        ):
            raise ValueError("cost token estimates must be non-negative integers")
        return {
            "account_id": profile["account_id"],
            "provider": profile["provider"],
            "model_alias": profile["model_alias"],
            "model_snapshot": profile["model_snapshot"],
            "pricing_version": profile["pricing_version"],
            "fx_micro_cny_per_usd": profile["fx_micro_cny_per_usd"],
            "estimated_input_tokens": estimated_input_tokens,
            "max_output_tokens": max_output_tokens,
            "reserved_micro_cny": (
                estimated_input_tokens * int(profile["input_rate"])
                + max_output_tokens * int(profile["reserve_output_rate"])
            ),
        }

    @staticmethod
    def _validated_cost_reservation(
        value: Mapping[str, Any],
    ) -> dict[str, int | str]:
        if not isinstance(value, Mapping):
            raise TypeError("cost reservation must be a mapping")
        required = {
            "account_id",
            "provider",
            "model_alias",
            "model_snapshot",
            "pricing_version",
            "fx_micro_cny_per_usd",
            "estimated_input_tokens",
            "max_output_tokens",
            "reserved_micro_cny",
        }
        if set(value) != required:
            raise ValueError("cost reservation fields are not authorized")
        expected = V1Storage.phase1c_cost_reservation(
            value["estimated_input_tokens"],
            value["max_output_tokens"],
            provider=value["provider"],
            model=value["model_snapshot"],
        )
        if any(value[key] != expected[key] for key in required):
            raise ValueError("cost reservation is not an authorized Phase 1C quote")
        profile = _COST_PROFILES[
            (str(expected["provider"]), str(expected["model_snapshot"]))
        ]
        if int(expected["reserved_micro_cny"]) > int(profile["max_call"]):
            raise ValueError("model cost per-call budget exceeded")
        return expected

    def _ensure_cost_account_locked(self, quote: Mapping[str, Any], now: str) -> None:
        profile = _COST_PROFILES[(str(quote["provider"]), str(quote["model_snapshot"]))]
        self._conn.execute(
            """INSERT INTO v1_model_cost_accounts(
                account_id, provider, model_alias, model_snapshot, pricing_version,
                fx_micro_cny_per_usd, max_call_micro_cny, max_total_micro_cny,
                breached, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(account_id) DO NOTHING""",
            (
                profile["account_id"],
                profile["provider"],
                profile["model_alias"],
                profile["model_snapshot"],
                profile["pricing_version"],
                profile["fx_micro_cny_per_usd"],
                profile["max_call"],
                profile["max_total"],
                now,
                now,
            ),
        )
        account = self._conn.execute(
            "SELECT * FROM v1_model_cost_accounts WHERE account_id = ?",
            (profile["account_id"],),
        ).fetchone()
        if account is None or any(
            account[key] != value
            for key, value in {
                "provider": profile["provider"],
                "model_alias": profile["model_alias"],
                "model_snapshot": profile["model_snapshot"],
                "pricing_version": profile["pricing_version"],
                "fx_micro_cny_per_usd": profile["fx_micro_cny_per_usd"],
                "max_call_micro_cny": profile["max_call"],
                "max_total_micro_cny": profile["max_total"],
            }.items()
        ):
            raise RuntimeError("model cost account binding mismatch")

    def _cost_usage_locked(self, account_id: str) -> dict[str, int]:
        row = self._conn.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN status = 'settled' THEN settled_micro_cny ELSE 0 END), 0) AS settled,
                COALESCE(SUM(CASE WHEN status IN ('held', 'unknown') THEN reserved_micro_cny ELSE 0 END), 0) AS held
            FROM v1_model_cost_reservations WHERE account_id = ?""",
            (account_id,),
        ).fetchone()
        return {
            "settled_micro_cny": int(row["settled"]),
            "held_micro_cny": int(row["held"]),
        }

    def _reserve_cost_locked(
        self, attempt_id: str, reservation: Mapping[str, Any], now: str
    ) -> None:
        quote = self._validated_cost_reservation(reservation)
        self._ensure_cost_account_locked(quote, now)
        profile = _COST_PROFILES[(str(quote["provider"]), str(quote["model_snapshot"]))]
        existing = self._conn.execute(
            "SELECT * FROM v1_model_cost_reservations WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if existing is not None:
            if any(existing[key] != quote[key] for key in quote):
                raise ValueError("model cost reservation idempotency conflict")
            return
        account = self._conn.execute(
            "SELECT breached FROM v1_model_cost_accounts WHERE account_id = ?",
            (quote["account_id"],),
        ).fetchone()
        if account is None or int(account["breached"]):
            raise ValueError("model cost account is breached")
        usage = self._cost_usage_locked(str(quote["account_id"]))
        if usage["settled_micro_cny"] + usage["held_micro_cny"] + int(
            quote["reserved_micro_cny"]
        ) > int(profile["max_total"]):
            raise ValueError("model cost total budget exceeded")
        self._conn.execute(
            """INSERT INTO v1_model_cost_reservations(
                attempt_id, account_id, provider, model_alias, model_snapshot,
                pricing_version, fx_micro_cny_per_usd, estimated_input_tokens,
                max_output_tokens, reserved_micro_cny, settled_micro_cny, status,
                created_at, settled_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'held', ?, NULL, ?)""",
            (
                attempt_id,
                quote["account_id"],
                quote["provider"],
                quote["model_alias"],
                quote["model_snapshot"],
                quote["pricing_version"],
                quote["fx_micro_cny_per_usd"],
                quote["estimated_input_tokens"],
                quote["max_output_tokens"],
                quote["reserved_micro_cny"],
                now,
                now,
            ),
        )

    def _settle_cost_locked(
        self, attempt_id: str, input_tokens: int, output_tokens: int, now: str
    ) -> None:
        reservation = self._conn.execute(
            "SELECT * FROM v1_model_cost_reservations WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if reservation is None:
            return
        if reservation["status"] == "settled":
            return
        if reservation["status"] not in {"held", "unknown"}:
            raise ValueError("released model cost reservation cannot settle")
        profile = _COST_PROFILES.get(
            (str(reservation["provider"]), str(reservation["model_snapshot"]))
        )
        if profile is None:
            raise RuntimeError("model cost reservation profile is unknown")
        actual = input_tokens * int(profile["input_rate"]) + output_tokens * int(
            profile["output_rate"]
        )
        self._conn.execute(
            """UPDATE v1_model_cost_reservations
            SET status='settled', settled_micro_cny=?, settled_at=?, updated_at=?
            WHERE attempt_id=?""",
            (actual, now, now, attempt_id),
        )
        if actual > int(reservation["reserved_micro_cny"]):
            self._conn.execute(
                """UPDATE v1_model_cost_accounts SET breached=1, updated_at=?
                WHERE account_id=?""",
                (now, reservation["account_id"]),
            )

    def _release_cost_locked(self, attempt_id: str, now: str) -> None:
        self._conn.execute(
            """UPDATE v1_model_cost_reservations
            SET status='released', settled_micro_cny=0, settled_at=?, updated_at=?
            WHERE attempt_id=? AND status='held'""",
            (now, now, attempt_id),
        )

    def model_cost_usage(
        self, *, provider: str = PHASE1C_PROVIDER, model: str = PHASE1C_MODEL_SNAPSHOT
    ) -> dict[str, int | bool | str]:
        """Return one immutable account's aggregates, never provider payloads."""
        profile = _COST_PROFILES.get((provider, model))
        if profile is None:
            raise ValueError("model cost profile is not authorized")
        with self._lock:
            account = self._conn.execute(
                "SELECT * FROM v1_model_cost_accounts WHERE account_id = ?",
                (profile["account_id"],),
            ).fetchone()
            if account is None:
                return {
                    "account_id": profile["account_id"],
                    "settled_micro_cny": 0,
                    "held_micro_cny": 0,
                    "max_total_micro_cny": profile["max_total"],
                    "available_micro_cny": profile["max_total"],
                    "breached": False,
                }
            usage = self._cost_usage_locked(str(profile["account_id"]))
            return {
                "account_id": profile["account_id"],
                **usage,
                "max_total_micro_cny": int(account["max_total_micro_cny"]),
                "available_micro_cny": int(account["max_total_micro_cny"])
                - usage["settled_micro_cny"]
                - usage["held_micro_cny"],
                "breached": bool(account["breached"]),
            }

    @staticmethod
    def _model_call_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _model_attempt_from_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field in (
            "parameters_json",
            "candidate_json",
            "usage_json",
            "cost_json",
            "error_json",
        ):
            key = field.removesuffix("_json")
            result[key] = json.loads(row[field]) if row[field] else None
            result.pop(field, None)
        return result

    def prepare_model_call(
        self,
        call_id: str,
        quest_id: str,
        purpose: str,
        idempotency_key: str,
        input_hash: str,
        prompt_version: str,
        request_schema_version: int,
        candidate_schema_version: int,
        adapter: str,
        model: str,
        parameters: Mapping[str, Any],
        reserved_tokens: int,
        *,
        expected_state_version: int | None = None,
        dispatch_token: str | None = None,
        cost_reservation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if reserved_tokens < 0 or not all(
            (
                call_id,
                purpose,
                idempotency_key,
                input_hash,
                prompt_version,
                adapter,
                model,
            )
        ):
            raise ValueError("model call identifiers and reservation must be valid")
        self._require_model_hash(input_hash, "input hash")
        clean_parameters = self._model_summary(parameters, "model parameters")
        clean_cost_reservation = (
            self._validated_cost_reservation(cost_reservation)
            if cost_reservation is not None
            else None
        )
        with self._transaction():
            binding = self._model_binding_locked(quest_id)
            if (
                expected_state_version is not None
                and binding["state_version"] != expected_state_version
            ):
                raise ValueError("state version conflict")
            existing = self._conn.execute(
                "SELECT * FROM v1_model_calls WHERE quest_id = ? AND idempotency_key = ?",
                (quest_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                token = dispatch_token or f"dispatch_{call_id}_1"
                expected_call = {
                    "call_id": call_id,
                    "quest_id": quest_id,
                    "purpose": purpose,
                    "idempotency_key": idempotency_key,
                    "request_schema_version": request_schema_version,
                    "candidate_schema_version": candidate_schema_version,
                    "input_hash": input_hash,
                    "prompt_version": prompt_version,
                    "contract_id": binding["contract_id"],
                    "contract_version": binding["contract_version"],
                    "contract_hash": binding["contract_hash"],
                    "plan_id": binding["plan_id"],
                    "plan_version": binding["plan_version"],
                    "plan_hash": binding["plan_hash"],
                    "expected_state_version": binding["state_version"],
                    "max_tokens": binding["max_tokens"],
                }
                attempt = self._conn.execute(
                    "SELECT * FROM v1_model_attempts WHERE call_id = ? AND attempt_no = 1",
                    (existing["call_id"],),
                ).fetchone()
                if (
                    any(existing[key] != value for key, value in expected_call.items())
                    or attempt is None
                    or attempt["adapter"] != adapter
                    or attempt["model"] != model
                    or attempt["parameters_json"] != _json(clean_parameters)
                    or int(attempt["reserved_tokens"]) != reserved_tokens
                    or attempt["dispatch_token"] != token
                ):
                    raise ValueError("model call idempotency conflict")
                reservation = self._conn.execute(
                    "SELECT * FROM v1_model_cost_reservations WHERE attempt_id = ?",
                    (attempt["attempt_id"],),
                ).fetchone()
                if (reservation is None) != (clean_cost_reservation is None):
                    raise ValueError("model cost reservation idempotency conflict")
                if reservation is not None and any(
                    reservation[key] != value
                    for key, value in clean_cost_reservation.items()
                ):
                    raise ValueError("model cost reservation idempotency conflict")
                return {
                    "call": self._model_call_from_row(existing),
                    "attempts": [self._model_attempt_from_row(attempt)],
                }
            usage = self._model_usage_locked(quest_id)
            if (
                usage["settled_tokens"] + usage["held_tokens"] + reserved_tokens
                > binding["max_tokens"]
            ):
                raise ValueError("model token budget exceeded")
            now = utc_now()
            token = dispatch_token or f"dispatch_{call_id}_1"
            self._conn.execute(
                """INSERT INTO v1_model_calls(call_id, quest_id, purpose, idempotency_key,
                   request_schema_version, candidate_schema_version, input_hash, prompt_version,
                   contract_id, contract_version, contract_hash, plan_id, plan_version, plan_hash,
                   expected_state_version, max_tokens, status, winning_attempt_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', NULL, ?, ?)""",
                (
                    call_id,
                    quest_id,
                    purpose,
                    idempotency_key,
                    request_schema_version,
                    candidate_schema_version,
                    input_hash,
                    prompt_version,
                    binding["contract_id"],
                    binding["contract_version"],
                    binding["contract_hash"],
                    binding["plan_id"],
                    binding["plan_version"],
                    binding["plan_hash"],
                    binding["state_version"],
                    binding["max_tokens"],
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """INSERT INTO v1_model_attempts(attempt_id, call_id, attempt_no, dispatch_token,
                   adapter, model, parameters_json, status, validation_status, reserved_tokens,
                   created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?, 'prepared', 'pending', ?, ?, ?)""",
                (
                    f"{call_id}:1",
                    call_id,
                    token,
                    adapter,
                    model,
                    _json(clean_parameters),
                    reserved_tokens,
                    now,
                    now,
                ),
            )
            if clean_cost_reservation is not None:
                self._reserve_cost_locked(f"{call_id}:1", clean_cost_reservation, now)
            return self.get_model_call(call_id) or {}

    def mark_model_attempt_dispatched(self, attempt_id: str) -> dict[str, Any]:
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM v1_model_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row["status"] == "dispatched":
                return self._model_attempt_from_row(row)
            if row["status"] != "prepared":
                raise ValueError("model attempt is not prepared")
            now = utc_now()
            self._conn.execute(
                "UPDATE v1_model_attempts SET status='dispatched', dispatched_at=?, updated_at=? WHERE attempt_id=?",
                (now, now, attempt_id),
            )
            self._conn.execute(
                "UPDATE v1_model_calls SET status='dispatched', updated_at=? WHERE call_id=?",
                (now, row["call_id"]),
            )
            updated = self._conn.execute(
                "SELECT * FROM v1_model_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return self._model_attempt_from_row(updated)

    def _settle_model_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        validation_status: str,
        response_hash: str | None,
        candidate: Mapping[str, Any] | None,
        input_tokens: int,
        output_tokens: int,
        usage: Mapping[str, Any] | None,
        cost: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
        validate_current: bool,
        callback_quest_id: str | None = None,
        callback_input_hash: str | None = None,
        retain_candidate: bool = True,
    ) -> dict[str, Any]:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("model token usage must be non-negative")
        clean_candidate = (
            self._model_summary(
                candidate,
                "model candidate",
                max_bytes=262_144,
                allow_public_counter_keys=False,
            )
            if candidate is not None
            else None
        )
        if not isinstance(retain_candidate, bool):
            raise TypeError("retain_candidate must be a boolean")
        candidate_json = (
            _json(clean_candidate)
            if clean_candidate is not None and retain_candidate
            else None
        )
        if candidate_json is not None and len(candidate_json.encode("utf-8")) > 262_144:
            raise ValueError("model candidate is too large")
        if clean_candidate is not None:
            self._require_model_hash(stable_hash(clean_candidate), "candidate hash")
        clean_usage = (
            self._model_summary(usage, "model usage") if usage is not None else None
        )
        clean_cost = (
            self._model_summary(cost, "model cost") if cost is not None else None
        )
        clean_error = (
            self._model_summary(error, "model error") if error is not None else None
        )
        with self._transaction():
            row = self._conn.execute(
                "SELECT a.*, c.quest_id FROM v1_model_attempts a JOIN v1_model_calls c ON c.call_id=a.call_id WHERE a.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if callback_quest_id is not None and row["quest_id"] != callback_quest_id:
                raise ValueError("model callback quest binding conflict")
            call = self._conn.execute(
                "SELECT * FROM v1_model_calls WHERE call_id=?", (row["call_id"],)
            ).fetchone()
            if (
                callback_input_hash is not None
                and call["input_hash"] != callback_input_hash
            ):
                raise ValueError("model callback input binding conflict")
            if row["response_hash"] is not None:
                if response_hash == row["response_hash"]:
                    return self._model_attempt_from_row(row)
                raise ValueError("model callback response conflict")
            allowed_statuses = (
                {"dispatched"} if validate_current else {"prepared", "dispatched"}
            )
            if row["status"] not in allowed_statuses:
                raise ValueError("model attempt is already final")
            final_validation = validation_status
            if validate_current:
                binding = self._model_binding_locked(row["quest_id"])
                current = binding["state_version"] == call[
                    "expected_state_version"
                ] and all(
                    binding[key] == call[key]
                    for key in (
                        "contract_id",
                        "contract_version",
                        "contract_hash",
                        "plan_id",
                        "plan_version",
                        "plan_hash",
                    )
                )
                if current:
                    prior = self._conn.execute(
                        "SELECT attempt_id FROM v1_model_attempts WHERE call_id=? AND validation_status='validated_current'",
                        (row["call_id"],),
                    ).fetchone()
                    final_validation = (
                        "validated_current" if prior is None else "conflict"
                    )
                else:
                    final_validation = "stale"
            settled = input_tokens + output_tokens
            now = utc_now()
            self._conn.execute(
                """UPDATE v1_model_attempts SET status=?, validation_status=?, settled_tokens=?, input_tokens=?, output_tokens=?,
                   candidate_json=?, candidate_hash=?, response_hash=?, usage_json=?, cost_json=?, error_json=?, settled_at=?, updated_at=?
                   WHERE attempt_id=?""",
                (
                    status,
                    final_validation,
                    settled,
                    input_tokens,
                    output_tokens,
                    candidate_json,
                    stable_hash(clean_candidate)
                    if clean_candidate is not None
                    else None,
                    response_hash,
                    _json(clean_usage) if clean_usage is not None else None,
                    _json(clean_cost) if clean_cost is not None else None,
                    _json(clean_error) if clean_error is not None else None,
                    now,
                    now,
                    attempt_id,
                ),
            )
            self._settle_cost_locked(attempt_id, input_tokens, output_tokens, now)
            if final_validation == "validated_current":
                self._conn.execute(
                    "UPDATE v1_model_calls SET status=?, winning_attempt_id=?, updated_at=? WHERE call_id=? AND winning_attempt_id IS NULL",
                    (status, attempt_id, now, row["call_id"]),
                )
            else:
                self._conn.execute(
                    "UPDATE v1_model_calls SET status=?, updated_at=? WHERE call_id=?",
                    (status, now, row["call_id"]),
                )
            updated = self._conn.execute(
                "SELECT * FROM v1_model_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return self._model_attempt_from_row(updated)

    def record_model_success(
        self,
        attempt_id: str,
        quest_id: str,
        input_hash: str,
        response_hash: str,
        candidate: Mapping[str, Any],
        *,
        input_tokens: int,
        output_tokens: int,
        usage: Mapping[str, Any] | None = None,
        cost: Mapping[str, Any] | None = None,
        retain_candidate: bool = True,
    ) -> dict[str, Any]:
        self._require_model_hash(input_hash, "input hash")
        self._require_model_hash(response_hash, "response hash")
        return self._settle_model_attempt(
            attempt_id,
            status="succeeded",
            validation_status="pending",
            response_hash=response_hash,
            candidate=candidate,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage=usage,
            cost=cost,
            error=None,
            validate_current=True,
            callback_quest_id=quest_id,
            callback_input_hash=input_hash,
            retain_candidate=retain_candidate,
        )

    def record_model_failure(
        self,
        attempt_id: str,
        error: Mapping[str, Any],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        usage: Mapping[str, Any] | None = None,
        cost: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._settle_model_attempt(
            attempt_id,
            status="failed",
            validation_status="invalid",
            response_hash=None,
            candidate=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage=usage,
            cost=cost,
            error=error,
            validate_current=False,
        )

    def mark_model_attempt_unknown(
        self, attempt_id: str, error: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM v1_model_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row["status"] == "unknown_outcome":
                return self._model_attempt_from_row(row)
            if row["status"] != "dispatched":
                raise ValueError("only dispatched model attempts may become unknown")
            now = utc_now()
            clean_error = self._model_summary(error, "model error") if error else None
            self._conn.execute(
                "UPDATE v1_model_attempts SET status='unknown_outcome', error_json=?, updated_at=? WHERE attempt_id=?",
                (_json(clean_error) if clean_error else None, now, attempt_id),
            )
            self._conn.execute(
                "UPDATE v1_model_cost_reservations SET status='unknown', updated_at=? WHERE attempt_id=? AND status='held'",
                (now, attempt_id),
            )
            self._conn.execute(
                "UPDATE v1_model_calls SET status='unknown_outcome', updated_at=? WHERE call_id=?",
                (now, row["call_id"]),
            )
            return self._model_attempt_from_row(
                self._conn.execute(
                    "SELECT * FROM v1_model_attempts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
            )

    def retry_model_call(
        self,
        call_id: str,
        attempt_id: str,
        dispatch_token: str,
        adapter: str,
        model: str,
        parameters: Mapping[str, Any],
        reserved_tokens: int,
        *,
        cost_reservation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if reserved_tokens < 0 or not all((attempt_id, dispatch_token, adapter, model)):
            raise ValueError("retry identifiers and reservation must be valid")
        clean_parameters = self._model_summary(parameters, "model parameters")
        clean_cost_reservation = (
            self._validated_cost_reservation(cost_reservation)
            if cost_reservation is not None
            else None
        )
        with self._transaction():
            call = self._conn.execute(
                "SELECT * FROM v1_model_calls WHERE call_id=?", (call_id,)
            ).fetchone()
            if call is None:
                raise KeyError(call_id)
            if call["winning_attempt_id"] is not None or call["status"] not in {
                "failed",
                "unknown_outcome",
            }:
                raise ValueError("terminal model call cannot be retried")
            binding = self._model_binding_locked(call["quest_id"])
            if binding["state_version"] != call["expected_state_version"] or any(
                binding[key] != call[key]
                for key in (
                    "contract_id",
                    "contract_version",
                    "contract_hash",
                    "plan_id",
                    "plan_version",
                    "plan_hash",
                )
            ):
                raise ValueError("stale model call cannot be retried")
            usage = self._model_usage_locked(call["quest_id"])
            if (
                usage["settled_tokens"] + usage["held_tokens"] + reserved_tokens
                > call["max_tokens"]
            ):
                raise ValueError("model token budget exceeded")
            number = (
                int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(attempt_no), 0) FROM v1_model_attempts WHERE call_id=?",
                        (call_id,),
                    ).fetchone()[0]
                )
                + 1
            )
            if number > MODEL_MAX_ATTEMPTS:
                raise ValueError("model retry limit exceeded")
            now = utc_now()
            self._conn.execute(
                "INSERT INTO v1_model_attempts(attempt_id, call_id, attempt_no, dispatch_token, adapter, model, parameters_json, status, validation_status, reserved_tokens, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', 'pending', ?, ?, ?)",
                (
                    attempt_id,
                    call_id,
                    number,
                    dispatch_token,
                    adapter,
                    model,
                    _json(clean_parameters),
                    reserved_tokens,
                    now,
                    now,
                ),
            )
            if clean_cost_reservation is not None:
                self._reserve_cost_locked(attempt_id, clean_cost_reservation, now)
            self._conn.execute(
                "UPDATE v1_model_calls SET status='prepared', updated_at=? WHERE call_id=?",
                (now, call_id),
            )
            return self._model_attempt_from_row(
                self._conn.execute(
                    "SELECT * FROM v1_model_attempts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
            )

    def get_model_call(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            call = self._conn.execute(
                "SELECT * FROM v1_model_calls WHERE call_id=?", (call_id,)
            ).fetchone()
            if call is None:
                return None
            attempts = self._conn.execute(
                "SELECT * FROM v1_model_attempts WHERE call_id=? ORDER BY attempt_no",
                (call_id,),
            ).fetchall()
            return {
                "call": self._model_call_from_row(call),
                "attempts": [self._model_attempt_from_row(row) for row in attempts],
            }

    def list_model_calls(self, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            ids = self._conn.execute(
                "SELECT call_id FROM v1_model_calls WHERE quest_id=? ORDER BY created_at, call_id",
                (quest_id,),
            ).fetchall()
        return [self.get_model_call(row["call_id"]) for row in ids]

    def model_token_usage(self, quest_id: str) -> dict[str, int]:
        with self._lock:
            result = self._model_usage_locked(quest_id)
            binding = self._model_binding_locked(quest_id)
            return {
                **result,
                "max_tokens": binding["max_tokens"],
                "available_tokens": binding["max_tokens"]
                - result["settled_tokens"]
                - result["held_tokens"],
            }

    def recover_model_calls(self) -> dict[str, int]:
        with self._transaction():
            now = utc_now()
            self._conn.execute(
                """UPDATE v1_model_cost_reservations SET status='released',
                    settled_micro_cny=0, settled_at=?, updated_at=?
                WHERE status='held' AND attempt_id IN (
                    SELECT attempt_id FROM v1_model_attempts WHERE status='prepared'
                )""",
                (now, now),
            )
            prepared = self._conn.execute(
                "UPDATE v1_model_attempts SET status='failed', validation_status='cancelled_before_dispatch', settled_tokens=0, error_json=?, settled_at=?, updated_at=? WHERE status='prepared'",
                (
                    _json(
                        {
                            "code": "PROCESS_INTERRUPTED",
                            "message": "Prepared model call was never dispatched",
                        }
                    ),
                    now,
                    now,
                ),
            ).rowcount
            self._conn.execute(
                """UPDATE v1_model_cost_reservations SET status='unknown', updated_at=?
                WHERE status='held' AND attempt_id IN (
                    SELECT attempt_id FROM v1_model_attempts WHERE status='dispatched'
                )""",
                (now,),
            )
            dispatched = self._conn.execute(
                "UPDATE v1_model_attempts SET status='unknown_outcome', error_json=?, updated_at=? WHERE status='dispatched'",
                (
                    _json(
                        {
                            "code": "PROCESS_INTERRUPTED",
                            "message": "Dispatch outcome requires reconciliation",
                        }
                    ),
                    now,
                ),
            ).rowcount
            self._conn.execute(
                "UPDATE v1_model_calls SET status='failed', updated_at=? WHERE winning_attempt_id IS NULL AND call_id IN (SELECT DISTINCT call_id FROM v1_model_attempts WHERE validation_status='cancelled_before_dispatch')",
                (now,),
            )
            self._conn.execute(
                "UPDATE v1_model_calls SET status='unknown_outcome', updated_at=? WHERE winning_attempt_id IS NULL AND call_id IN (SELECT DISTINCT call_id FROM v1_model_attempts WHERE status='unknown_outcome')",
                (now,),
            )
            return {
                "cancelled_prepared": int(prepared),
                "unknown_dispatched": int(dispatched),
            }

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["event_id"]),
            "quest_id": row["quest_id"],
            "sequence": int(row["sequence"]),
            "event_type": row["event_type"],
            "event_schema_version": int(row["event_schema_version"]),
            "state_version_before": int(row["state_version_before"]),
            "state_version_after": int(row["state_version_after"]),
            "payload": json.loads(row["payload_json"]),
            "state_hash": row["state_hash"],
            "created_at": row["created_at"],
        }

    def list_events(
        self, quest_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM v1_events
                WHERE quest_id = ? AND sequence > ? ORDER BY sequence
                """,
                (quest_id, after_sequence),
            ).fetchall()
            return [self._event_from_row(row) for row in rows]

    def replay(self, quest_id: str, from_checkpoint: bool = False) -> dict[str, Any]:
        initial: dict[str, Any] = {}
        start_sequence = 0
        if from_checkpoint:
            with self._lock:
                checkpoint = self._conn.execute(
                    """
                    SELECT * FROM v1_checkpoints WHERE quest_id = ?
                    ORDER BY state_version DESC LIMIT 1
                    """,
                    (quest_id,),
                ).fetchone()
            if checkpoint is not None:
                initial = json.loads(checkpoint["state_json"])
                start_sequence = int(checkpoint["state_version"])

        state = initial
        for event in self.list_events(quest_id, start_sequence):
            if event["event_schema_version"] != EVENT_SCHEMA_VERSION:
                raise ValueError("unknown event schema version")
            state = EventReducer().apply(
                state, Event(event["event_type"], event["payload"])
            )
            if stable_hash(state) != event["state_hash"]:
                raise ValueError("event replay checksum mismatch")
        return state

    def save_checkpoint(self, quest_id: str) -> dict[str, Any]:
        row = self.get_projection_row(quest_id)
        if row is None:
            raise KeyError(quest_id)
        state = json.loads(row["state_json"])
        state_hash = stable_hash(state)
        with self._transaction():
            existing = self._conn.execute(
                """
                SELECT state_hash FROM v1_checkpoints
                WHERE quest_id = ? AND state_version = ?
                """,
                (quest_id, row["state_version"]),
            ).fetchone()
            if existing is not None and existing["state_hash"] != state_hash:
                raise ValueError("checkpoint conflict")
            self._conn.execute(
                """
                INSERT OR IGNORE INTO v1_checkpoints(
                    quest_id, state_version, state_json, state_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    quest_id,
                    row["state_version"],
                    row["state_json"],
                    state_hash,
                    utc_now(),
                ),
            )
        return {
            "quest_id": quest_id,
            "state_version": int(row["state_version"]),
            "state_hash": state_hash,
        }

    def validate_checkpoint(
        self, quest_id: str, state_version: int | None = None
    ) -> bool:
        with self._lock:
            if state_version is None:
                checkpoint = self._conn.execute(
                    """
                    SELECT * FROM v1_checkpoints WHERE quest_id = ?
                    ORDER BY state_version DESC LIMIT 1
                    """,
                    (quest_id,),
                ).fetchone()
            else:
                checkpoint = self._conn.execute(
                    """
                    SELECT * FROM v1_checkpoints
                    WHERE quest_id = ? AND state_version = ?
                    """,
                    (quest_id, state_version),
                ).fetchone()
        if checkpoint is None:
            return False
        checkpoint_state = json.loads(checkpoint["state_json"])
        if stable_hash(checkpoint_state) != checkpoint["state_hash"]:
            return False
        try:
            full = self.replay(quest_id, from_checkpoint=False)
            suffix = self.replay(quest_id, from_checkpoint=True)
        except ValueError:
            return False
        current = self.get_quest(quest_id)
        return current is not None and stable_hash(full) == stable_hash(
            suffix
        ) == stable_hash(current)

    def has_checkpoint(self, quest_id: str) -> bool:
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM v1_checkpoints WHERE quest_id = ? LIMIT 1",
                    (quest_id,),
                ).fetchone()
                is not None
            )

    def store_contract_version(
        self,
        quest_id: str,
        contract: Mapping[str, Any],
        *,
        expected_state_version: int,
        event_type: str = "GoalContractConfirmed",
        status: str = "planned",
    ) -> dict[str, Any]:
        with self._transaction():
            row = self._conn.execute(
                "SELECT state_version FROM v1_quests WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()
            if row is None or int(row["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            self._insert_contract_locked(quest_id, contract, utc_now())
            return self._append_locked(
                quest_id,
                event_type,
                {
                    "contract": dict(contract),
                    "goal": str(contract.get("goal", "")),
                    "status": status,
                    "error": None,
                },
                expected_state_version=expected_state_version,
                event_schema_version=EVENT_SCHEMA_VERSION,
            )

    def store_plan_version(
        self,
        quest_id: str,
        plan: Mapping[str, Any],
        *,
        expected_state_version: int,
    ) -> dict[str, Any]:
        milestones = self._validate_plan(plan)
        current = self.require_quest(quest_id)
        completed = {
            item["id"]: item
            for item in current.get("milestones", [])
            if item.get("status") == "completed"
        }
        proposed = {str(item["id"]): item for item in milestones}
        for milestone_id, old in completed.items():
            new = proposed.get(milestone_id)
            if new is None:
                raise ValueError("replan cannot remove a completed milestone")
            for field in ("tool_name", "tool_args", "acceptance_criteria"):
                if old.get(field) != new.get(field):
                    raise ValueError("replan cannot modify a completed milestone")
            new["status"] = "completed"
            new["evidence_ids"] = list(old.get("evidence_ids", []))
        with self._transaction():
            row = self._conn.execute(
                "SELECT state_version FROM v1_quests WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()
            if row is None or int(row["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            self._insert_plan_locked(quest_id, plan, milestones, utc_now())
            return self._append_locked(
                quest_id,
                "PlanReplanned",
                {
                    "plan_id": str(plan.get("id", current.get("plan_id", ""))),
                    "plan_version": int(plan.get("version", 1)),
                    "plan_metadata": copy.deepcopy(dict(plan.get("metadata", {}))),
                    "milestones": milestones,
                    "status": "running",
                },
                expected_state_version=expected_state_version,
                event_schema_version=EVENT_SCHEMA_VERSION,
            )

    def _record(
        self,
        table: str,
        identifier: str,
        quest_id: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._transaction():
            encoded = _json(data)
            try:
                self._conn.execute(
                    f"INSERT INTO {table}(id, quest_id, data_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (identifier, quest_id, encoded, utc_now()),
                )
            except sqlite3.IntegrityError:
                existing = self._conn.execute(
                    f"SELECT quest_id, data_json FROM {table} WHERE id = ?",
                    (identifier,),
                ).fetchone()
                if (
                    existing is None
                    or existing["quest_id"] != quest_id
                    or existing["data_json"] != encoded
                ):
                    raise ValueError(f"{table} record conflict") from None
        return {"id": identifier, "quest_id": quest_id, "data": dict(data)}

    def _list_records(self, table: str, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, data_json, created_at FROM {table} "
                "WHERE quest_id = ? ORDER BY created_at, id",
                (quest_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "quest_id": quest_id,
                **json.loads(row["data_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def append_evidence(
        self, quest_id: str, evidence_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._record("v1_evidence", evidence_id, quest_id, data)

    def list_evidence(self, quest_id: str) -> list[dict[str, Any]]:
        return self._list_records("v1_evidence", quest_id)

    def append_verification_result(
        self, quest_id: str, result_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._record("v1_verification_results", result_id, quest_id, data)

    def list_verification_results(self, quest_id: str) -> list[dict[str, Any]]:
        return self._list_records("v1_verification_results", quest_id)

    def append_progress(
        self, quest_id: str, entry_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._record("v1_progress_entries", entry_id, quest_id, data)

    def list_progress(self, quest_id: str) -> list[dict[str, Any]]:
        return self._list_records("v1_progress_entries", quest_id)

    def append_decision(
        self, quest_id: str, decision_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._record("v1_decisions", decision_id, quest_id, data)

    def apply_decision(
        self,
        quest_id: str,
        decision_id: str,
        data: Mapping[str, Any],
        *,
        expected_state_version: int,
        kind: str,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        contract: Mapping[str, Any] | None = None,
        plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically record a user decision and every resulting mutation."""
        with self._transaction():
            row = self._conn.execute(
                "SELECT state_json, state_version, status FROM v1_quests WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()
            if row is None:
                raise KeyError(quest_id)
            if int(row["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            if row["status"] != "waiting_user":
                raise ValueError("decision not requested")
            state = json.loads(row["state_json"])
            if kind == "approve":
                pending = state.get("pending_approval") or {}
                if data.get("contract_patch", {}).get("action_id") != pending.get(
                    "action_id"
                ):
                    raise ValueError("approval target mismatch")
            milestones: list[dict[str, Any]] | None = None
            if kind == "modify":
                if contract is None or plan is None:
                    raise ValueError("modified contract and plan are required")
                milestones = self._validate_plan(plan)
                proposed = {str(item["id"]): item for item in milestones}
                for old in state.get("milestones", []):
                    if old.get("status") != "completed":
                        continue
                    new = proposed.get(str(old["id"]))
                    if new is None or any(
                        old.get(field) != new.get(field)
                        for field in ("tool_name", "tool_args", "acceptance_criteria")
                    ):
                        raise ValueError("replan cannot modify a completed milestone")
                    new["status"] = "completed"
                    new["evidence_ids"] = list(old.get("evidence_ids", []))
            now = utc_now()
            try:
                self._conn.execute(
                    "INSERT INTO v1_decisions(id, quest_id, data_json, created_at) VALUES (?, ?, ?, ?)",
                    (decision_id, quest_id, _json(data), now),
                )
                if kind == "modify":
                    assert (
                        contract is not None
                        and plan is not None
                        and milestones is not None
                    )
                    self._insert_contract_locked(quest_id, contract, now)
                    self._insert_plan_locked(quest_id, plan, milestones, now)
                version = expected_state_version
                for event_type, patch in events:
                    self._append_locked(
                        quest_id,
                        event_type,
                        patch,
                        expected_state_version=version,
                        event_schema_version=EVENT_SCHEMA_VERSION,
                        now=now,
                    )
                    version += 1
            except sqlite3.IntegrityError as exc:
                raise ValueError("decision conflict") from exc
        return self.require_quest(quest_id)

    def list_decisions(self, quest_id: str) -> list[dict[str, Any]]:
        return self._list_records("v1_decisions", quest_id)

    def acquire_lease(self, quest_id: str, owner: str, ttl: float) -> bool:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        now = time.time()
        with self._transaction():
            row = self._conn.execute(
                "SELECT owner, expires_at FROM v1_leases WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()
            if row is not None and float(row["expires_at"]) > now:
                # A live execution lease is exclusive even for the same
                # service owner.  Treating a duplicate acquisition as success
                # lets a losing concurrent start release the winner's lease.
                return False
            self._conn.execute("DELETE FROM v1_leases WHERE quest_id = ?", (quest_id,))
            self._conn.execute(
                "INSERT INTO v1_leases(quest_id, owner, expires_at) VALUES (?, ?, ?)",
                (quest_id, owner, now + ttl),
            )
            return True

    def admit_execution(
        self,
        quest_id: str,
        owner: str,
        ttl_seconds: float,
        expected_state_version: int,
        admitted_at: str,
    ) -> dict[str, Any] | None:
        """Atomically claim one execution lease and append its admission event."""
        if ttl_seconds <= 0 or not owner or not admitted_at:
            raise ValueError("execution admission is invalid")
        now = time.time()
        with self._transaction():
            row = self._conn.execute(
                "SELECT state_json, state_version FROM v1_quests WHERE quest_id=?",
                (quest_id,),
            ).fetchone()
            if row is None:
                raise KeyError(quest_id)
            if int(row["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            state = json.loads(row["state_json"])
            if state.get("status") not in {
                "planned",
                "recovering",
                "running",
                "verifying",
                "replanning",
            }:
                raise ValueError("quest is not active")
            lease = self._conn.execute(
                "SELECT owner, expires_at FROM v1_leases WHERE quest_id=?", (quest_id,)
            ).fetchone()
            if (
                lease is not None
                and float(lease["expires_at"]) > now
                and lease["owner"] != owner
            ):
                return None
            self._conn.execute("DELETE FROM v1_leases WHERE quest_id=?", (quest_id,))
            self._conn.execute(
                "INSERT INTO v1_leases(quest_id, owner, expires_at) VALUES (?, ?, ?)",
                (quest_id, owner, now + ttl_seconds),
            )
            patch: dict[str, Any] = {"status": "running", "error": None}
            if state.get("started_at") is None:
                patch["started_at"] = admitted_at
            return self._append_locked(
                quest_id,
                "ExecutionAdmitted",
                patch,
                expected_state_version=expected_state_version,
                event_schema_version=EVENT_SCHEMA_VERSION,
            )

    def renew_lease(self, quest_id: str, owner: str, ttl_seconds: float) -> bool:
        """Extend only the currently live lease held by this exact owner."""
        if ttl_seconds <= 0 or not owner:
            raise ValueError("lease renewal is invalid")
        now = time.time()
        with self._transaction():
            cursor = self._conn.execute(
                """UPDATE v1_leases SET expires_at=?
                WHERE quest_id=? AND owner=? AND expires_at>?""",
                (now + ttl_seconds, quest_id, owner, now),
            )
            return cursor.rowcount == 1

    def release_lease(self, quest_id: str, owner: str) -> bool:
        with self._transaction():
            cursor = self._conn.execute(
                "DELETE FROM v1_leases WHERE quest_id = ? AND owner = ?",
                (quest_id, owner),
            )
            return cursor.rowcount > 0

    def acquire_resource_lease(
        self, resource_key: str, quest_id: str, owner: str, ttl: float
    ) -> bool:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        now = time.time()
        with self._transaction():
            row = self._conn.execute(
                """
                SELECT quest_id, owner, expires_at FROM v1_resource_leases
                WHERE resource_key = ?
                """,
                (resource_key,),
            ).fetchone()
            if row is not None and float(row["expires_at"]) > now:
                return row["quest_id"] == quest_id and row["owner"] == owner
            self._conn.execute(
                "DELETE FROM v1_resource_leases WHERE resource_key = ?",
                (resource_key,),
            )
            self._conn.execute(
                """
                INSERT INTO v1_resource_leases(
                    resource_key, quest_id, owner, expires_at
                ) VALUES (?, ?, ?, ?)
                """,
                (resource_key, quest_id, owner, now + ttl),
            )
            return True

    def release_resource_lease(
        self, resource_key: str, quest_id: str, owner: str
    ) -> bool:
        with self._transaction():
            cursor = self._conn.execute(
                """
                DELETE FROM v1_resource_leases
                WHERE resource_key = ? AND quest_id = ? AND owner = ?
                """,
                (resource_key, quest_id, owner),
            )
            return cursor.rowcount > 0

    def clear_runtime_leases_on_startup(self) -> tuple[int, int]:
        """Clear leases after an exclusive single-node process restart.

        This method is only valid for the documented single-node deployment.
        It must run before the new service accepts requests.
        """

        with self._transaction():
            quest_count = self._conn.execute("DELETE FROM v1_leases").rowcount
            resource_count = self._conn.execute(
                "DELETE FROM v1_resource_leases"
            ).rowcount
            return quest_count, resource_count

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_id": row["action_id"],
            "quest_id": row["quest_id"],
            "milestone_id": row["milestone_id"],
            "idempotency_key": row["idempotency_key"],
            "tool_name": row["tool_name"],
            "arguments_hash": row["arguments_hash"],
            "arguments": json.loads(row["arguments_json"]),
            "pre_effect_hash": row["pre_effect_hash"],
            "expected_state_version": int(row["expected_state_version"]),
            "status": row["status"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "committed_event_id": row["committed_event_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def prepare_action(
        self,
        action_id: str,
        quest_id: str,
        milestone_id: str,
        idempotency_key: str,
        tool_name: str,
        arguments_hash: str,
        arguments: Mapping[str, Any],
        expected_state_version: int,
        pre_effect_hash: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction():
            projection = self._conn.execute(
                "SELECT state_version FROM v1_quests WHERE quest_id = ?",
                (quest_id,),
            ).fetchone()
            if projection is None:
                raise KeyError(quest_id)
            existing = self._conn.execute(
                """
                SELECT * FROM v1_tool_actions
                WHERE quest_id = ? AND idempotency_key = ?
                """,
                (quest_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["arguments_hash"] != arguments_hash
                    or existing["tool_name"] != tool_name
                ):
                    raise ValueError("idempotency key conflict")
                return self._action_from_row(existing)
            if int(projection["state_version"]) != expected_state_version:
                raise ValueError("state version conflict")
            now = utc_now()
            self._conn.execute(
                """
                INSERT INTO v1_tool_actions(
                    action_id, quest_id, milestone_id, idempotency_key,
                    tool_name, arguments_hash, arguments_json,
                    pre_effect_hash, expected_state_version, status,
                    result_json, error_json, created_at, updated_at, committed_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', NULL, NULL, ?, ?, NULL)
                """,
                (
                    action_id,
                    quest_id,
                    milestone_id,
                    idempotency_key,
                    tool_name,
                    arguments_hash,
                    _json(arguments),
                    pre_effect_hash,
                    expected_state_version,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self._action_from_row(row)

    def _transition_action(
        self,
        action_id: str,
        *,
        allowed_from: set[str],
        status: str,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError(action_id)
            if row["status"] == status:
                return self._action_from_row(row)
            if row["status"] not in allowed_from:
                raise ValueError(
                    f"action transition {row['status']} -> {status} is not allowed"
                )
            self._conn.execute(
                """
                UPDATE v1_tool_actions
                SET status = ?, result_json = ?, error_json = ?, updated_at = ?
                WHERE action_id = ?
                """,
                (
                    status,
                    _json(result) if result is not None else None,
                    _json(error) if error is not None else None,
                    utc_now(),
                    action_id,
                ),
            )
            updated = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self._action_from_row(updated)

    def mark_action_dispatched(self, action_id: str) -> dict[str, Any]:
        return self._transition_action(
            action_id, allowed_from={"prepared"}, status="dispatched"
        )

    def commit_action(
        self, action_id: str, result: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._transition_action(
            action_id,
            allowed_from={"dispatched", "unknown_effect"},
            status="committed",
            result=result,
        )

    def commit_action_with_event(
        self,
        action_id: str,
        result: Mapping[str, Any],
        *,
        file_observation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist an effect, receipt event, and optional file observation."""
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError(action_id)
            if row["status"] == "committed":
                if _json(json.loads(row["result_json"])) != _json(result):
                    raise ValueError("committed action result conflict")
                if row["committed_event_id"] is None:
                    raise ValueError("committed action receipt event missing")
                return self._action_from_row(row)
            if row["status"] not in {"dispatched", "unknown_effect"}:
                raise ValueError(
                    f"action transition {row['status']} -> committed is not allowed"
                )
            now = utc_now()
            self._conn.execute(
                "UPDATE v1_tool_actions SET status='committed', result_json=?, error_json=NULL, updated_at=? WHERE action_id=?",
                (_json(result), now, action_id),
            )
            projection = self._conn.execute(
                "SELECT state_version FROM v1_quests WHERE quest_id=?",
                (row["quest_id"],),
            ).fetchone()
            receipt = {
                "action_id": action_id,
                "idempotency_key": row["idempotency_key"],
                "status": "committed",
                "result": dict(result),
                "error": None,
            }
            event = self._append_locked(
                row["quest_id"],
                "ToolCommitted",
                {"last_receipt": receipt},
                expected_state_version=int(projection["state_version"]),
                event_schema_version=EVENT_SCHEMA_VERSION,
                now=now,
            )
            self._conn.execute(
                "UPDATE v1_tool_actions SET committed_event_id=? WHERE action_id=?",
                (event["id"], action_id),
            )
            if file_observation is not None:
                self._insert_tool_file_observation_locked(
                    file_observation,
                    action_id=action_id,
                    quest_id=row["quest_id"],
                    committed_event_id=int(event["id"]),
                )
            updated = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE action_id=?", (action_id,)
            ).fetchone()
            return self._action_from_row(updated)

    def fail_action(self, action_id: str, error: Mapping[str, Any]) -> dict[str, Any]:
        return self._transition_action(
            action_id,
            allowed_from={"prepared", "dispatched"},
            status="failed",
            error=error,
        )

    def mark_action_unknown(
        self, action_id: str, error: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._transition_action(
            action_id,
            allowed_from={"dispatched"},
            status="unknown_effect",
            error=error,
        )

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v1_tool_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self._action_from_row(row) if row else None

    def list_unresolved_actions(self, quest_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM v1_tool_actions
                WHERE quest_id = ? AND status IN ('dispatched', 'unknown_effect')
                ORDER BY created_at
                """,
                (quest_id,),
            ).fetchall()
            return [self._action_from_row(row) for row in rows]

    def action_recovery_summary(self) -> dict[str, int]:
        with self._lock:
            linked = self._conn.execute(
                "SELECT COUNT(*) FROM v1_tool_actions WHERE status='committed' AND committed_event_id IS NOT NULL"
            ).fetchone()[0]
            orphan = self._conn.execute(
                "SELECT COUNT(*) FROM v1_tool_actions WHERE status='committed' AND committed_event_id IS NULL"
            ).fetchone()[0]
            duplicate = self._conn.execute(
                "SELECT COUNT(*) FROM (SELECT committed_event_id FROM v1_tool_actions WHERE committed_event_id IS NOT NULL GROUP BY committed_event_id HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            return {
                "linked_committed_actions": int(linked),
                "orphan_committed_actions": int(orphan),
                "duplicate_committed_event_links": int(duplicate),
            }

    def mark_dispatched_actions_unknown(self) -> int:
        with self._transaction():
            cursor = self._conn.execute(
                """
                UPDATE v1_tool_actions
                SET status = 'unknown_effect',
                    error_json = ?, updated_at = ?
                WHERE status = 'dispatched'
                """,
                (
                    _json(
                        {
                            "code": "PROCESS_INTERRUPTED",
                            "message": "Dispatch outcome must be reconciled",
                        }
                    ),
                    utc_now(),
                ),
            )
            return cursor.rowcount

    def create_benchmark_run(
        self, run_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO v1_benchmark_runs(
                    run_id, data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, _json(data), now, now),
            )
        return {"run_id": run_id, **dict(data), "created_at": now, "updated_at": now}

    def update_benchmark_run(
        self, run_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            cursor = self._conn.execute(
                """
                UPDATE v1_benchmark_runs SET data_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (_json(data), now, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)
        return {"run_id": run_id, **dict(data), "updated_at": now}

    def get_benchmark_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v1_benchmark_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": run_id,
            **json.loads(row["data_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def append_benchmark_result(
        self, result_id: str, run_id: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO v1_benchmark_results(id, run_id, data_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (result_id, run_id, _json(data), now),
            )
        return {"id": result_id, "run_id": run_id, **dict(data), "created_at": now}

    def list_benchmark_results(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM v1_benchmark_results
                WHERE run_id = ? ORDER BY created_at, id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": run_id,
                **json.loads(row["data_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # Backward-compatible aliases for the first implementation draft.
    benchmark_run = create_benchmark_run
    benchmark_result = append_benchmark_result

    def import_legacy(self, quests: Iterable[Mapping[str, Any]]) -> int:
        """Explicitly import legacy snapshots without mutating or validating them."""

        imported = 0
        for source in quests:
            old = copy.deepcopy(dict(source))
            quest_id = str(old.get("id") or old.get("quest_id") or "")
            if not quest_id or self.get_quest(quest_id) is not None:
                continue
            old_status = str(old.get("status", "failed"))
            recovery_required = old_status in {"running", "verifying", "recovering"}
            status = "paused" if recovery_required else old_status
            now = utc_now()
            state = {
                "id": quest_id,
                "goal": str(old.get("goal", "Legacy Quest")),
                "workspace": str(old.get("workspace", "")),
                "status": status,
                "state_version": 0,
                "plan_version": 0,
                "contract": None,
                "milestones": old.get("milestones", []),
                "current_milestone_id": None,
                "progress": float(old.get("progress", 0.0)),
                "route": ["legacy"],
                "budget_usage": {},
                "pause_requested": False,
                "recovery_required": recovery_required,
                "artifact_review_required": False,
                "artifact_disposition": "not_applicable",
                "pending_artifact_review": None,
                "legacy_unverified": True,
                "legacy_snapshot_hash": stable_hash(old),
                "error": old.get("error"),
                "created_at": str(old.get("created_at", now)),
                "updated_at": now,
                "started_at": old.get("started_at"),
                "finished_at": old.get("finished_at"),
            }
            with self._transaction():
                self._conn.execute(
                    """
                    INSERT INTO v1_quests(
                        quest_id, state_json, state_version, status,
                        created_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (quest_id, _json({}), status, state["created_at"], now),
                )
                self._append_locked(
                    quest_id,
                    "LegacyStateImported",
                    state,
                    expected_state_version=0,
                    event_schema_version=EVENT_SCHEMA_VERSION,
                    now=now,
                )
            imported += 1
        return imported
