"""CLI for Phase 3C controlled local writes.

The CLI deliberately does not accept caller-selected backup or receipt paths.
Those paths are committed in the user or restore authorization before dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.controlled_write import (
    ControlledWriteAttention,
    ControlledWriteError,
    RestoreAuthorization,
    UserAuthorization,
    apply,
    check,
    create_authorization,
    create_restore_authorization,
    load_record,
    reconcile,
    restore,
)

CALLS = {
    "provider": 0,
    "image": 0,
    "embedding": 0,
    "mcp": 0,
    "network": 0,
    "egress": 0,
    "paid": 0,
}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ControlledWriteError("INVALID_ARGUMENTS")


def path(value: str) -> Path:
    parsed = Path(value)
    if not parsed.is_absolute() or str(parsed) != value:
        raise ControlledWriteError("INVALID_PATH")
    return parsed


def _operation_paths(authorization: Path) -> tuple[Path, Path]:
    try:
        record = load_record(authorization)
    except (OSError, ValueError) as error:
        raise ControlledWriteError("INVALID_AUTHORIZATION") from error
    if not isinstance(record, (UserAuthorization, RestoreAuthorization)):
        raise ControlledWriteError("INVALID_AUTHORIZATION")
    return Path(record.backup_path), Path(record.receipt_path)


def parser() -> Parser:
    value = Parser()
    commands = value.add_subparsers(dest="command", required=True)
    authorize = commands.add_parser("authorize")
    for name in (
        "root",
        "proposal",
        "result",
        "target",
        "plan",
        "ledger-root",
        "authorization-out",
        "operation-id",
        "nonce",
    ):
        authorize.add_argument("--" + name, required=True)
    restore_authorize = commands.add_parser("restore-authorize")
    for name in (
        "root",
        "receipt",
        "target",
        "ledger-root",
        "authorization-out",
        "operation-id",
        "nonce",
    ):
        restore_authorize.add_argument("--" + name, required=True)
    checked = commands.add_parser("check")
    for name in ("authorization", "ledger-root"):
        checked.add_argument("--" + name, required=True)
    for command in ("apply", "reconcile", "restore"):
        current = commands.add_parser(command)
        for name in ("root", "authorization", "target", "ledger-root"):
            current.add_argument("--" + name, required=True)
        if command == "apply":
            for name in ("proposal", "result", "plan"):
                current.add_argument("--" + name, required=True)
    return value


def _status(
    *, outcome: str, code: str, write_performed: object, **extra: object
) -> dict[str, object]:
    return {
        "schema_version": "v3-controlled-write-cli-status-v1",
        "outcome": outcome,
        "code": code,
        "write_performed": write_performed,
        "offline_calls": CALLS,
        **extra,
    }


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    try:
        args = parser().parse_args(raw)
        command = args.command
        if command == "authorize":
            record = create_authorization(
                path(args.root),
                path(args.result),
                path(args.target),
                path(args.plan),
                path(args.proposal),
                path(args.ledger_root),
                path(args.authorization_out),
                args.operation_id,
                args.nonce,
            )
            status = _status(
                outcome="ok",
                code="AUTHORIZED",
                write_performed=False,
                operation_id=record.operation_id,
            )
        elif command == "restore-authorize":
            record = create_restore_authorization(
                path(args.root),
                path(args.receipt),
                path(args.target),
                path(args.ledger_root),
                path(args.authorization_out),
                args.operation_id,
                args.nonce,
            )
            status = _status(
                outcome="ok",
                code="RESTORE_AUTHORIZED",
                write_performed=False,
                operation_id=record.operation_id,
            )
        elif command == "check":
            state = check(path(args.authorization), path(args.ledger_root))
            if state in {"RECONCILE_REQUIRED", "TARGET_CHANGED_AFTER_RECEIPT"}:
                raise ControlledWriteAttention(state)
            status = _status(
                outcome="ok",
                code=state,
                write_performed=False,
            )
        else:
            authorization = path(args.authorization)
            backup, receipt = _operation_paths(authorization)
            if command == "apply":
                record = apply(
                    path(args.root),
                    authorization,
                    path(args.result),
                    path(args.proposal),
                    path(args.target),
                    path(args.plan),
                    path(args.ledger_root),
                    backup,
                    receipt,
                )
                performed: object = True
            elif command == "reconcile":
                record = reconcile(
                    path(args.root),
                    authorization,
                    path(args.target),
                    path(args.ledger_root),
                    backup,
                    receipt,
                )
                performed = False
            else:
                record = restore(
                    path(args.root),
                    authorization,
                    path(args.target),
                    path(args.ledger_root),
                    backup,
                    receipt,
                )
                performed = True
            status = _status(
                outcome="ok",
                code=record.state,
                write_performed=performed,
                operation_id=record.operation_id,
            )
        exit_code = 0
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2
    except ControlledWriteAttention as error:
        status = _status(
            outcome="attention_required",
            code=error.code,
            # Dispatch may have occurred, so do not falsely claim no write.
            write_performed="unknown",
        )
        exit_code = 3
    except (ControlledWriteError, OSError, ValueError) as error:
        status = _status(
            outcome="rejected",
            code=getattr(error, "code", "REJECTED"),
            write_performed=False,
        )
        exit_code = 2
    print(json.dumps(status, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
