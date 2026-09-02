"""Create, load, or check one redacted v3 loopback operation binding."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.app import controlled_write
from backend.app.controlled_write import RestoreAuthorization, UserAuthorization
from backend.app.safe_files import (
    is_reparse,
    is_safe_directory,
    read_stable_regular_file,
)
from backend.app.v3_loopback_records import (
    LoopbackRecordError,
    OperationBinding,
    load_record,
    make_binding,
    publish_create_only,
)


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("absolute path required")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise argparse.ArgumentTypeError("existing canonical path required") from error


def _operation_id(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise argparse.ArgumentTypeError("64 lowercase hexadecimal characters required")
    return value


def _output(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _stable_authorization(
    path: Path,
) -> tuple[UserAuthorization | RestoreAuthorization, bytes]:
    try:
        metadata = path.lstat()
        if (
            path.resolve(strict=True) != path
            or path.parent.resolve(strict=True) != path.parent
            or not is_safe_directory(path.parent.lstat())
            or is_reparse(metadata)
        ):
            raise OSError("unsafe authorization")
        stable = read_stable_regular_file(
            path, metadata, capture_bytes=True, require_single_link=True
        )
        if stable is None or stable[2] is None:
            raise OSError("unstable authorization")
        auth = controlled_write.parse_record_bytes(stable[2])
    except (OSError, controlled_write.ControlledWriteError) as error:
        raise controlled_write.ControlledWriteError("INVALID_AUTHORIZATION") from error
    if not isinstance(auth, (UserAuthorization, RestoreAuthorization)):
        raise controlled_write.ControlledWriteError("INVALID_AUTHORIZATION")
    return auth, stable[2]


def _work_root(path: Path) -> tuple[Path, Path]:
    bindings, idempotency = path / "bindings", path / "idempotency"
    if (
        not bindings.is_dir()
        or not idempotency.is_dir()
        or path.is_symlink()
        or bindings.is_symlink()
        or idempotency.is_symlink()
    ):
        raise controlled_write.ControlledWriteError("INVALID_WORK_ROOT")
    return bindings, idempotency


def _create(arguments: argparse.Namespace) -> dict[str, str]:
    work_root: Path = arguments.work_root
    bindings, _ = _work_root(work_root)
    auth_path: Path = arguments.authorization
    auth, raw = _stable_authorization(auth_path)
    controlled_write.check(auth_path, Path(auth.ledger_root))
    material_root = Path(auth.material_root)
    if (
        not auth_path.is_relative_to(work_root)
        or material_root == work_root
        or material_root.is_relative_to(work_root)
        or work_root.is_relative_to(material_root)
    ):
        raise controlled_write.ControlledWriteError("INVALID_AUTHORIZATION_PATH")
    allowed = (
        ("apply", "reconcile")
        if isinstance(auth, UserAuthorization)
        else ("restore", "reconcile")
    )
    binding = make_binding(
        web_operation_id=arguments.web_operation_id,
        work_root=str(work_root),
        work_root_device=int(work_root.lstat().st_dev),
        work_root_inode=int(work_root.lstat().st_ino),
        authorization_path=str(auth_path),
        authorization_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        authorization_hash=auth.authorization_hash,
        authorization_schema_version=auth.schema_version,
        controlled_operation_id=auth.operation_id,
        material_root=auth.material_root,
        target_relative_path=auth.target_relative_path,
        target_path_sha256=hashlib.sha256(auth.target_path.encode()).hexdigest(),
        target_display=auth.target_relative_path,
        allowed_mutations=allowed,
    )
    publish_create_only(
        bindings, bindings / f"{arguments.web_operation_id}.json", binding
    )
    return {"binding_id": arguments.web_operation_id, "status": "CREATED"}


def _load(arguments: argparse.Namespace, *, checked: bool) -> dict[str, str]:
    bindings, _ = _work_root(arguments.work_root)
    record = load_record(bindings / f"{arguments.web_operation_id}.json")
    if (
        not isinstance(record, OperationBinding)
        or record.web_operation_id != arguments.web_operation_id
    ):
        raise LoopbackRecordError("INVALID_BINDING")
    if checked:
        auth, raw = _stable_authorization(Path(record.authorization_path))
        expected_mutations = (
            ("apply", "reconcile")
            if isinstance(auth, UserAuthorization)
            else ("restore", "reconcile")
        )
        if (
            record.work_root != str(arguments.work_root)
            or (record.work_root_device, record.work_root_inode)
            != (
                int(arguments.work_root.lstat().st_dev),
                int(arguments.work_root.lstat().st_ino),
            )
            or not Path(record.authorization_path).is_relative_to(arguments.work_root)
            or hashlib.sha256(raw).hexdigest() != record.authorization_bytes_sha256
            or auth.authorization_hash != record.authorization_hash
            or auth.schema_version != record.authorization_schema_version
            or auth.operation_id != record.controlled_operation_id
            or record.material_root != auth.material_root
            or record.target_relative_path != auth.target_relative_path
            or record.target_display != auth.target_relative_path
            or record.target_path_sha256
            != hashlib.sha256(auth.target_path.encode("utf-8")).hexdigest()
            or record.allowed_mutations != expected_mutations
        ):
            raise LoopbackRecordError("AUTHORIZATION_BINDING_MISMATCH")
        controlled_write.check(Path(record.authorization_path), Path(auth.ledger_root))
    return {
        "binding_id": record.web_operation_id,
        "status": "CHECKED" if checked else "LOADED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "load", "check"):
        child = commands.add_parser(command)
        child.add_argument("--work-root", required=True, type=_path)
        child.add_argument("--web-operation-id", required=True, type=_operation_id)
        if command == "create":
            child.add_argument("--authorization", required=True, type=_path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "create":
            value = _create(arguments)
        else:
            value = _load(arguments, checked=arguments.command == "check")
    except (
        OSError,
        ValueError,
        LoopbackRecordError,
        controlled_write.ControlledWriteError,
    ) as error:
        _output(
            {"code": getattr(error, "code", "BINDING_REJECTED"), "status": "REJECTED"}
        )
        return 2
    _output(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
