from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from backend.app.safe_files import read_stable_regular_file


@pytest.mark.parametrize("capture_bytes", [False, True])
def test_single_link_mode_reads_regular_file(
    tmp_path: Path, capture_bytes: bool
) -> None:
    path = tmp_path / "source.txt"
    contents = b"single link\n"
    path.write_bytes(contents)

    result = read_stable_regular_file(
        path,
        path.lstat(),
        capture_bytes=capture_bytes,
        require_single_link=True,
    )

    assert result == (
        hashlib.sha256(contents).hexdigest(),
        len(contents),
        contents if capture_bytes else None,
    )


def test_single_link_mode_rejects_hard_link_but_default_remains_compatible(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("shared", encoding="utf-8")
    linked = tmp_path / "linked.txt"
    try:
        os.link(source, linked)
    except OSError as error:
        pytest.skip(f"hard links are unavailable on this platform: {error}")

    metadata = source.lstat()
    assert metadata.st_nlink > 1
    assert read_stable_regular_file(source, metadata, capture_bytes=True) == (
        hashlib.sha256(b"shared").hexdigest(),
        len(b"shared"),
        b"shared",
    )
    assert (
        read_stable_regular_file(
            source,
            metadata,
            capture_bytes=True,
            require_single_link=True,
        )
        is None
    )


@pytest.mark.parametrize("invalid_value", [0, 1, "true", None])
def test_single_link_mode_requires_bool(tmp_path: Path, invalid_value: object) -> None:
    path = tmp_path / "source.txt"
    path.write_text("contents", encoding="utf-8")

    with pytest.raises(TypeError, match="require_single_link must be a bool"):
        read_stable_regular_file(
            path,
            path.lstat(),
            require_single_link=invalid_value,  # type: ignore[arg-type]
        )
