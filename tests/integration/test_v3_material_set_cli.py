from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "inspect_v3_material_set.py"
FIXTURES = Path(__file__).parents[2] / "examples" / "v3-phase-0"
SCENARIOS = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))[
    "scenarios"
]


def _run(
    root: Path | str,
    output: Path | str,
    *files: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--root",
        str(root),
        "--output",
        str(output),
    ]
    for file_name in files:
        command.extend(["--file", file_name])
    return subprocess.run(command, check=False, capture_output=True, text=True, cwd=cwd)


def test_cli_committed_scenarios_and_reversed_positive_reports(tmp_path: Path) -> None:
    for scenario in SCENARIOS:
        root = (FIXTURES / scenario["root"]).resolve()
        first, second = (
            tmp_path / f"{scenario['name']}-a.json",
            tmp_path / f"{scenario['name']}-b.json",
        )
        assert _run(root, first, *scenario["files"]).returncode == (
            0 if scenario["expected_status"] == "complete" else 2
        )
        assert (
            json.loads(first.read_text(encoding="utf-8"))["status"]
            == scenario["expected_status"]
        )
        if scenario["expected_status"] == "complete":
            assert _run(root, second, *reversed(scenario["files"])).returncode == 0
            assert first.read_bytes() == second.read_bytes()


def test_cli_rejects_inside_existing_and_writes_negative_report(tmp_path: Path) -> None:
    root = (FIXTURES / "negative-inputs").resolve()
    outside = tmp_path / "out"
    outside.mkdir()
    negative = outside / "negative.json"
    completed = _run(root, negative, "blank.txt")
    assert completed.returncode == 2
    assert json.loads(negative.read_text(encoding="utf-8"))["status"] == "empty"
    assert _run(root, root / "inside.json", "blank.txt").returncode == 2
    assert (
        _run(
            root, root / "nested" / ".." / "inside-normalized.json", "blank.txt"
        ).returncode
        == 2
    )
    assert _run(root, negative, "blank.txt").returncode == 2


def test_cli_invalid_roots_never_write_reports(tmp_path: Path) -> None:
    material_root = tmp_path / "materials"
    material_root.mkdir()
    (material_root / "blank.txt").write_text(" ", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    candidates: list[tuple[Path | str, Path]] = [
        (tmp_path / "missing", outside / "missing.json"),
        (material_root / "blank.txt", outside / "file-root.json"),
        (str(material_root) + "\\.", outside / "noncanonical.json"),
    ]
    for root, output in candidates:
        assert _run(root, output, "blank.txt").returncode == 2
        assert not output.exists()
    link = tmp_path / "root-link"
    try:
        link.symlink_to(material_root, target_is_directory=True)
    except OSError:
        return
    output = outside / "symlink-root.json"
    assert _run(link, output, "blank.txt").returncode == 2
    assert not output.exists()


def test_cli_relative_root_cannot_write_inside_its_real_directory(
    tmp_path: Path,
) -> None:
    material_root = tmp_path / "materials"
    material_root.mkdir()
    (material_root / "blank.txt").write_text(" ", encoding="utf-8")

    direct = material_root / "relative-inside.json"
    assert _run("materials", direct, "blank.txt", cwd=tmp_path).returncode == 2
    assert not direct.exists()

    nested = material_root / "nested"
    nested.mkdir()
    normalized = material_root / "normalized-inside.json"
    literal_output = str(nested) + "\\..\\normalized-inside.json"
    assert _run("materials", literal_output, "blank.txt", cwd=tmp_path).returncode == 2
    assert not normalized.exists()


def test_cli_rejects_noncanonical_output_parent_without_writing(tmp_path: Path) -> None:
    root = (FIXTURES / "negative-inputs").resolve()
    outside = tmp_path / "outside"
    nested = outside / "nested"
    nested.mkdir(parents=True)
    canonical_target = outside / "report.json"
    literal_output = str(nested) + "\\..\\report.json"

    assert _run(root, literal_output, "blank.txt").returncode == 2
    assert not canonical_target.exists()
