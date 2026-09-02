from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FIXTURE_IDS = (
    "main",
    "tutorial",
    "history",
    "failure",
    "artifact_review_waiting_user",
    "restore_waiting_user",
    "settings",
)
VIEWPORTS = ((1280, 720), (1920, 1080), (900, 720))
REQUIRED_MANIFEST = {
    "schema_version",
    "fixtures",
    "godot_version",
    "renderer",
    "font_hashes",
    "asset_hashes",
    "comparator",
    "acceptance_log",
}
OPTIONAL_MANIFEST: set[str] = set()
FIXTURE_FIELDS = {
    "id",
    "script",
    "viewport",
    "expected_png_sha256",
    "expected_rgba_sha256",
    "comparator",
}
GODOT_VERSION = "4.7.1.stable.official.a13da4feb"
RENDERER = "gl_compatibility"
MAX_PIXELS = 20_000_000
MAX_DECOMPRESSED = 256_000_000


@dataclass(frozen=True)
class Image:
    width: int
    height: int
    rgba: bytes


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def encode_png(image: Image) -> bytes:
    if len(image.rgba) != image.width * image.height * 4:
        raise ValueError("RGBA length does not match dimensions")
    rows = b"".join(
        b"\0" + image.rgba[y * image.width * 4 : (y + 1) * image.width * 4]
        for y in range(image.height)
    )
    return (
        PNG_SIGNATURE
        + _chunk(
            b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
        )
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def inspect_png(data: bytes) -> tuple[int, int]:
    """Validate PNG framing/CRCs without decompressing IDAT payloads."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("invalid PNG signature")
    offset, width, height, chunk_count = len(PNG_SIGNATURE), 0, 0, 0
    seen_ihdr = seen_idat = seen_iend = False
    idat_length = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind, body = (
            data[offset + 4 : offset + 8],
            data[offset + 8 : offset + 8 + length],
        )
        crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        if len(body) != length or zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValueError("invalid PNG CRC")
        offset += 12 + length
        if kind == b"IHDR":
            if chunk_count != 0 or seen_ihdr or length != 13:
                raise ValueError("invalid IHDR")
            seen_ihdr = True
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", body)
            )
            if (
                not width
                or not height
                or width * height > MAX_PIXELS
                or depth != 8
                or color not in (2, 6)
                or compression
                or filtering
                or interlace
            ):
                raise ValueError("unsupported PNG format")
        elif kind == b"IDAT":
            if not seen_ihdr or seen_iend:
                raise ValueError("invalid IDAT")
            seen_idat = True
            idat_length += length
        elif kind == b"IEND":
            if (
                not seen_ihdr
                or not seen_idat
                or length
                or seen_iend
                or offset != len(data)
            ):
                raise ValueError("invalid IEND")
            seen_iend = True
            break
        chunk_count += 1
    if not seen_ihdr or not seen_idat or not idat_length or not seen_iend:
        raise ValueError("PNG is missing required chunks")
    return width, height


def parse_png(data: bytes) -> Image:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("invalid PNG signature")
    offset, width, height, color, payload = len(PNG_SIGNATURE), 0, 0, -1, bytearray()
    seen_ihdr = seen_iend = False
    chunk_count = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind, body = (
            data[offset + 4 : offset + 8],
            data[offset + 8 : offset + 8 + length],
        )
        crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        if len(body) != length or zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValueError("invalid PNG CRC")
        offset += 12 + length
        if kind == b"IHDR":
            if chunk_count != 0 or seen_ihdr or payload or length != 13:
                raise ValueError("invalid IHDR")
            seen_ihdr = True
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", body)
            )
            if (
                not width
                or not height
                or width * height > MAX_PIXELS
                or depth != 8
                or color not in (2, 6)
                or compression
                or filtering
                or interlace
            ):
                raise ValueError("unsupported PNG format")
        elif kind == b"IDAT":
            if not seen_ihdr or seen_iend:
                raise ValueError("invalid IDAT")
            payload.extend(body)
        elif kind == b"IEND":
            if length or seen_iend:
                raise ValueError("invalid IEND")
            seen_iend = True
            if offset != len(data):
                raise ValueError("PNG has trailing bytes")
            break
        chunk_count += 1
    if not seen_ihdr or not seen_iend or not payload:
        raise ValueError("PNG is missing required chunks")
    channels = 4 if color == 6 else 3
    decoder = zlib.decompressobj()
    expected_length = height * (width * (4 if color == 6 else 3) + 1)
    raw = decoder.decompress(bytes(payload), expected_length + 1)
    if (
        len(raw) != expected_length
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise ValueError("PNG decompression limit exceeded")
    stride, previous, rgba = width * channels, bytes(width * channels), bytearray()
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG data length mismatch")
    for row_index in range(height):
        start, filter_type = row_index * (stride + 1), raw[row_index * (stride + 1)]
        source, row = raw[start + 1 : start + 1 + stride], bytearray(stride)
        for index, value in enumerate(source):
            left, up = (
                (row[index - channels] if index >= channels else 0),
                previous[index],
            )
            if filter_type == 0:
                result = value
            elif filter_type == 1:
                result = (value + left) & 255
            elif filter_type == 2:
                result = (value + up) & 255
            elif filter_type == 3:
                result = (value + ((left + up) // 2)) & 255
            elif filter_type == 4:
                upper_left = previous[index - channels] if index >= channels else 0
                pa, pb, pc = (
                    abs(up - upper_left),
                    abs(left - upper_left),
                    abs(left + up - 2 * upper_left),
                )
                result = (
                    value
                    + (
                        left
                        if pa <= pb and pa <= pc
                        else up
                        if pb <= pc
                        else upper_left
                    )
                ) & 255
            else:
                raise ValueError("unsupported PNG filter")
            row[index] = result
        if channels == 4:
            rgba.extend(row)
        else:
            for index in range(0, len(row), 3):
                rgba.extend((*row[index : index + 3], 255))
        previous = bytes(row)
    return Image(width, height, bytes(rgba))


def rgba_sha256(image: Image) -> str:
    return hashlib.sha256(image.rgba).hexdigest()


def compare(expected: Image, actual: Image) -> dict[str, float | int | bool]:
    if (expected.width, expected.height) != (actual.width, actual.height):
        return {
            "match": False,
            "changed_pixel_ratio": 1.0,
            "mean_abs_channel_error": 255.0,
            "max_channel_error": 255,
        }
    changed = total_error = maximum = 0
    for pixel in range(expected.width * expected.height):
        errors = [
            abs(expected.rgba[pixel * 4 + channel] - actual.rgba[pixel * 4 + channel])
            for channel in range(4)
        ]
        changed += int(any(errors))
        total_error += sum(errors)
        maximum = max(maximum, max(errors))
    return {
        "match": changed == 0,
        "changed_pixel_ratio": changed / (expected.width * expected.height),
        "mean_abs_channel_error": total_error / len(expected.rgba),
        "max_channel_error": maximum,
    }


def diff_heatmap(expected: Image, actual: Image) -> Image:
    if (expected.width, expected.height) != (actual.width, actual.height):
        raise ValueError("diff dimensions differ")
    pixels = bytearray()
    for index in range(0, len(expected.rgba), 4):
        error = max(
            abs(expected.rgba[index + channel] - actual.rgba[index + channel])
            for channel in range(4)
        )
        pixels.extend((error, 0, 255 - error, 255))
    return Image(expected.width, expected.height, bytes(pixels))


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def load_manifest(
    path: Path,
    *,
    allowed_missing_pairs: set[tuple[str, tuple[int, int]]] | None = None,
) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) - OPTIONAL_MANIFEST != REQUIRED_MANIFEST
        or not isinstance(data["fixtures"], list)
    ):
        raise ValueError("manifest fields are invalid")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["godot_version"] != GODOT_VERSION
        or data["renderer"] != RENDERER
    ):
        raise ValueError("manifest engine guard is invalid")
    if any(
        not isinstance(mapping, dict)
        or not mapping
        or any(
            not isinstance(key, str) or not key or not _hex64(value)
            for key, value in mapping.items()
        )
        for mapping in (data["font_hashes"], data["asset_hashes"])
    ):
        raise ValueError("manifest asset hashes are invalid")
    if not isinstance(data["comparator"], dict):
        raise TypeError("manifest comparator is invalid")
    keys = set(data["comparator"])
    if keys != {
        "changed_pixel_ratio",
        "mean_abs_channel_error",
        "max_channel_error",
    } or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= (1 if key == "changed_pixel_ratio" else 255)
        for key, value in data["comparator"].items()
    ):
        raise ValueError("manifest comparator thresholds are invalid")
    pairs = []
    for fixture in data["fixtures"]:
        if (
            not isinstance(fixture, dict)
            or set(fixture) != FIXTURE_FIELDS
            or fixture["id"] not in FIXTURE_IDS
            or not _hex64(fixture["expected_png_sha256"])
            or not _hex64(fixture["expected_rgba_sha256"])
        ):
            raise ValueError("fixture fields are invalid")
        comparator = fixture["comparator"]
        if (
            fixture["script"] != f"res://tests/capture/{fixture['id']}.gd"
            or not isinstance(fixture["viewport"], list)
            or tuple(fixture["viewport"]) not in VIEWPORTS
            or not isinstance(comparator, dict)
            or set(comparator) != keys
            or not all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and 0 <= value <= (1 if key == "changed_pixel_ratio" else 255)
                for key, value in comparator.items()
            )
        ):
            raise ValueError("fixture contract is invalid")
        pairs.append((fixture["id"], tuple(fixture["viewport"])))
    expected_pairs = {
        (fixture_id, viewport) for fixture_id in FIXTURE_IDS for viewport in VIEWPORTS
    }
    allowed_missing = (
        set() if allowed_missing_pairs is None else set(allowed_missing_pairs)
    )
    actual_pairs = set(pairs)
    if (
        not allowed_missing <= expected_pairs
        or not actual_pairs <= expected_pairs
        or expected_pairs - actual_pairs > allowed_missing
        or len(actual_pairs) != len(pairs)
    ):
        raise ValueError("manifest must contain the complete capture matrix")
    if (
        not isinstance(data["acceptance_log"], list)
        or len(data["acceptance_log"]) > 1000
    ):
        raise ValueError("manifest acceptance log is invalid")
    required_acceptance = {
        "revision",
        "fixture_id",
        "viewport",
        "old_png_sha256",
        "new_png_sha256",
        "reason",
        "reviewed_by",
    }
    previous_revision = 0
    for acceptance in data["acceptance_log"]:
        if (
            not isinstance(acceptance, dict)
            or set(acceptance) != required_acceptance
            or type(acceptance["revision"]) is not int
            or acceptance["revision"] <= previous_revision
            or acceptance["fixture_id"] not in FIXTURE_IDS
            or not isinstance(acceptance["viewport"], list)
            or tuple(acceptance["viewport"]) not in VIEWPORTS
            or (
                acceptance["old_png_sha256"] is not None
                and not _hex64(acceptance["old_png_sha256"])
            )
            or not _hex64(acceptance["new_png_sha256"])
            or not all(
                isinstance(acceptance[key], str) and acceptance[key].strip()
                for key in ("reason", "reviewed_by")
            )
        ):
            raise ValueError("manifest acceptance log is invalid")
        previous_revision = acceptance["revision"]
    return data


def confined(path: Path, root: Path) -> Path:
    resolved, base = path.resolve(), root.resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError("path escapes sandbox")
    return resolved


def _roots(project_root: Path, sandbox: Path) -> tuple[Path, Path, Path]:
    project = project_root.resolve()
    if not (project / "godot" / "project.godot").is_file():
        raise ValueError("project root is invalid")
    sandbox_root = project / "sandbox"
    if sandbox.resolve() != sandbox_root.resolve():
        raise ValueError("sandbox root is invalid")
    return (
        project,
        sandbox_root.resolve(),
        (project / "godot" / "tests" / "goldens").resolve(),
    )


def _guard_project_hashes(manifest: dict[str, Any], project_root: Path) -> None:
    for map_name, prefix in (
        ("font_hashes", "godot/assets/fonts/"),
        ("asset_hashes", "godot/"),
    ):
        for relative_name, expected_hash in manifest[map_name].items():
            if not isinstance(relative_name, str) or "\\" in relative_name:
                raise ValueError("project hash path is invalid")
            relative = PurePosixPath(relative_name)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in ("", ".", "..") for part in relative.parts)
                or not relative_name.startswith(prefix)
            ):
                raise ValueError("project hash path is invalid")
            source = project_root.joinpath(*relative.parts)
            resolved = confined(source, project_root)
            if (
                source.is_symlink()
                or not resolved.is_file()
                or hashlib.sha256(resolved.read_bytes()).hexdigest() != expected_hash
            ):
                raise ValueError("project hash guard failed")


def accept_candidate(
    candidate: Path,
    golden: Path,
    sandbox: Path,
    reason: str,
    reviewed_by: str,
    manifest: Path | None = None,
    fixture_id: str = "",
    viewport: tuple[int, int] | None = None,
    project_root: Path | None = None,
) -> None:
    if (
        os.environ.get("PROJECTTOWN_UPDATE_GOLDENS") != "1"
        or os.environ.get("CI")
        or os.environ.get("GITHUB_ACTIONS")
        or not reason.strip()
        or not reviewed_by.strip()
    ):
        raise PermissionError("golden update gate rejected")
    if manifest is None or project_root is None or not fixture_id or viewport is None:
        raise ValueError(
            "accept requires project root, manifest, fixture id and viewport"
        )
    _, sandbox_root, golden_root = _roots(project_root, sandbox)
    expected_manifest = golden_root / "manifest.json"
    candidate, golden, manifest = (
        confined(candidate, sandbox_root),
        confined(golden, golden_root),
        manifest.resolve(),
    )
    if manifest != expected_manifest:
        raise ValueError("manifest must be canonical")
    # A newly introduced deterministic fixture must still pass the same accept
    # gate for every viewport.  During that narrow transition only the settings
    # rows may be absent; ordinary fixture updates continue to require a fully
    # strict manifest.
    allowed_missing = (
        {("settings", candidate_viewport) for candidate_viewport in VIEWPORTS}
        if fixture_id == "settings"
        else set()
    )
    data = load_manifest(manifest, allowed_missing_pairs=allowed_missing)
    _guard_project_hashes(data, project_root.resolve())
    candidate_png = candidate.read_bytes()
    candidate_image = parse_png(candidate_png)
    expected_name = f"{fixture_id}-{viewport[0]}x{viewport[1]}.png"
    if candidate.name != expected_name or golden.name != expected_name:
        raise ValueError("accept file target does not match fixture")
    if (candidate_image.width, candidate_image.height) != viewport:
        raise ValueError("accept candidate viewport mismatch")
    target = next(
        (
            item
            for item in data["fixtures"]
            if item["id"] == fixture_id and tuple(item["viewport"]) == viewport
        ),
        None,
    )
    if target is None:
        if (fixture_id, viewport) not in allowed_missing:
            raise ValueError("accept fixture target is invalid")
        target = {
            "id": fixture_id,
            "script": f"res://tests/capture/{fixture_id}.gd",
            "viewport": list(viewport),
            "expected_png_sha256": "0" * 64,
            "expected_rgba_sha256": "0" * 64,
            "comparator": dict(data["comparator"]),
        }
        data["fixtures"].append(target)
        fixture_order = {name: index for index, name in enumerate(FIXTURE_IDS)}
        viewport_order = {value: index for index, value in enumerate(VIEWPORTS)}
        data["fixtures"].sort(
            key=lambda item: (
                fixture_order[item["id"]],
                viewport_order[tuple(item["viewport"])],
            )
        )
    old = hashlib.sha256(golden.read_bytes()).hexdigest() if golden.exists() else None
    new = hashlib.sha256(candidate_png).hexdigest()
    target["expected_png_sha256"] = new
    target["expected_rgba_sha256"] = rgba_sha256(candidate_image)
    revision = (
        data["acceptance_log"][-1]["revision"] + 1 if data["acceptance_log"] else 1
    )
    data["acceptance_log"].append(
        {
            "revision": revision,
            "fixture_id": fixture_id,
            "viewport": list(viewport),
            "old_png_sha256": old,
            "new_png_sha256": new,
            "reason": reason,
            "reviewed_by": reviewed_by,
        }
    )
    # Validate the updated document before replacing either target file.
    temporary_manifest = manifest.with_suffix(manifest.suffix + ".tmp")
    temporary_manifest.write_text(
        json.dumps(data, sort_keys=True, indent=2), encoding="utf-8"
    )
    load_manifest(temporary_manifest, allowed_missing_pairs=allowed_missing)
    temporary = golden.with_suffix(golden.suffix + ".tmp")
    temporary.write_bytes(candidate_png)
    temporary.replace(golden)
    temporary_manifest.replace(manifest)


def verify(
    manifest_path: Path,
    golden_root: Path,
    candidate_root: Path,
    diff_root: Path,
    sandbox: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    _, sandbox_root, canonical_root = _roots(project_root, sandbox)
    manifest_path = manifest_path.resolve()
    if manifest_path != canonical_root / "manifest.json":
        raise ValueError("manifest must be canonical")
    manifest = load_manifest(manifest_path)
    _guard_project_hashes(manifest, project_root.resolve())
    golden_root, candidate_root, diff_root = (
        confined(golden_root, canonical_root),
        confined(candidate_root, sandbox_root),
        confined(diff_root, sandbox_root),
    )
    if golden_root == canonical_root or golden_root.parent != canonical_root:
        raise ValueError("golden root must be a canonical platform directory")
    results = []
    for fixture in manifest["fixtures"]:
        stem = f"{fixture['id']}-{fixture['viewport'][0]}x{fixture['viewport'][1]}.png"
        golden_path, candidate_path, diff_path = (
            confined(golden_root / stem, canonical_root),
            confined(candidate_root / stem, sandbox_root),
            confined(diff_root / stem, sandbox_root),
        )
        golden_bytes, candidate_bytes = (
            golden_path.read_bytes(),
            candidate_path.read_bytes(),
        )
        golden_hash, candidate_hash = (
            hashlib.sha256(golden_bytes).hexdigest(),
            hashlib.sha256(candidate_bytes).hexdigest(),
        )
        golden_dimensions, candidate_dimensions = (
            inspect_png(golden_bytes),
            inspect_png(candidate_bytes),
        )
        if (
            golden_hash == fixture["expected_png_sha256"]
            and candidate_bytes == golden_bytes
            and golden_dimensions == tuple(fixture["viewport"]) == candidate_dimensions
        ):
            results.append(
                {
                    "fixture": fixture["id"],
                    "viewport": fixture["viewport"],
                    "passed": True,
                    "metrics": {
                        "match": True,
                        "changed_pixel_ratio": 0.0,
                        "mean_abs_channel_error": 0.0,
                        "max_channel_error": 0,
                    },
                    "expected_rgba_sha256": fixture["expected_rgba_sha256"],
                    "candidate_rgba_sha256": fixture["expected_rgba_sha256"],
                    "expected_png_sha256": golden_hash,
                    "candidate_png_sha256": candidate_hash,
                }
            )
            continue
        expected, actual = parse_png(golden_bytes), parse_png(candidate_bytes)
        dimensions = (
            (expected.width, expected.height)
            == tuple(fixture["viewport"])
            == (actual.width, actual.height)
        )
        metrics = compare(expected, actual)
        threshold = fixture["comparator"]
        passed = (
            dimensions
            and golden_hash == fixture["expected_png_sha256"]
            and rgba_sha256(expected) == fixture["expected_rgba_sha256"]
            and (
                metrics["match"]
                or (
                    metrics["changed_pixel_ratio"] <= threshold["changed_pixel_ratio"]
                    and metrics["mean_abs_channel_error"]
                    <= threshold["mean_abs_channel_error"]
                    and metrics["max_channel_error"] <= threshold["max_channel_error"]
                )
            )
        )
        if not passed and (expected.width, expected.height) == (
            actual.width,
            actual.height,
        ):
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_bytes(encode_png(diff_heatmap(expected, actual)))
        results.append(
            {
                "fixture": fixture["id"],
                "viewport": fixture["viewport"],
                "passed": passed,
                "metrics": metrics,
                "expected_rgba_sha256": rgba_sha256(expected),
                "candidate_rgba_sha256": rgba_sha256(actual),
                "expected_png_sha256": golden_hash,
                "candidate_png_sha256": candidate_hash,
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "accept"))
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--golden", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--golden-root", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--diff-root", type=Path)
    parser.add_argument("--fixture-id")
    parser.add_argument("--viewport", nargs=2, type=int)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--reviewed-by", default="")
    args = parser.parse_args(argv)
    if args.command == "verify":
        if not all(
            (args.manifest, args.golden_root, args.candidate_root, args.diff_root)
        ):
            parser.error("verify requires manifest, roots and diff root")
        try:
            results = verify(
                args.manifest,
                args.golden_root,
                args.candidate_root,
                args.diff_root,
                args.sandbox,
                args.project_root,
            )
        except (OSError, PermissionError, ValueError, json.JSONDecodeError) as error:
            print(
                json.dumps({"ok": False, "error": type(error).__name__}, sort_keys=True)
            )
            return 2
        print(
            json.dumps(
                {"ok": all(item["passed"] for item in results), "results": results},
                sort_keys=True,
            )
        )
        return int(not all(item["passed"] for item in results))
    if not all(
        (
            args.candidate,
            args.golden,
            args.manifest,
            args.fixture_id,
            args.viewport,
            args.project_root,
        )
    ):
        parser.error(
            "accept requires project-root, candidate, golden, manifest, fixture-id and viewport"
        )
    try:
        accept_candidate(
            args.candidate,
            args.golden,
            args.sandbox,
            args.reason,
            args.reviewed_by,
            args.manifest,
            args.fixture_id,
            tuple(args.viewport),
            args.project_root,
        )
    except (OSError, PermissionError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
