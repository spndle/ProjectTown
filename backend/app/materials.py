"""Offline inspection of an explicitly selected local material set."""

from __future__ import annotations

import hashlib
import json
import stat
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .safe_files import (
    is_reparse,
    is_safe_directory,
    read_stable_regular_file,
    same_file,
)

SUPPORTED_SUFFIXES = frozenset({".md", ".txt", ".json", ".py"})
MANIFEST_SCHEMA_VERSION = "v3-material-set-manifest-v1"


@dataclass(frozen=True)
class MaterialSetPolicy:
    version: str = "v3-material-set-v1"
    max_files: int = 100
    max_file_bytes: int = 1_048_576
    max_total_bytes: int = 10 * 1_048_576


DEFAULT_POLICY = MaterialSetPolicy()


@dataclass(frozen=True)
class MaterialSource:
    relative_path: str
    suffix: str
    size_bytes: int
    sha256: str
    line_count: int


@dataclass(frozen=True)
class MaterialSetIssue:
    code: str
    relative_path: str | None = None


@dataclass(frozen=True)
class MaterialSetManifest:
    schema_version: str
    policy_version: str
    policy: MaterialSetPolicy
    status: str
    entries: tuple[MaterialSource, ...]
    root_hash: str | None
    file_count: int
    total_bytes: int
    issues: tuple[MaterialSetIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "policy": asdict(self.policy),
            "status": self.status,
            "entries": [asdict(item) for item in self.entries],
            "root_hash": self.root_hash,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "issues": [asdict(item) for item in self.issues],
        }


def _manifest(
    policy: MaterialSetPolicy,
    status: str,
    entries: list[MaterialSource] | None = None,
    total_bytes: int = 0,
    issue: MaterialSetIssue | None = None,
) -> MaterialSetManifest:
    if status != "complete":
        return MaterialSetManifest(
            MANIFEST_SCHEMA_VERSION,
            policy.version,
            policy,
            status,
            (),
            None,
            0,
            0,
            (issue,) if issue else (),
        )
    ordered = tuple(sorted(entries or [], key=lambda entry: entry.relative_path))
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "policy": asdict(policy),
        "entries": [asdict(item) for item in ordered],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return MaterialSetManifest(
        MANIFEST_SCHEMA_VERSION,
        policy.version,
        policy,
        "complete",
        ordered,
        hashlib.sha256(encoded).hexdigest(),
        len(ordered),
        total_bytes,
    )


def _issue(
    policy: MaterialSetPolicy, status: str, code: str, relative_path: str | None = None
) -> MaterialSetManifest:
    return _manifest(policy, status, issue=MaterialSetIssue(code, relative_path))


def _canonical_selection(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != unicodedata.normalize("NFC", value)
    ):
        return None
    if "\x00" in value or "\\" in value or value.startswith("/") or ":" in value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return value


def inspect_material_set(
    root: Path,
    selected_relative_paths: Sequence[str],
    *,
    policy: MaterialSetPolicy = DEFAULT_POLICY,
) -> MaterialSetManifest:
    """Return only deterministic metadata for explicit files; never source text."""
    if (
        not isinstance(policy.version, str)
        or not policy.version
        or policy.version != unicodedata.normalize("NFC", policy.version)
    ):
        return _issue(policy, "unsupported", "invalid_policy")
    limits = (policy.max_files, policy.max_file_bytes, policy.max_total_bytes)
    if any(
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
        for limit in limits
    ):
        return _issue(policy, "unsupported", "invalid_policy")
    if not isinstance(root, Path) or not root.is_absolute():
        return _issue(policy, "unsupported", "invalid_root")
    try:
        root_metadata = root.lstat()
    except OSError:
        return _issue(policy, "unrecoverable", "root_unavailable")
    if not is_safe_directory(root_metadata):
        return _issue(policy, "unsupported", "unsafe_root")
    try:
        canonical_root = root.resolve(strict=True)
    except OSError:
        return _issue(policy, "unrecoverable", "root_unavailable")
    if root != canonical_root:
        return _issue(policy, "unsupported", "noncanonical_root")
    if (
        isinstance(selected_relative_paths, (str, bytes))
        or not isinstance(selected_relative_paths, Sequence)
        or not selected_relative_paths
    ):
        return _issue(policy, "empty", "empty_selection")
    selected: list[str] = []
    casefolded: set[str] = set()
    for value in selected_relative_paths:
        relative_path = _canonical_selection(value)
        if relative_path is None:
            return _issue(policy, "unsupported", "invalid_selection")
        folded = relative_path.casefold()
        if folded in casefolded:
            return _issue(policy, "unsupported", "duplicate_selection", relative_path)
        casefolded.add(folded)
        selected.append(relative_path)
    if len(selected) > policy.max_files:
        return _issue(policy, "limit_exceeded", "file_limit")
    entries: list[MaterialSource] = []
    total_bytes = 0
    checked_directories: list[tuple[Path, object]] = [(root, root_metadata)]
    for relative_path in sorted(selected):
        path = root.joinpath(*relative_path.split("/"))
        try:
            current = root
            for component in relative_path.split("/")[:-1]:
                current = current / component
                before = current.lstat()
                if not is_safe_directory(before):
                    return _issue(
                        policy, "unsupported", "unsafe_ancestor", relative_path
                    )
                checked_directories.append((current, before))
            metadata = path.lstat()
        except OSError:
            return _issue(policy, "unrecoverable", "path_unavailable", relative_path)
        if (
            stat.S_ISDIR(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or is_reparse(metadata)
        ):
            return _issue(policy, "unsupported", "unsafe_file", relative_path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return _issue(policy, "unsupported", "unsupported_suffix", relative_path)
        if (
            metadata.st_size > policy.max_file_bytes
            or total_bytes + metadata.st_size > policy.max_total_bytes
        ):
            return _issue(policy, "limit_exceeded", "byte_limit", relative_path)
        stable = read_stable_regular_file(path, metadata, capture_bytes=True)
        if stable is None:
            return _issue(policy, "unstable", "unstable_read", relative_path)
        digest, size, contents = stable
        assert contents is not None
        try:
            text = contents.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _issue(policy, "unsupported", "invalid_utf8", relative_path)
        if not text.strip():
            return _issue(policy, "empty", "empty_content", relative_path)
        if size > policy.max_file_bytes or total_bytes + size > policy.max_total_bytes:
            return _issue(policy, "limit_exceeded", "byte_limit", relative_path)
        entries.append(
            MaterialSource(relative_path, suffix, size, digest, len(text.splitlines()))
        )
        total_bytes += size
    try:
        for directory, expected in checked_directories:
            after = directory.lstat()
            if not is_safe_directory(after):
                return _issue(policy, "unsupported", "unsafe_ancestor")
            if not same_file(expected, after):
                return _issue(policy, "unstable", "directory_changed")
    except OSError:
        return _issue(policy, "unrecoverable", "root_unavailable")
    return _manifest(policy, "complete", entries, total_bytes)
