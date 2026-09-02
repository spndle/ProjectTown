from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.tools import Sandbox
from backend.app.v1 import provenance
from backend.app.v1.provenance import (
    SnapshotPolicy,
    classify_artifact_provenance,
    scan_sandbox_workspace,
    scan_workspace,
)

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64


def _entry(path: str, digest: str, size: int) -> dict[str, object]:
    return {"relative_path": path, "sha256": digest, "size": size}


def _action(
    action_id: str, event_id: int, *, status: str = "committed"
) -> dict[str, object]:
    return {
        "action_id": action_id,
        "quest_id": "q1",
        "tool_name": "write_file",
        "arguments": {"relative_path": "report.md"},
        "status": status,
        "committed_event_id": event_id,
    }


def _observation(
    action_id: str,
    event_id: int,
    before: str | None,
    after: str,
    size: int,
    kind: str,
    sequence: int | None = None,
    **overrides: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "action_id": action_id,
        "quest_id": "q1",
        "committed_event_id": event_id,
        "committed_event_sequence": event_id if sequence is None else sequence,
        "relative_path": "report.md",
        "before_sha256": before,
        "after_sha256": after,
        "after_size_bytes": size,
        "change_kind": kind,
        "status": "observed",
    }
    result.update(overrides)
    return result


def _classify(
    *,
    baseline_status: str | None = "complete",
    final_status: str = "complete",
    baseline_entries: list[dict[str, object]] | None = None,
    final_entries: list[dict[str, object]] | None = None,
    actions: list[dict[str, object]] | None = None,
    observations: list[dict[str, object]] | None = None,
    artifact_hash: str = H1,
    artifact_size: int = 1,
) -> dict[str, object]:
    baseline = (
        None
        if baseline_status is None
        else {"quest_id": "q1", "status": baseline_status}
    )
    return classify_artifact_provenance(
        {"relative_path": "report.md", "sha256": artifact_hash, "size": artifact_size},
        baseline,
        baseline_entries or [],
        {"quest_id": "q1", "status": final_status},
        final_entries
        if final_entries is not None
        else [_entry("report.md", artifact_hash, artifact_size)],
        actions or [],
        observations or [],
    )


def test_snapshot_is_deterministic_includes_hidden_files_and_hashes_raw_bytes(
    tmp_path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "z.txt").write_bytes(b"z\x00")
    (root / ".hidden").write_bytes(b"\xff\x00")
    (root / "nested").mkdir()
    (root / "nested" / "a.txt").write_bytes(b"a")

    first = scan_workspace(root, workspace="quests/q1")
    second = scan_workspace(root, workspace="quests/q1")

    assert first == second
    assert first.status == "complete"
    assert [entry.relative_path for entry in first.entries] == [
        ".hidden",
        "nested/a.txt",
        "z.txt",
    ]
    assert (
        first.entries[0].sha256
        == "ea5dbf9596d187e9500f23e9a680109475341cf4e81f7e043f7d97152c10772f"
    )


def test_snapshot_rejects_links_and_reparse_points(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")
    assert scan_workspace(root, workspace="quests/q1").status == "unsupported"


def test_snapshot_rejects_special_files_when_supported(tmp_path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform does not support fifo fixtures")
    root = tmp_path / "workspace"
    root.mkdir()
    fifo = root / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError as error:
        pytest.skip(f"fifo creation is unavailable: {error}")
    assert scan_workspace(root, workspace="quests/q1").status == "unsupported"


def test_snapshot_reports_limits_without_raising(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "one.txt").write_bytes(b"12")
    assert (
        scan_workspace(
            root, workspace="quests/q1", policy=SnapshotPolicy(max_file_bytes=1)
        ).status
        == "limit_exceeded"
    )
    assert (
        scan_workspace(
            root, workspace="quests/q1", policy=SnapshotPolicy(max_files=0)
        ).status
        == "limit_exceeded"
    )
    assert (
        scan_workspace(
            root, workspace="quests/q1", policy=SnapshotPolicy(max_total_bytes=1)
        ).status
        == "limit_exceeded"
    )


def test_snapshot_marks_a_file_that_changes_during_read_unstable(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    monkeypatch.setattr(provenance, "_same_file", lambda _before, _after: False)
    assert scan_workspace(root, workspace="quests/q1").status == "unstable"


def test_snapshot_marks_directory_changed_during_child_processing_unstable(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "payload.txt").write_text("payload", encoding="utf-8")
    original_lstat = Path.lstat
    root_calls = 0

    def changed_final_directory_lstat(path: Path):
        nonlocal root_calls
        metadata = original_lstat(path)
        if path == root:
            root_calls += 1
            if root_calls == 4:

                class Changed:
                    st_mode = metadata.st_mode
                    st_size = metadata.st_size
                    st_mtime_ns = metadata.st_mtime_ns + 1
                    st_ctime_ns = metadata.st_ctime_ns
                    st_ino = metadata.st_ino
                    st_dev = metadata.st_dev

                return Changed()
        return metadata

    monkeypatch.setattr(Path, "lstat", changed_final_directory_lstat)
    assert scan_workspace(root, workspace="quests/q1").status == "unstable"


def test_snapshot_root_hash_changes_with_any_file_byte(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"one")
    before = scan_workspace(root, workspace="quests/q1")
    payload.write_bytes(b"two")
    after = scan_workspace(root, workspace="quests/q1")
    assert before.status == after.status == "complete"
    assert before.root_hash != after.root_hash


def test_snapshot_reparse_is_rejected_with_cross_platform_monkeypatch(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "payload.txt").write_text("payload", encoding="utf-8")
    monkeypatch.setattr(provenance, "_is_reparse", lambda _metadata: True)
    assert scan_workspace(root, workspace="quests/q1").status == "unsupported"


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("report.md", True),
        ("nested/report.md", True),
        ("nested\\report.md", False),
        ("../report.md", False),
        ("nested/../report.md", False),
        ("/report.md", False),
        ("", False),
        ("bad\x00name", False),
    ],
)
def test_snapshot_relative_paths_are_canonical_posix(relative_path, expected) -> None:
    assert provenance._is_safe_snapshot_relative_path(relative_path) is expected


def test_snapshot_rejects_replaced_pending_directory(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "payload.txt").write_text("payload", encoding="utf-8")
    original_lstat = Path.lstat
    calls = 0

    def replaced_lstat(path: Path):
        nonlocal calls
        metadata = original_lstat(path)
        if path == nested:
            calls += 1
            if calls >= 2:

                class Changed:
                    st_mode = metadata.st_mode
                    st_size = metadata.st_size
                    st_mtime_ns = metadata.st_mtime_ns
                    st_ctime_ns = metadata.st_ctime_ns + 1
                    st_ino = metadata.st_ino
                    st_dev = metadata.st_dev

                return Changed()
        return metadata

    monkeypatch.setattr(Path, "lstat", replaced_lstat)
    assert scan_workspace(root, workspace="quests/q1").status == "unstable"


def test_snapshot_reports_missing_or_invalid_roots_structurally(tmp_path) -> None:
    assert (
        scan_workspace(tmp_path / "missing", workspace="quests/q1").status
        == "unrecoverable"
    )
    file_root = tmp_path / "file.txt"
    file_root.write_text("not a directory", encoding="utf-8")
    assert scan_workspace(file_root, workspace="quests/q1").status == "unsupported"


def test_snapshot_uses_an_existing_sandbox_workspace_without_creating_one(
    tmp_path,
) -> None:
    sandbox = Sandbox(tmp_path / "sandbox")
    missing = scan_sandbox_workspace(sandbox, "quests/missing")
    assert missing.status == "unrecoverable"
    assert not (tmp_path / "sandbox" / "quests" / "missing").exists()
    workspace = sandbox.workspace_path("quests/q1", create=True)
    (workspace / "payload.bin").write_bytes(b"payload")
    assert scan_sandbox_workspace(sandbox, "quests/q1").status == "complete"


@pytest.mark.parametrize(
    ("name", "kwargs", "expected"),
    [
        (
            "created",
            {
                "actions": [_action("a1", 10)],
                "observations": [_observation("a1", 10, None, H1, 1, "created")],
            },
            "shadow_observed_created",
        ),
        (
            "continuous_modified",
            {
                "baseline_entries": [_entry("report.md", H0, 1)],
                "actions": [_action("a2", 20), _action("a1", 10)],
                "observations": [
                    _observation("a2", 20, H2, H1, 1, "modified"),
                    _observation("a1", 10, H0, H2, 1, "modified"),
                ],
            },
            "shadow_observed_modified",
        ),
        (
            "unchanged",
            {
                "baseline_entries": [_entry("report.md", H1, 1)],
                "actions": [_action("a1", 10)],
                "observations": [_observation("a1", 10, H1, H1, 1, "unchanged")],
            },
            "shadow_observed_unchanged",
        ),
        (
            "restored",
            {
                "baseline_entries": [_entry("report.md", H1, 1)],
                "actions": [_action("a1", 10), _action("a2", 20)],
                "observations": [
                    _observation("a1", 10, H1, H2, 1, "modified"),
                    _observation("a2", 20, H2, H1, 1, "modified"),
                ],
            },
            "shadow_observed_restored",
        ),
        (
            "existing_unchanged",
            {"baseline_entries": [_entry("report.md", H1, 1)]},
            "shadow_existing_unchanged",
        ),
        (
            "external_drift",
            {"baseline_entries": [_entry("report.md", H0, 1)]},
            "shadow_external_drift",
        ),
        ("unobserved_created", {}, "shadow_unobserved_created"),
        (
            "missing_observation",
            {"actions": [_action("a1", 10)]},
            "unrecoverable_missing_observation",
        ),
        (
            "unresolved",
            {"actions": [_action("a1", 10, status="unknown_effect")]},
            "unrecoverable_unresolved_effect",
        ),
        (
            "chain_break",
            {
                "baseline_entries": [_entry("report.md", H0, 1)],
                "actions": [_action("a1", 10)],
                "observations": [_observation("a1", 10, H2, H1, 1, "modified")],
            },
            "unrecoverable_chain_break",
        ),
        (
            "legacy",
            {"baseline_status": "legacy_unobserved"},
            "legacy_unobserved",
        ),
        ("baseline_missing", {"baseline_status": None}, "legacy_unobserved"),
        (
            "baseline_failure",
            {"baseline_status": "limit_exceeded"},
            "unrecoverable_baseline_limit_exceeded",
        ),
    ],
)
def test_classify_artifact_provenance_matrix(name, kwargs, expected) -> None:
    del name
    result = _classify(**kwargs)
    assert result["provenance_status"] == expected
    assert result["storage_status"] in {"shadow", "legacy_unobserved", "unrecoverable"}
    assert result["storage_status"] != "verified"
    assert result["artifact_hash"] == H1


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"final_status": "unstable"}, "unrecoverable_final_unstable"),
        ({"final_status": "unsupported"}, "unrecoverable_final_unsupported"),
        ({"final_status": "limit_exceeded"}, "unrecoverable_final_limit_exceeded"),
        (
            {"final_status": "legacy_unobserved"},
            "unrecoverable_final_legacy_unobserved",
        ),
        ({"final_status": "unrecoverable"}, "unrecoverable_final_unrecoverable"),
        ({"final_entries": []}, "unrecoverable_final_missing"),
        (
            {"final_entries": [_entry("report.md", H2, 1)]},
            "unrecoverable_final_hash_mismatch",
        ),
        (
            {"final_entries": [_entry("report.md", H1, 2)]},
            "unrecoverable_final_size_mismatch",
        ),
    ],
)
def test_classify_requires_a_matching_complete_final_snapshot(kwargs, expected) -> None:
    assert _classify(**kwargs)["provenance_status"] == expected


@pytest.mark.parametrize("status", ["injected_status", "", None, 7, ["unstable"]])
def test_classify_maps_unknown_or_malformed_final_status_to_invalid(status) -> None:
    assert (
        _classify(final_status=status)["provenance_status"]
        == "unrecoverable_final_invalid"
    )


@pytest.mark.parametrize("status", ["injected_status", "", None, 7, ["unstable"]])
def test_classify_maps_unknown_or_malformed_baseline_status_to_invalid(status) -> None:
    result = classify_artifact_provenance(
        {"relative_path": "report.md", "sha256": H1, "size": 1},
        {"quest_id": "q1", "status": status},
        [],
        {"quest_id": "q1", "status": "complete"},
        [_entry("report.md", H1, 1)],
        [],
        [],
    )
    assert result["provenance_status"] == "unrecoverable_baseline_invalid"


@pytest.mark.parametrize(
    "observation",
    [
        _observation("a1", 10, None, H1, 1, "created", quest_id="q2"),
        _observation("a1", 11, None, H1, 1, "created"),
        _observation("a1", 10, None, H1, 1, "created", relative_path="other.md"),
    ],
)
def test_classify_rejects_cross_quest_event_or_path_observation(observation) -> None:
    result = _classify(actions=[_action("a1", 10)], observations=[observation])
    assert result["provenance_status"] == "unrecoverable_observation_binding"


def test_classify_rejects_duplicate_observations_and_malformed_input() -> None:
    observation = _observation("a1", 10, None, H1, 1, "created")
    duplicate = _classify(
        actions=[_action("a1", 10)], observations=[observation, dict(observation)]
    )
    assert duplicate["provenance_status"] == "unrecoverable_duplicate_observation"
    malformed = classify_artifact_provenance(  # type: ignore[arg-type]
        {"relative_path": "report.md", "sha256": "invalid", "size": 1},
        {"quest_id": "q1", "status": "complete"},
        [],
        {"quest_id": "q1", "status": "complete"},
        [],
        [],
        [],
    )
    assert malformed["provenance_status"] == "unrecoverable_invalid_artifact"


def test_classify_rejects_action_quest_and_terminal_chain_mismatch() -> None:
    wrong_quest = _action("a1", 10)
    wrong_quest["quest_id"] = "q2"
    result = _classify(
        actions=[wrong_quest],
        observations=[_observation("a1", 10, None, H1, 1, "created")],
    )
    assert result["provenance_status"] == "unrecoverable_action_binding"
    terminal = _classify(
        actions=[_action("a1", 10)],
        observations=[_observation("a1", 10, None, H2, 1, "created")],
    )
    assert terminal["provenance_status"] == "unrecoverable_chain_terminal_mismatch"


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../report.md",
        "dir\\report.md",
        "bad\x00name",
        ".",
        "dir/./report.md",
        "dir//report.md",
    ],
)
def test_classify_rejects_unsafe_posix_relative_paths(unsafe_path) -> None:
    artifact = {"relative_path": unsafe_path, "sha256": H1, "size": 1}
    result = classify_artifact_provenance(
        artifact,
        {"quest_id": "q1", "status": "complete"},
        [],
        {"quest_id": "q1", "status": "complete"},
        [_entry("report.md", H1, 1)],
        [],
        [],
    )
    assert result["provenance_status"] == "unrecoverable_invalid_artifact"


@pytest.mark.parametrize("kind", ["entry", "action", "observation"])
def test_classify_rejects_unsafe_paths_in_all_evidence(kind) -> None:
    action = _action("a1", 10)
    observation = _observation("a1", 10, None, H1, 1, "created")
    baseline_entries: list[dict[str, object]] = []
    final_entries: list[dict[str, object]] = [_entry("report.md", H1, 1)]
    if kind == "entry":
        final_entries = [_entry("dir/../report.md", H1, 1)]
    elif kind == "action":
        action["arguments"] = {"relative_path": "dir\\report.md"}
    else:
        observation["relative_path"] = "dir//report.md"
    result = _classify(
        baseline_entries=baseline_entries,
        final_entries=final_entries,
        actions=[action],
        observations=[observation],
    )
    assert result["provenance_status"].startswith("unrecoverable_")


def test_classify_orders_by_committed_event_sequence_not_global_event_id() -> None:
    first = _action("a1", 200)
    second = _action("a2", 100)
    result = _classify(
        baseline_entries=[_entry("report.md", H0, 1)],
        actions=[second, first],
        observations=[
            _observation("a1", 200, H0, H2, 1, "modified", sequence=10),
            _observation("a2", 100, H2, H1, 1, "modified", sequence=20),
        ],
    )
    assert result["provenance_status"] == "shadow_observed_modified"
    assert result["terminal_action_id"] == "a2"
    assert result["terminal_committed_event_id"] == 100


@pytest.mark.parametrize("sequence", [None, 0, -1, True, "10"])
def test_classify_requires_a_positive_integer_observation_sequence(sequence) -> None:
    observation = _observation("a1", 10, None, H1, 1, "created")
    if sequence is None:
        observation.pop("committed_event_sequence")
    else:
        observation["committed_event_sequence"] = sequence
    result = _classify(actions=[_action("a1", 10)], observations=[observation])
    assert result["provenance_status"] == "unrecoverable_observation_binding"


def test_classify_rejects_duplicate_observation_event_sequences() -> None:
    result = _classify(
        baseline_entries=[_entry("report.md", H0, 1)],
        actions=[_action("a1", 10), _action("a2", 20)],
        observations=[
            _observation("a1", 10, H0, H2, 1, "modified", sequence=10),
            _observation("a2", 20, H2, H1, 1, "modified", sequence=10),
        ],
    )
    assert result["provenance_status"] == "unrecoverable_event_binding"


def test_same_file_includes_device_and_read_requires_caller_metadata(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    payload = root / "payload.txt"
    payload.write_text("payload", encoding="utf-8")
    metadata = payload.lstat()

    class OtherDevice:
        st_mode = metadata.st_mode
        st_size = metadata.st_size
        st_mtime_ns = metadata.st_mtime_ns
        st_ctime_ns = metadata.st_ctime_ns
        st_ino = metadata.st_ino
        st_dev = metadata.st_dev + 1

    assert not provenance._same_file(metadata, OtherDevice())
    original_lstat = Path.lstat
    calls = 0

    def replaced_lstat(path: Path):
        nonlocal calls
        observed = original_lstat(path)
        if path == payload:
            calls += 1
            if calls == 1:

                class Replaced:
                    st_mode = observed.st_mode
                    st_size = observed.st_size
                    st_mtime_ns = observed.st_mtime_ns
                    st_ctime_ns = observed.st_ctime_ns
                    st_ino = observed.st_ino
                    st_dev = observed.st_dev + 1

                return Replaced()
        return observed

    monkeypatch.setattr(Path, "lstat", replaced_lstat)
    assert provenance._read_regular(payload, metadata) is None


def test_read_regular_rejects_mismatched_open_descriptor_before_any_read(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"\xff\x00")
    metadata = payload.lstat()
    original_fstat = os.fstat
    read_calls = 0

    def changed_fstat(descriptor: int):
        observed = original_fstat(descriptor)

        class Changed:
            st_mode = observed.st_mode
            st_size = observed.st_size
            st_mtime_ns = observed.st_mtime_ns
            st_ctime_ns = observed.st_ctime_ns
            st_ino = observed.st_ino
            st_dev = observed.st_dev + 1

        return Changed()

    def no_read(descriptor: int, size: int) -> bytes:
        nonlocal read_calls
        del descriptor, size
        read_calls += 1
        raise AssertionError("a mismatched descriptor must not be read")

    monkeypatch.setattr(provenance.os, "fstat", changed_fstat)
    monkeypatch.setattr(provenance.os, "read", no_read)
    assert provenance._read_regular(payload, metadata) is None
    assert read_calls == 0


def test_read_regular_preserves_raw_byte_hash_with_descriptor_reads(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"\xff\x00")
    original_read = os.read
    requested_sizes: list[int] = []

    def tracking_read(descriptor: int, size: int) -> bytes:
        requested_sizes.append(size)
        return original_read(descriptor, size)

    monkeypatch.setattr(provenance.os, "read", tracking_read)
    assert provenance._read_regular(payload, payload.lstat()) == (
        "ea5dbf9596d187e9500f23e9a680109475341cf4e81f7e043f7d97152c10772f",
        2,
    )
    assert requested_sizes == [2, 1]
    assert sum(requested_sizes) <= payload.stat().st_size + 1


def test_read_regular_bounds_reads_when_file_grows_during_read(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"x")
    metadata = payload.lstat()
    original_read = os.read
    requested_sizes: list[int] = []

    def continuously_growing_read(descriptor: int, size: int) -> bytes:
        requested_sizes.append(size)
        del descriptor
        return b"x" * size

    monkeypatch.setattr(provenance.os, "read", continuously_growing_read)
    assert provenance._read_regular(payload, metadata) is None
    assert requested_sizes == [1, 1]
    assert sum(requested_sizes) <= metadata.st_size + 1
    monkeypatch.setattr(provenance.os, "read", original_read)


def test_read_regular_hashes_empty_file_with_a_bounded_eof_check(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    payload = root / "empty.bin"
    payload.write_bytes(b"")
    assert provenance._read_regular(payload, payload.lstat()) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        0,
    )
