from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from backend.app import materials
from backend.app.materials import MaterialSetPolicy, inspect_material_set


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "materials"
    root.mkdir()
    return root


def test_complete_is_deterministic_and_supports_all_suffixes_and_chinese(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    (root / "研究.md").write_text("第一行\n第二行\n", encoding="utf-8")
    (root / "a.txt").write_text("text", encoding="utf-8")
    (root / "b.json").write_text('{"a":1}', encoding="utf-8")
    (root / "c.py").write_text("VALUE = 1\n", encoding="utf-8")
    first = inspect_material_set(root, ["c.py", "研究.md", "b.json", "a.txt"])
    second = inspect_material_set(root, ["a.txt", "b.json", "c.py", "研究.md"])
    assert first.status == second.status == "complete"
    assert first.to_dict() == second.to_dict()
    assert [entry.relative_path for entry in first.entries] == [
        "a.txt",
        "b.json",
        "c.py",
        "研究.md",
    ]
    assert first.entries[3].line_count == 2
    assert first.entries[0].sha256 == hashlib.sha256(b"text").hexdigest()
    rendered = first.to_dict()
    assert rendered["schema_version"] == "v3-material-set-manifest-v1"
    assert rendered["policy"] == {
        "version": "v3-material-set-v1",
        "max_files": 100,
        "max_file_bytes": 1_048_576,
        "max_total_bytes": 10 * 1_048_576,
    }


@pytest.mark.parametrize(
    "selected",
    [
        [],
        ["../a.txt"],
        ["/a.txt"],
        ["C:a.txt"],
        ["a\x00.txt"],
        ["a\\b.txt"],
        ["."],
        [".."],
        ["a/./b.txt"],
        ["a.txt", "A.TXT"],
        "a.txt",
    ],
)
def test_invalid_or_duplicate_selections_fail_closed(
    tmp_path: Path, selected: list[str]
) -> None:
    root = _root(tmp_path)
    (root / "a.txt").write_text("x", encoding="utf-8")
    result = inspect_material_set(root, selected)
    assert result.status in {"empty", "unsupported"}
    assert (
        result.entries == ()
        and result.root_hash is None
        and result.file_count == result.total_bytes == 0
    )


def test_non_nfc_and_roots_fail_without_creation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "a.txt").write_text("x", encoding="utf-8")
    decomposed = unicodedata.normalize("NFD", "é.txt")
    assert inspect_material_set(root, [decomposed]).status == "unsupported"
    missing = tmp_path / "missing"
    assert inspect_material_set(missing, ["a.txt"]).status == "unrecoverable"
    assert not missing.exists()
    assert inspect_material_set(Path("relative"), ["a.txt"]).status == "unsupported"
    noncanonical = root / ".." / root.name
    assert inspect_material_set(noncanonical, ["a.txt"]).status == "unsupported"


@pytest.mark.parametrize(
    "name,contents,status",
    [
        ("missing.txt", None, "unrecoverable"),
        ("bad.csv", b"a,b", "unsupported"),
        ("bad.txt", b"\xff", "unsupported"),
        ("blank.txt", b" \n", "empty"),
    ],
)
def test_file_failures_are_structured(
    tmp_path: Path, name: str, contents: bytes | None, status: str
) -> None:
    root = _root(tmp_path)
    if contents is not None:
        (root / name).write_bytes(contents)
    result = inspect_material_set(root, [name])
    assert result.status == status
    assert (
        result.entries == ()
        and result.root_hash is None
        and result.file_count == result.total_bytes == 0
    )


def test_directory_limits_unselected_and_hash_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    (root / "selected.txt").write_text("one", encoding="utf-8")
    (root / "ignored.txt").write_text("ignored", encoding="utf-8")
    (root / "ignored.csv").write_text("ignored", encoding="utf-8")
    (root / "directory").mkdir()
    assert inspect_material_set(root, ["selected.txt"]).status == "complete"
    assert inspect_material_set(root, ["directory"]).status == "unsupported"
    assert (
        inspect_material_set(
            root, ["selected.txt", "ignored.txt"], policy=MaterialSetPolicy(max_files=1)
        ).status
        == "limit_exceeded"
    )
    assert (
        inspect_material_set(
            root, ["selected.txt"], policy=MaterialSetPolicy(max_file_bytes=2)
        ).status
        == "limit_exceeded"
    )
    assert (
        inspect_material_set(
            root,
            ["selected.txt", "ignored.txt"],
            policy=MaterialSetPolicy(max_total_bytes=5),
        ).status
        == "limit_exceeded"
    )
    first = inspect_material_set(root, ["selected.txt"])
    changed_selection = inspect_material_set(root, ["selected.txt", "ignored.txt"])
    changed_policy = inspect_material_set(
        root, ["selected.txt"], policy=MaterialSetPolicy(version="other")
    )
    changed_limit = inspect_material_set(
        root, ["selected.txt"], policy=MaterialSetPolicy(max_files=99)
    )
    (root / "selected.txt").write_text("two", encoding="utf-8")
    changed_bytes = inspect_material_set(root, ["selected.txt"])
    assert first.root_hash != changed_policy.root_hash != changed_bytes.root_hash
    assert first.root_hash != changed_selection.root_hash
    assert first.root_hash != changed_limit.root_hash
    monkeypatch.setattr(
        materials, "MANIFEST_SCHEMA_VERSION", "v3-material-set-manifest-v2"
    )
    changed_schema = inspect_material_set(root, ["selected.txt"])
    assert changed_schema.root_hash != changed_bytes.root_hash
    assert (
        inspect_material_set(
            root, ["selected.txt"], policy=MaterialSetPolicy(version="")
        ).status
        == "unsupported"
    )
    for field in ("max_files", "max_file_bytes", "max_total_bytes"):
        for invalid in (True, "1", 1.5, None):
            policy_values = {field: invalid}
            assert (
                inspect_material_set(
                    root,
                    ["selected.txt"],
                    policy=MaterialSetPolicy(**policy_values),
                ).status
                == "unsupported"
            )  # type: ignore[arg-type]


def test_symlink_unstable_and_no_leakage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    payload = root / "payload.txt"
    payload.write_text("secret content", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(payload)
    except OSError:
        pytest.skip("symlink unavailable")
    assert inspect_material_set(root, ["link.txt"]).status == "unsupported"
    monkeypatch.setattr(
        materials, "read_stable_regular_file", lambda *_args, **_kwargs: None
    )
    result = inspect_material_set(root, ["payload.txt"])
    rendered = str(result.to_dict())
    assert result.status == "unstable"
    assert str(root) not in rendered and "secret content" not in rendered


def test_monkeypatched_reparse_and_source_bytes_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    source = root / "payload.txt"
    source.write_bytes(b"unchanged\n")
    before = source.read_bytes()
    monkeypatch.setattr(materials, "is_reparse", lambda _metadata: True)
    assert inspect_material_set(root, ["payload.txt"]).status == "unsupported"
    assert source.read_bytes() == before


def test_committed_scenarios() -> None:
    base = Path(__file__).parents[2] / "examples" / "v3-phase-0"
    scenario_manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert scenario_manifest["schema_version"] == "v3-phase-0-scenarios-v1"
    assert scenario_manifest["policy_version"] == "v3-material-set-v1"
    for scenario in scenario_manifest["scenarios"]:
        result = inspect_material_set(base / scenario["root"], scenario["files"])
        assert result.status == scenario["expected_status"]
