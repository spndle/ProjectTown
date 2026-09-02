"""Write a deterministic offline material-set inspection report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.materials import inspect_material_set
from backend.app.safe_files import is_safe_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--file", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root, output = Path(args.root), Path(args.output)
    if (
        args.root != str(root)
        or args.output != str(output)
        or not root.is_absolute()
        or not output.is_absolute()
        or output.exists()
    ):
        return 2
    try:
        root_metadata = root.lstat()
        canonical_root = root.resolve(strict=True)
        parent_safe = is_safe_directory(output.parent.lstat())
        canonical_parent = output.parent.resolve(strict=True)
    except OSError:
        return 2
    if (
        not is_safe_directory(root_metadata)
        or root != canonical_root
        or not parent_safe
        or output.parent != canonical_parent
    ):
        return 2
    try:
        canonical_parent.relative_to(canonical_root)
    except ValueError:
        pass
    else:
        return 2
    manifest = inspect_material_set(root, args.file)
    data = (
        json.dumps(
            manifest.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except OSError:
        return 2
    return 0 if manifest.status == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
