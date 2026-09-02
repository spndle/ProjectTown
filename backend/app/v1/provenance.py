"""Read-only workspace snapshotting for shadow provenance.

This module deliberately has no dependency on tool execution or storage.  Its
caller supplies a workspace root already resolved by :class:`Sandbox`; scanning
never creates, deletes, opens for writing, or follows links beneath that root.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..safe_files import is_reparse as _is_reparse
from ..safe_files import is_safe_directory as _is_safe_directory
from ..safe_files import read_stable_regular_file
from ..safe_files import same_file as _same_file
from ..tools import Sandbox

DEFAULT_POLICY_VERSION = "workspace-snapshot-v1"
DEFAULT_MAX_FILES = 2_000
DEFAULT_MAX_FILE_BYTES = 1_048_576
DEFAULT_MAX_TOTAL_BYTES = 64 * 1_048_576


@dataclass(frozen=True)
class SnapshotPolicy:
    version: str = DEFAULT_POLICY_VERSION
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES


DEFAULT_POLICY = SnapshotPolicy()


@dataclass(frozen=True)
class WorkspaceSnapshotEntry:
    relative_path: str
    file_type: str
    size: int
    sha256: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    workspace: str
    policy_version: str
    status: str
    entries: tuple[WorkspaceSnapshotEntry, ...]
    root_hash: str | None
    file_count: int
    total_bytes: int

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["entries"] = [asdict(entry) for entry in self.entries]
        return result


def _is_safe_snapshot_relative_path(relative_path: str) -> bool:
    return (
        bool(relative_path)
        and "\x00" not in relative_path
        and not relative_path.startswith("/")
        and "\\" not in relative_path
        and all(part not in {"", ".", ".."} for part in relative_path.split("/"))
    )


def _root_hash(policy_version: str, entries: list[WorkspaceSnapshotEntry]) -> str:
    payload = {
        "policy_version": policy_version,
        "entries": [asdict(entry) for entry in entries],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result(
    workspace: str,
    policy: SnapshotPolicy,
    status: str,
    entries: list[WorkspaceSnapshotEntry],
    total_bytes: int,
) -> WorkspaceSnapshot:
    complete = status == "complete"
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    return WorkspaceSnapshot(
        workspace=workspace,
        policy_version=policy.version,
        status=status,
        entries=ordered if complete else (),
        root_hash=_root_hash(policy.version, list(ordered)) if complete else None,
        file_count=len(ordered) if complete else 0,
        total_bytes=total_bytes if complete else 0,
    )


def _read_regular(path: Path, metadata: os.stat_result) -> tuple[str, int] | None:
    """Hash one file only if it remains the same regular file during reading."""
    read = read_stable_regular_file(path, metadata)
    return None if read is None else (read[0], read[1])


def scan_workspace(
    workspace_root: Path,
    *,
    workspace: str,
    policy: SnapshotPolicy = DEFAULT_POLICY,
) -> WorkspaceSnapshot:
    """Produce a deterministic, non-mutating snapshot of an existing workspace.

    Any link, junction, special file, resource limit, I/O failure, or observed
    instability becomes a structured status.  Those expected boundary outcomes
    are never raised to the caller.
    """

    if not workspace or "\x00" in workspace:
        return _result(workspace, policy, "unsupported", [], 0)
    if policy.max_files < 0 or policy.max_file_bytes < 0 or policy.max_total_bytes < 0:
        return _result(workspace, policy, "unsupported", [], 0)
    try:
        root_metadata = workspace_root.lstat()
        if not _is_safe_directory(root_metadata):
            return _result(workspace, policy, "unsupported", [], 0)
    except OSError:
        return _result(workspace, policy, "unrecoverable", [], 0)

    entries: list[WorkspaceSnapshotEntry] = []
    total_bytes = 0
    pending: list[tuple[Path, str, os.stat_result]] = [
        (workspace_root, "", root_metadata)
    ]
    try:
        while pending:
            directory, prefix, expected_directory = pending.pop()
            before_directory = directory.lstat()
            if not _is_safe_directory(before_directory):
                return _result(workspace, policy, "unsupported", [], 0)
            if not _same_file(expected_directory, before_directory):
                return _result(workspace, policy, "unstable", [], 0)
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda item: item.name)
            after_directory = directory.lstat()
            if not _is_safe_directory(after_directory):
                return _result(workspace, policy, "unsupported", [], 0)
            if not _same_file(before_directory, after_directory):
                return _result(workspace, policy, "unstable", [], 0)
            for child in children:
                relative_path = f"{prefix}/{child.name}" if prefix else child.name
                if not _is_safe_snapshot_relative_path(relative_path):
                    return _result(workspace, policy, "unsupported", [], 0)
                child_path = Path(child.path)
                metadata = child_path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                    return _result(workspace, policy, "unsupported", [], 0)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append((child_path, relative_path, metadata))
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    return _result(workspace, policy, "unsupported", [], 0)
                if len(entries) >= policy.max_files:
                    return _result(workspace, policy, "limit_exceeded", [], 0)
                if metadata.st_size > policy.max_file_bytes:
                    return _result(workspace, policy, "limit_exceeded", [], 0)
                if total_bytes + metadata.st_size > policy.max_total_bytes:
                    return _result(workspace, policy, "limit_exceeded", [], 0)
                read = _read_regular(child_path, metadata)
                if read is None:
                    return _result(workspace, policy, "unstable", [], 0)
                digest, size = read
                if (
                    size > policy.max_file_bytes
                    or total_bytes + size > policy.max_total_bytes
                ):
                    return _result(workspace, policy, "limit_exceeded", [], 0)
                entries.append(
                    WorkspaceSnapshotEntry(
                        relative_path=relative_path,
                        file_type="regular",
                        size=size,
                        sha256=digest,
                    )
                )
                total_bytes += size
            final_directory = directory.lstat()
            if not _is_safe_directory(final_directory):
                return _result(workspace, policy, "unsupported", [], 0)
            if not _same_file(before_directory, final_directory):
                return _result(workspace, policy, "unstable", [], 0)
    except OSError:
        return _result(workspace, policy, "unrecoverable", [], 0)
    return _result(workspace, policy, "complete", entries, total_bytes)


def scan_sandbox_workspace(
    sandbox: Sandbox,
    workspace: str,
    *,
    policy: SnapshotPolicy = DEFAULT_POLICY,
) -> WorkspaceSnapshot:
    """Scan a Sandbox-resolved existing workspace without creating it."""

    try:
        root = sandbox.workspace_path(workspace, create=False)
    except ToolError:
        return _result(workspace, policy, "unsupported", [], 0)
    return scan_workspace(root, workspace=workspace, policy=policy)


_SHA256_HEX = frozenset("0123456789abcdef")
_UNRESOLVED_ACTION_STATES = frozenset({"prepared", "dispatched", "unknown_effect"})


def _status_result(
    status: str,
    artifact_hash: str | None,
    reason_code: str,
    *,
    action_id: str | None = None,
    committed_event_id: int | None = None,
) -> dict[str, object]:
    """Build the stable, compatibility-shadow classifier result shape."""

    storage_status = (
        "legacy_unobserved"
        if status == "legacy_unobserved"
        else "unrecoverable"
        if status.startswith("unrecoverable_")
        else "shadow"
    )
    return {
        "provenance_status": status,
        "storage_status": storage_status,
        "artifact_hash": artifact_hash,
        "terminal_action_id": action_id,
        "terminal_committed_event_id": committed_event_id,
        "reason_code": reason_code,
    }


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_HEX for character in value)
    )


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[object] | None:
    return (
        value
        if isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        else None
    )


def _path_from(value: Mapping[str, Any]) -> str | None:
    candidates = [value[key] for key in ("relative_path", "path") if key in value]
    if not candidates or any(
        not isinstance(candidate, str)
        or not candidate
        or "\x00" in candidate
        or "\\" in candidate
        or candidate.startswith("/")
        or any(part in {"", ".", ".."} for part in candidate.split("/"))
        for candidate in candidates
    ):
        return None
    path = candidates[0]
    return path if all(candidate == path for candidate in candidates) else None


def _artifact_values(artifact: Mapping[str, Any]) -> tuple[str, str, int] | None:
    relative_path = _path_from(artifact)
    digest = next(
        (
            artifact.get(key)
            for key in ("sha256", "artifact_hash", "hash")
            if artifact.get(key) is not None
        ),
        None,
    )
    size = next(
        (
            artifact.get(key)
            for key in ("size", "size_bytes", "after_size_bytes")
            if artifact.get(key) is not None
        ),
        None,
    )
    if (
        relative_path is None
        or not _is_sha256(digest)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
    ):
        return None
    return relative_path, digest, size


def _entries_by_path(entries: Sequence[object]) -> dict[str, Mapping[str, Any]] | None:
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw_entry in entries:
        entry = _mapping(raw_entry)
        if entry is None:
            return None
        path = _path_from(entry)
        digest = entry.get("sha256")
        size = entry.get("size")
        if (
            path is None
            or path in indexed
            or not _is_sha256(digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            return None
        indexed[path] = entry
    return indexed


def _action_path(action: Mapping[str, Any]) -> str | None:
    arguments = action.get("arguments")
    return _path_from(arguments) if isinstance(arguments, Mapping) else None


def classify_artifact_provenance(
    artifact: Mapping[str, Any],
    baseline_snapshot: Mapping[str, Any] | None,
    baseline_entries: Sequence[Mapping[str, Any]] | Sequence[object],
    final_snapshot: Mapping[str, Any] | None,
    final_entries: Sequence[Mapping[str, Any]] | Sequence[object],
    file_actions: Sequence[Mapping[str, Any]] | Sequence[object],
    observations: Sequence[Mapping[str, Any]] | Sequence[object],
) -> dict[str, object]:
    """Classify one manifest artifact without claiming verified provenance.

    This is deliberately a pure, compatibility-shadow function: it trusts no
    model output, touches neither the filesystem nor persistence, and returns a
    deterministic ``unrecoverable_*`` result for malformed evidence.
    """

    artifact_hash: str | None = None
    try:
        artifact_map = _mapping(artifact)
        final_map = _mapping(final_snapshot)
        baseline_map = (
            _mapping(baseline_snapshot) if baseline_snapshot is not None else None
        )
        baseline_sequence = _sequence(baseline_entries)
        final_sequence = _sequence(final_entries)
        action_sequence = _sequence(file_actions)
        observation_sequence = _sequence(observations)
        if (
            artifact_map is None
            or final_map is None
            or baseline_sequence is None
            or final_sequence is None
            or action_sequence is None
            or observation_sequence is None
        ):
            return _status_result("unrecoverable_invalid_input", None, "invalid_input")
        artifact_values = _artifact_values(artifact_map)
        if artifact_values is None:
            return _status_result(
                "unrecoverable_invalid_artifact", None, "invalid_artifact"
            )
        relative_path, artifact_hash, artifact_size = artifact_values
        final_status = final_map.get("status")
        if final_status != "complete":
            suffix = (
                final_status
                if isinstance(final_status, str)
                and final_status
                in {
                    "unstable",
                    "unsupported",
                    "limit_exceeded",
                    "legacy_unobserved",
                    "unrecoverable",
                }
                else "invalid"
            )
            return _status_result(
                f"unrecoverable_final_{suffix}",
                artifact_hash,
                "final_snapshot_not_complete",
            )
        final_index = _entries_by_path(final_sequence)
        if final_index is None:
            return _status_result(
                "unrecoverable_final_entries", artifact_hash, "invalid_final_entries"
            )
        final_entry = final_index.get(relative_path)
        if final_entry is None:
            return _status_result(
                "unrecoverable_final_missing",
                artifact_hash,
                "artifact_missing_from_final",
            )
        if final_entry["sha256"] != artifact_hash:
            return _status_result(
                "unrecoverable_final_hash_mismatch",
                artifact_hash,
                "final_hash_mismatch",
            )
        if final_entry["size"] != artifact_size:
            return _status_result(
                "unrecoverable_final_size_mismatch",
                artifact_hash,
                "final_size_mismatch",
            )

        if baseline_map is None:
            return _status_result(
                "legacy_unobserved", artifact_hash, "baseline_missing"
            )
        baseline_status = baseline_map.get("status")
        if baseline_status == "legacy_unobserved":
            return _status_result("legacy_unobserved", artifact_hash, "baseline_legacy")
        if baseline_status != "complete":
            suffix = (
                baseline_status
                if isinstance(baseline_status, str)
                and baseline_status
                in {"unstable", "unsupported", "limit_exceeded", "unrecoverable"}
                else "invalid"
            )
            return _status_result(
                f"unrecoverable_baseline_{suffix}",
                artifact_hash,
                "baseline_snapshot_not_complete",
            )
        quest_id = baseline_map.get("quest_id")
        if (
            not isinstance(quest_id, str)
            or not quest_id
            or final_map.get("quest_id") != quest_id
        ):
            return _status_result(
                "unrecoverable_snapshot_binding",
                artifact_hash,
                "snapshot_quest_mismatch",
            )
        baseline_index = _entries_by_path(baseline_sequence)
        if baseline_index is None:
            return _status_result(
                "unrecoverable_baseline_entries",
                artifact_hash,
                "invalid_baseline_entries",
            )
        baseline_entry = baseline_index.get(relative_path)

        relevant: list[Mapping[str, Any]] = []
        for raw_action in action_sequence:
            action = _mapping(raw_action)
            if action is None:
                return _status_result(
                    "unrecoverable_invalid_actions", artifact_hash, "invalid_action"
                )
            if action.get("tool_name") != "write_file":
                continue
            action_path = _action_path(action)
            if action_path is None:
                return _status_result(
                    "unrecoverable_invalid_actions",
                    artifact_hash,
                    "invalid_action_path",
                )
            if action_path != relative_path:
                continue
            status = action.get("status")
            if status in _UNRESOLVED_ACTION_STATES:
                return _status_result(
                    "unrecoverable_unresolved_effect",
                    artifact_hash,
                    "unresolved_effect",
                )
            if status == "committed":
                relevant.append(action)

        if not relevant:
            if baseline_entry is None:
                return _status_result(
                    "shadow_unobserved_created", artifact_hash, "no_observed_action"
                )
            if (
                baseline_entry["sha256"] == artifact_hash
                and baseline_entry["size"] == artifact_size
            ):
                return _status_result(
                    "shadow_existing_unchanged", artifact_hash, "no_observed_action"
                )
            return _status_result(
                "shadow_external_drift", artifact_hash, "no_observed_action"
            )

        indexed_observations: dict[str, Mapping[str, Any]] = {}
        for raw_observation in observation_sequence:
            observation = _mapping(raw_observation)
            if observation is None:
                return _status_result(
                    "unrecoverable_invalid_observations",
                    artifact_hash,
                    "invalid_observation",
                )
            action_id = observation.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                return _status_result(
                    "unrecoverable_invalid_observations",
                    artifact_hash,
                    "invalid_observation",
                )
            if action_id in indexed_observations:
                return _status_result(
                    "unrecoverable_duplicate_observation",
                    artifact_hash,
                    "duplicate_observation",
                )
            indexed_observations[action_id] = observation

        ordered: list[tuple[int, str, Mapping[str, Any], Mapping[str, Any]]] = []
        for action in relevant:
            action_id = action.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                return _status_result(
                    "unrecoverable_action_binding",
                    artifact_hash,
                    "invalid_action_binding",
                )
            observation = indexed_observations.get(action_id)
            if observation is None:
                return _status_result(
                    "unrecoverable_missing_observation",
                    artifact_hash,
                    "missing_observation",
                )
            sequence = observation.get("committed_event_sequence")
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence <= 0
            ):
                return _status_result(
                    "unrecoverable_observation_binding",
                    artifact_hash,
                    "invalid_committed_event_sequence",
                )
            ordered.append((sequence, action_id, action, observation))
        ordered.sort(key=lambda item: (item[0], item[1]))
        if len({item[0] for item in ordered}) != len(ordered):
            return _status_result(
                "unrecoverable_event_binding",
                artifact_hash,
                "duplicate_committed_event_sequence",
            )
        current_hash = baseline_entry["sha256"] if baseline_entry is not None else None
        current_size = baseline_entry["size"] if baseline_entry is not None else None
        changed_away_from_baseline = False
        terminal_action_id: str | None = None
        terminal_event_id: int | None = None
        for _event_sequence, action_id, action, observation in ordered:
            action_id = action.get("action_id")
            event_id = action.get("committed_event_id")
            if (
                not isinstance(action_id, str)
                or not action_id
                or isinstance(event_id, bool)
                or not isinstance(event_id, int)
                or action.get("quest_id") != quest_id
            ):
                return _status_result(
                    "unrecoverable_action_binding",
                    artifact_hash,
                    "invalid_action_binding",
                )
            before_hash = observation.get("before_sha256")
            after_hash = observation.get("after_sha256")
            after_size = observation.get("after_size_bytes")
            change_kind = observation.get("change_kind")
            if (
                observation.get("status") != "observed"
                or observation.get("quest_id") != quest_id
                or observation.get("action_id") != action_id
                or observation.get("committed_event_id") != event_id
                or _path_from(observation) != relative_path
                or (before_hash is not None and not _is_sha256(before_hash))
                or not _is_sha256(after_hash)
                or isinstance(after_size, bool)
                or not isinstance(after_size, int)
                or after_size < 0
            ):
                return _status_result(
                    "unrecoverable_observation_binding",
                    artifact_hash,
                    "invalid_observation_binding",
                )
            if before_hash != current_hash:
                return _status_result(
                    "unrecoverable_chain_break", artifact_hash, "before_hash_mismatch"
                )
            expected_kind = (
                "created"
                if current_hash is None
                else "unchanged"
                if current_hash == after_hash and current_size == after_size
                else "modified"
            )
            if change_kind != expected_kind:
                return _status_result(
                    "unrecoverable_chain_break", artifact_hash, "change_kind_mismatch"
                )
            if baseline_entry is not None and after_hash != baseline_entry["sha256"]:
                changed_away_from_baseline = True
            current_hash, current_size = after_hash, after_size
            terminal_action_id, terminal_event_id = action_id, event_id
        if current_hash != artifact_hash or current_size != artifact_size:
            return _status_result(
                "unrecoverable_chain_terminal_mismatch",
                artifact_hash,
                "chain_terminal_mismatch",
                action_id=terminal_action_id,
                committed_event_id=terminal_event_id,
            )
        if baseline_entry is None:
            status = "shadow_observed_created"
        elif current_hash == baseline_entry["sha256"] and changed_away_from_baseline:
            status = "shadow_observed_restored"
        elif all(
            observation.get("change_kind") == "unchanged"
            for _event_sequence, _action_id, _action, observation in ordered
        ):
            status = "shadow_observed_unchanged"
        else:
            status = "shadow_observed_modified"
        return _status_result(
            status,
            artifact_hash,
            "observed_chain_complete",
            action_id=terminal_action_id,
            committed_event_id=terminal_event_id,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return _status_result(
            "unrecoverable_invalid_input", artifact_hash, "invalid_input"
        )
