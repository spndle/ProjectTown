"""Disposable, local-only fixtures for the opt-in v3 loopback tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend.app.controlled_write import create_authorization
from backend.app.v3_loopback_records import make_binding, publish_create_only
from tests.controlled_write_support import ready

OPERATION_ID = "b" * 64


def loopback_ready(tmp_path: Path) -> dict[str, object]:
    value = ready(tmp_path)
    root = value["root"]
    assert isinstance(root, Path)
    work = tmp_path / "loopback-work"
    (work / "bindings").mkdir(parents=True)
    (work / "idempotency").mkdir()
    (work / "ledger").mkdir()
    authorization_path = work / "authorization.json"
    authorization = create_authorization(
        root,
        value["result"],  # type: ignore[arg-type]
        value["target"],  # type: ignore[arg-type]
        value["plan"],  # type: ignore[arg-type]
        value["proposal_path"],  # type: ignore[arg-type]
        work / "ledger",
        authorization_path,
        "loopback-operation-001",
        "b" * 32,
    )
    raw = authorization_path.read_bytes()
    metadata = work.lstat()
    binding = make_binding(
        web_operation_id=OPERATION_ID,
        work_root=str(work),
        work_root_device=int(metadata.st_dev),
        work_root_inode=int(metadata.st_ino),
        authorization_path=str(authorization_path),
        authorization_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        authorization_hash=authorization.authorization_hash,
        authorization_schema_version=authorization.schema_version,
        controlled_operation_id=authorization.operation_id,
        material_root=str(root),
        target_relative_path=authorization.target_relative_path,
        target_path_sha256=hashlib.sha256(
            authorization.target_path.encode()
        ).hexdigest(),
        target_display=authorization.target_relative_path,
        allowed_mutations=("apply", "reconcile"),
    )
    publish_create_only(
        work / "bindings", work / "bindings" / f"{OPERATION_ID}.json", binding
    )
    value.update(
        {
            "work": work,
            "authorization_path": authorization_path,
            "authorization": authorization,
            "binding": binding,
        }
    )
    return value
