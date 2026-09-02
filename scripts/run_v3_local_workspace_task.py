"""Create or verify a read-only local workspace task binding."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.local_workspace_task import (
    LocalWorkspaceTaskError,
    load_binding,
    make_binding,
    publish_binding,
    sha256,
    verify_binding,
)
from backend.app.local_workspace_task_authoring import (
    AuthoringError,
    build_catalog,
    initialize_work_root,
)
from backend.app.material_workflow import (
    DraftSession,
    MaterialWorkflowError,
    ResultSession,
    load_external_session,
    serialize_session,
    verify_result,
)
from backend.app.safe_files import is_safe_directory


def _path(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise argparse.ArgumentTypeError("absolute path required")
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        raise argparse.ArgumentTypeError("existing canonical path required") from error


def _id(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise argparse.ArgumentTypeError("64 lowercase hexadecimal characters required")
    return value


def _output(value: dict[str, str]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _create(args: argparse.Namespace) -> dict[str, str]:
    work = args.ui_work_root
    if not is_safe_directory(work.lstat()) or not is_safe_directory(
        (work / "bindings").lstat()
    ):
        raise LocalWorkspaceTaskError("INVALID_WORK_ROOT")
    material = args.material_root
    if (
        material == work
        or material.is_relative_to(work)
        or work.is_relative_to(material)
    ):
        raise LocalWorkspaceTaskError("ROOT_SEPARATION_REQUIRED")
    draft, result = (
        load_external_session(args.draft),
        load_external_session(args.result),
    )
    if (
        not isinstance(draft, DraftSession)
        or not isinstance(result, ResultSession)
        or serialize_session(draft) != serialize_session(result.draft)
        or not verify_result(material, result)
    ):
        raise LocalWorkspaceTaskError("MATERIAL_STALE_OR_MISMATCH")
    metadata = work.lstat()
    binding = make_binding(
        task_id=args.task_id,
        ui_work_root=str(work),
        ui_work_root_device=int(metadata.st_dev),
        ui_work_root_inode=int(metadata.st_ino),
        material_root=str(material),
        draft_path=str(args.draft),
        draft_sha256=sha256(args.draft.read_bytes()),
        result_path=str(args.result),
        result_sha256=sha256(args.result.read_bytes()),
        artifact_kind=result.draft.artifact_kind,
        task_label=args.task_label,
    )
    publish_binding(work, binding)
    return {"status": "CREATED", "task_id": args.task_id}


def _check(args: argparse.Namespace) -> dict[str, str]:
    binding = load_binding(args.ui_work_root / "bindings" / f"{args.task_id}.json")
    if binding.task_id != args.task_id:
        raise LocalWorkspaceTaskError("INVALID_BINDING")
    verify_binding(binding, args.ui_work_root)
    return {"status": "CHECKED", "task_id": args.task_id}


def _authoring_init(args: argparse.Namespace) -> dict[str, str]:
    """Initialize only the fixed, external authoring record directories.

    The operator supplies roots to this offline setup command; those values are
    intentionally never echoed in its stable JSON output.
    """
    initialize_work_root(args.ui_work_root, args.material_root)
    return {"status": "AUTHORING_INITIALIZED"}


def _authoring_check(args: argparse.Namespace) -> dict[str, str]:
    """Verify safe roots, fixed directories, and a readable material catalog."""
    required = (
        "catalogs",
        "requests",
        "intents",
        "receipts",
        "drafts",
        "results",
        "exports",
        "bindings",
        "authoring-bindings",
    )
    work, material = args.ui_work_root, args.material_root
    if (
        work == material
        or work.is_relative_to(material)
        or material.is_relative_to(work)
        or not is_safe_directory(work.lstat())
        or not is_safe_directory(material.lstat())
        or any(
            not (work / name).is_dir()
            or not is_safe_directory((work / name).lstat())
            or (work / name).resolve(strict=True) != work / name
            for name in required
        )
    ):
        raise LocalWorkspaceTaskError("INVALID_AUTHORING_ROOT")
    build_catalog(material)
    return {"status": "AUTHORING_CHECKED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "check"):
        child = commands.add_parser(name)
        child.add_argument("--ui-work-root", required=True, type=_path)
        child.add_argument("--task-id", required=True, type=_id)
        if name == "create":
            child.add_argument("--material-root", required=True, type=_path)
            child.add_argument("--draft", required=True, type=_path)
            child.add_argument("--result", required=True, type=_path)
            child.add_argument("--task-label", required=True)
    for name in ("authoring-init", "authoring-check"):
        child = commands.add_parser(name)
        child.add_argument("--ui-work-root", required=True, type=_path)
        child.add_argument("--material-root", required=True, type=_path)
    args = parser.parse_args(argv)
    try:
        result = {
            "create": _create,
            "check": _check,
            "authoring-init": _authoring_init,
            "authoring-check": _authoring_check,
        }[args.command](args)
        _output(result)
        return 0
    except (
        OSError,
        ValueError,
        MaterialWorkflowError,
        LocalWorkspaceTaskError,
        AuthoringError,
    ) as error:
        _output(
            {"code": getattr(error, "code", "BINDING_REJECTED"), "status": "REJECTED"}
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
