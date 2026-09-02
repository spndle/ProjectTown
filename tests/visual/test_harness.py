from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from tests.visual import harness


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def _png(width: int, height: int, color: int, raw: bytes) -> bytes:
    return (
        harness.PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _image(width: int = 2, height: int = 1, value: int = 0) -> harness.Image:
    return harness.Image(width, height, bytes((value, 0, 0, 255)) * (width * height))


def _filtered_row(source: bytes, filter_type: int, channels: int = 4) -> bytes:
    encoded = bytearray()
    for index, value in enumerate(source):
        left = source[index - channels] if index >= channels else 0
        predictor = (
            0 if filter_type in (0, 2) else left if filter_type in (1, 4) else left // 2
        )
        encoded.append((value - predictor) & 255)
    return bytes((filter_type,)) + bytes(encoded)


@pytest.mark.parametrize("filter_type", range(5))
def test_png_decodes_each_filter_type(filter_type: int) -> None:
    rgba = bytes((12, 34, 56, 255, 78, 90, 123, 200))
    assert harness.parse_png(
        _png(2, 1, 6, _filtered_row(rgba, filter_type))
    ) == harness.Image(2, 1, rgba)


def test_png_rgb_expands_alpha() -> None:
    rgb = bytes((1, 2, 3, 4, 5, 6))
    assert harness.parse_png(_png(2, 1, 2, _filtered_row(rgb, 1, 3))).rgba == bytes(
        (1, 2, 3, 255, 4, 5, 6, 255)
    )


def test_png_rejects_crc_missing_iend_trailing_oversize_and_zlib_tail() -> None:
    good = harness.encode_png(_image())
    bad_crc = bytearray(good)
    bad_crc[-1] ^= 1
    with pytest.raises(ValueError, match="CRC"):
        harness.parse_png(bytes(bad_crc))
    with pytest.raises(ValueError):
        harness.parse_png(good[:-12])
    with pytest.raises(ValueError):
        harness.parse_png(good + b"trailing")
    oversized = (
        harness.PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 20_000_001, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError):
        harness.parse_png(oversized)
    tail = _png(2, 1, 6, b"\0" + _image().rgba)
    idat_start = tail.index(b"IDAT") - 4
    length = struct.unpack(">I", tail[idat_start : idat_start + 4])[0]
    compressed = tail[idat_start + 8 : idat_start + 8 + length] + zlib.compress(
        b"extra"
    )
    malformed = (
        harness.PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError):
        harness.parse_png(malformed)


def _manifest_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: dict[tuple[str, tuple[int, int]], harness.Image] | None = None,
    comparator: dict[str, float] | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    viewports = ((2, 1), (3, 1), (4, 1))
    monkeypatch.setattr(harness, "VIEWPORTS", viewports)
    project = tmp_path / "project"
    (project / "godot" / "assets" / "fonts").mkdir(parents=True)
    (project / "godot" / "project.godot").write_text("[application]", encoding="utf-8")
    font = project / "godot" / "assets" / "fonts" / "test.ttf"
    asset = project / "godot" / "assets" / "test.asset"
    font.write_bytes(b"font")
    asset.write_bytes(b"asset")
    sandbox = project / "sandbox"
    golden = project / "godot" / "tests" / "goldens" / "windows"
    candidate = sandbox / "candidate"
    diff = sandbox / "diff"
    golden.mkdir(parents=True)
    candidate.mkdir(parents=True)
    thresholds = comparator or {
        "changed_pixel_ratio": 0.0,
        "mean_abs_channel_error": 0.0,
        "max_channel_error": 0.0,
    }
    fixtures = []
    for fixture_id in harness.FIXTURE_IDS:
        for viewport in viewports:
            expected = _image(*viewport, value=1)
            actual = (changed or {}).get((fixture_id, viewport), expected)
            stem = f"{fixture_id}-{viewport[0]}x{viewport[1]}.png"
            golden_bytes = harness.encode_png(expected)
            (golden / stem).write_bytes(golden_bytes)
            (candidate / stem).write_bytes(harness.encode_png(actual))
            fixtures.append(
                {
                    "id": fixture_id,
                    "script": f"res://tests/capture/{fixture_id}.gd",
                    "viewport": list(viewport),
                    "expected_png_sha256": hashlib.sha256(golden_bytes).hexdigest(),
                    "expected_rgba_sha256": harness.rgba_sha256(expected),
                    "comparator": thresholds.copy(),
                }
            )
    manifest = project / "godot" / "tests" / "goldens" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fixtures": fixtures,
                "godot_version": harness.GODOT_VERSION,
                "renderer": harness.RENDERER,
                "font_hashes": {
                    "godot/assets/fonts/test.ttf": hashlib.sha256(
                        font.read_bytes()
                    ).hexdigest()
                },
                "asset_hashes": {
                    "godot/assets/test.asset": hashlib.sha256(
                        asset.read_bytes()
                    ).hexdigest()
                },
                "comparator": thresholds,
                "acceptance_log": [],
            }
        ),
        encoding="utf-8",
    )
    return sandbox, manifest, golden, candidate, diff


def test_manifest_requires_strict_matrix_hashes_thresholds_and_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, manifest, _, _, _ = _manifest_tree(tmp_path, monkeypatch)
    assert len(harness.load_manifest(manifest)["fixtures"]) == len(
        harness.FIXTURE_IDS
    ) * len(harness.VIEWPORTS)
    original = json.loads(manifest.read_text())
    broken = dict(original)
    broken["fixtures"] = original["fixtures"][:-1]
    manifest.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)
    broken = dict(original)
    broken["font_hashes"] = []
    manifest.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)
    broken = dict(original)
    broken["comparator"] = {
        "changed_pixel_ratio": 1.1,
        "mean_abs_channel_error": 0.0,
        "max_channel_error": 0.0,
    }
    manifest.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)
    broken = dict(original)
    broken["schema_version"] = True
    manifest.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)
    broken = dict(original)
    broken["fixtures"] = [dict(item) for item in original["fixtures"]]
    broken["fixtures"][0]["comparator"] = {
        "changed_pixel_ratio": True,
        "mean_abs_channel_error": 0.0,
        "max_channel_error": 0.0,
    }
    manifest.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)
    broken = dict(original)
    broken["acceptance_log"] = [{"fixture_id": "main"}]
    manifest.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)


def test_confined_rejects_real_parent_escape(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with pytest.raises(ValueError):
        harness.confined(sandbox / ".." / "outside", sandbox)


def test_confined_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    sandbox.mkdir()
    outside.mkdir()
    link = sandbox / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError):
        harness.confined(link / "candidate.png", sandbox)


def test_verify_full_matrix_exact_pass_and_never_writes_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    before = {path.name: path.read_bytes() for path in golden.iterdir()}
    monkeypatch.setattr(
        harness,
        "parse_png",
        lambda _: (_ for _ in ()).throw(AssertionError("exact fast path parsed PNG")),
    )
    results = harness.verify(manifest, golden, candidate, diff, sandbox, sandbox.parent)
    assert len(results) == len(harness.FIXTURE_IDS) * len(harness.VIEWPORTS) and all(
        item["passed"] for item in results
    )
    assert before == {path.name: path.read_bytes() for path in golden.iterdir()}


def test_verify_tolerant_pass_then_zero_threshold_failure_writes_parseable_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewport = (2, 1)
    thresholds = {
        "changed_pixel_ratio": 1.0,
        "mean_abs_channel_error": 1.0,
        "max_channel_error": 1.0,
    }
    sandbox, manifest, golden, candidate, diff = _manifest_tree(
        tmp_path,
        monkeypatch,
        {("main", viewport): _image(*viewport, value=2)},
        thresholds,
    )
    original_parse, calls = harness.parse_png, []

    def tracked_parse(data: bytes) -> harness.Image:
        calls.append(data)
        return original_parse(data)

    monkeypatch.setattr(harness, "parse_png", tracked_parse)
    assert all(
        item["passed"]
        for item in harness.verify(
            manifest, golden, candidate, diff, sandbox, sandbox.parent
        )
    )
    assert len(calls) >= 2
    data = json.loads(manifest.read_text())
    data["fixtures"][0]["comparator"] = {
        "changed_pixel_ratio": 0.0,
        "mean_abs_channel_error": 0.0,
        "max_channel_error": 0.0,
    }
    manifest.write_text(json.dumps(data))
    assert not harness.verify(
        manifest, golden, candidate, diff, sandbox, sandbox.parent
    )[0]["passed"]
    assert harness.parse_png((diff / "main-2x1.png").read_bytes()).width == 2


def test_bad_ihdr_crc_cannot_take_exact_fast_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    target = golden / "main-2x1.png"
    broken = bytearray(target.read_bytes())
    broken[29] ^= 1
    target.write_bytes(broken)
    (candidate / target.name).write_bytes(broken)
    data = json.loads(manifest.read_text())
    data["fixtures"][0]["expected_png_sha256"] = hashlib.sha256(broken).hexdigest()
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="CRC"):
        harness.verify(manifest, golden, candidate, diff, sandbox, sandbox.parent)


def test_verify_rejects_wrong_viewport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    (candidate / "main-2x1.png").write_bytes(harness.encode_png(_image(1, 1, 1)))
    assert not harness.verify(
        manifest, golden, candidate, diff, sandbox, sandbox.parent
    )[0]["passed"]


def test_path_model_rejects_noncanonical_manifest_and_golden_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    outside_manifest = sandbox / "manifest.json"
    outside_manifest.write_bytes(manifest.read_bytes())
    with pytest.raises(ValueError):
        harness.verify(
            outside_manifest, golden, candidate, diff, sandbox, sandbox.parent
        )
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    with pytest.raises(ValueError):
        harness.accept_candidate(
            candidate / "main-2x1.png",
            sandbox / "main-2x1.png",
            sandbox,
            "review",
            "reviewer",
            manifest,
            "main",
            (2, 1),
            sandbox.parent,
        )


def test_project_hash_guard_rejects_tamper_without_accept_write_and_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    font = sandbox.parent / "godot" / "assets" / "fonts" / "test.ttf"
    font.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        harness.verify(manifest, golden, candidate, diff, sandbox, sandbox.parent)
    assert (
        harness.main(
            [
                "verify",
                "--manifest",
                str(manifest),
                "--golden-root",
                str(golden),
                "--candidate-root",
                str(candidate),
                "--diff-root",
                str(diff),
                "--sandbox",
                str(sandbox),
                "--project-root",
                str(sandbox.parent),
            ]
        )
        == 2
    )
    assert "test.ttf" not in capsys.readouterr().out
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    target = golden / "main-2x1.png"
    before = target.read_bytes()
    with pytest.raises(ValueError):
        harness.accept_candidate(
            candidate / target.name,
            target,
            sandbox,
            "review",
            "reviewer",
            manifest,
            "main",
            (2, 1),
            sandbox.parent,
        )
    assert target.read_bytes() == before
    font.write_bytes(b"font")
    (sandbox.parent / "godot" / "assets" / "test.asset").write_bytes(b"tampered asset")
    with pytest.raises(ValueError):
        harness.verify(manifest, golden, candidate, diff, sandbox, sandbox.parent)
    data = json.loads(manifest.read_text())
    data["font_hashes"] = {"../outside.ttf": "a" * 64}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        harness.verify(manifest, golden, candidate, diff, sandbox, sandbox.parent)


def test_accept_invalid_target_leaves_golden_and_manifest_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, _ = _manifest_tree(tmp_path, monkeypatch)
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    target = golden / "main-2x1.png"
    before_golden, before_manifest = target.read_bytes(), manifest.read_bytes()
    with pytest.raises(ValueError):
        harness.accept_candidate(
            candidate / target.name,
            target,
            sandbox,
            "review",
            "reviewer",
            manifest,
            "nope",
            (2, 1),
            sandbox.parent,
        )
    assert (
        target.read_bytes() == before_golden
        and manifest.read_bytes() == before_manifest
    )
    with pytest.raises(ValueError):
        harness.accept_candidate(
            candidate / target.name,
            golden / "tutorial-2x1.png",
            sandbox,
            "review",
            "reviewer",
            manifest,
            "main",
            (2, 1),
            sandbox.parent,
        )
    assert (
        target.read_bytes() == before_golden
        and manifest.read_bytes() == before_manifest
    )


def test_accept_double_gate_syncs_manifest_audit_and_verify_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    target = golden / "main-2x1.png"
    candidate_target = candidate / target.name
    candidate_target.write_bytes(harness.encode_png(_image(2, 1, 7)))
    with pytest.raises(PermissionError):
        harness.accept_candidate(
            candidate_target,
            target,
            sandbox,
            "review",
            "reviewer",
            manifest,
            "main",
            (2, 1),
            sandbox.parent,
        )
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    harness.accept_candidate(
        candidate_target,
        target,
        sandbox,
        "review",
        "reviewer",
        manifest,
        "main",
        (2, 1),
        sandbox.parent,
    )
    accepted = harness.load_manifest(manifest)
    first = accepted["fixtures"][0]
    assert (
        first["expected_png_sha256"]
        == hashlib.sha256(candidate_target.read_bytes()).hexdigest()
    )
    assert first["expected_rgba_sha256"] == harness.rgba_sha256(
        harness.parse_png(candidate_target.read_bytes())
    )
    assert (
        accepted["acceptance_log"][-1]["fixture_id"] == "main"
        and accepted["acceptance_log"][-1]["old_png_sha256"]
    )
    second = candidate / "main-3x1.png"
    second.write_bytes(harness.encode_png(_image(3, 1, 9)))
    harness.accept_candidate(
        second,
        golden / second.name,
        sandbox,
        "second review",
        "reviewer",
        manifest,
        "main",
        (3, 1),
        sandbox.parent,
    )
    accepted = harness.load_manifest(manifest)
    assert [entry["revision"] for entry in accepted["acceptance_log"]] == [1, 2]
    assert all(
        item["passed"]
        for item in harness.verify(
            manifest, golden, candidate, diff, sandbox, sandbox.parent
        )
    )


def test_accept_gate_can_register_only_complete_settings_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["fixtures"] = [item for item in data["fixtures"] if item["id"] != "settings"]
    for viewport in harness.VIEWPORTS:
        (golden / f"settings-{viewport[0]}x{viewport[1]}.png").unlink()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        harness.load_manifest(manifest)
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    for viewport in harness.VIEWPORTS:
        name = f"settings-{viewport[0]}x{viewport[1]}.png"
        harness.accept_candidate(
            candidate / name,
            golden / name,
            sandbox,
            "add deterministic settings fixture",
            "Sol",
            manifest,
            "settings",
            viewport,
            sandbox.parent,
        )
    accepted = harness.load_manifest(manifest)
    assert len(accepted["fixtures"]) == len(harness.FIXTURE_IDS) * len(
        harness.VIEWPORTS
    )
    assert [item["fixture_id"] for item in accepted["acceptance_log"][-3:]] == [
        "settings"
    ] * 3
    assert all(
        item["passed"]
        for item in harness.verify(
            manifest, golden, candidate, diff, sandbox, sandbox.parent
        )
    )


def test_cli_accept_requires_target_and_cli_verify_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, manifest, golden, candidate, diff = _manifest_tree(tmp_path, monkeypatch)
    assert (
        harness.main(
            [
                "verify",
                "--manifest",
                str(manifest),
                "--golden-root",
                str(golden),
                "--candidate-root",
                str(candidate),
                "--diff-root",
                str(diff),
                "--sandbox",
                str(sandbox),
                "--project-root",
                str(sandbox.parent),
            ]
        )
        == 0
    )
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    with pytest.raises(SystemExit):
        harness.main(
            [
                "accept",
                "--candidate",
                str(candidate / "main-2x1.png"),
                "--golden",
                str(golden / "main-2x1.png"),
                "--manifest",
                str(manifest),
                "--sandbox",
                str(sandbox),
                "--project-root",
                str(sandbox.parent),
                "--reason",
                "r",
                "--reviewed-by",
                "u",
            ]
        )
    assert (
        harness.main(
            [
                "accept",
                "--candidate",
                str(candidate / "main-2x1.png"),
                "--golden",
                str(golden / "main-2x1.png"),
                "--manifest",
                str(manifest),
                "--fixture-id",
                "main",
                "--viewport",
                "2",
                "1",
                "--sandbox",
                str(sandbox),
                "--project-root",
                str(sandbox.parent),
                "--reason",
                "review",
                "--reviewed-by",
                "reviewer",
            ]
        )
        == 0
    )
    assert all(
        item["passed"]
        for item in harness.verify(
            manifest, golden, candidate, diff, sandbox, sandbox.parent
        )
    )


def test_accept_rejects_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox, manifest, golden, candidate, _ = _manifest_tree(tmp_path, monkeypatch)
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(PermissionError):
        harness.accept_candidate(
            candidate / "main-2x1.png",
            golden / "main-2x1.png",
            sandbox,
            "reason",
            "reviewer",
            manifest,
            "main",
            (2, 1),
            sandbox.parent,
        )
