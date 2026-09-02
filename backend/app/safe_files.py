"""Small read-only primitives for stable, non-reparse local files."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

_REPARSE_POINT = 0x0400
_CHUNK_SIZE = 64 * 1024


def is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def is_safe_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not is_reparse(metadata)
    )


def same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_mode == after.st_mode
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
        and before.st_ino == after.st_ino
        and before.st_dev == after.st_dev
    )


def same_opened_regular_file(
    path_metadata: os.stat_result, descriptor_metadata: os.stat_result
) -> bool:
    """Compare identity while allowing Windows lstat/fstat ctime differences."""
    return (
        path_metadata.st_mode == descriptor_metadata.st_mode
        and path_metadata.st_size == descriptor_metadata.st_size
        and path_metadata.st_mtime_ns == descriptor_metadata.st_mtime_ns
        and path_metadata.st_ino == descriptor_metadata.st_ino
        and path_metadata.st_dev == descriptor_metadata.st_dev
    )


def read_stable_regular_file(
    path: Path,
    metadata: os.stat_result,
    *,
    capture_bytes: bool = False,
    require_single_link: bool = False,
) -> tuple[str, int, bytes | None] | None:
    """Read one unchanged regular file through a descriptor, or return ``None``.

    The caller's metadata is rechecked before opening and after reading.  When
    ``require_single_link`` is enabled, every file metadata observation must
    also report exactly one hard link. This is intentionally read-only and
    bounded by the original file size.
    """
    if not isinstance(require_single_link, bool):
        raise TypeError("require_single_link must be a bool")
    if require_single_link and metadata.st_nlink != 1:
        return None
    for _attempt in range(2):
        descriptor: int | None = None
        try:
            before = path.lstat()
            if (
                not same_file(metadata, before)
                or not stat.S_ISREG(before.st_mode)
                or is_reparse(before)
                or (require_single_link and before.st_nlink != 1)
            ):
                return None
            flags = (
                os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if (
                not same_opened_regular_file(before, opened)
                or not stat.S_ISREG(opened.st_mode)
                or is_reparse(opened)
                or (require_single_link and opened.st_nlink != 1)
            ):
                return None
            expected_size = int(before.st_size)
            digest = hashlib.sha256()
            captured = bytearray() if capture_bytes else None
            bytes_read = 0
            while bytes_read < expected_size:
                chunk = os.read(
                    descriptor, min(_CHUNK_SIZE, expected_size - bytes_read)
                )
                if not chunk:
                    return None
                bytes_read += len(chunk)
                if bytes_read > expected_size:
                    return None
                digest.update(chunk)
                if captured is not None:
                    captured.extend(chunk)
            if os.read(descriptor, 1):
                return None
            after_opened = os.fstat(descriptor)
            after = path.lstat()
            if (
                same_file(opened, after_opened)
                and same_file(before, after)
                and same_opened_regular_file(after, after_opened)
                and (not require_single_link or after_opened.st_nlink == 1)
                and (not require_single_link or after.st_nlink == 1)
            ):
                return (
                    digest.hexdigest(),
                    int(after.st_size),
                    bytes(captured) if captured is not None else None,
                )
        except OSError:
            continue
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    return None
