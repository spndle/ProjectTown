"""Create or check the additive Phase 2 closeout receipt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.phase2_closeout import (
    Phase2CloseoutError,
    create_closeout,
    load_closeout,
    publish_closeout,
    verify_closeout,
)


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        raise Phase2CloseoutError("INVALID_ARGUMENTS")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "check"):
        item = commands.add_parser(name)
        item.add_argument("--receipt-root", required=True)
        item.add_argument("--t001-study-root", required=True)
        item.add_argument("--t001-work-root", required=True)
        item.add_argument("--t002-study-root", required=True)
        item.add_argument("--t002-work-root", required=True)
        if name == "create":
            item.add_argument("--record-created-on", required=True)
    return parser


def _status(
    command: str, outcome: str, code: str, **fields: object
) -> dict[str, object]:
    return {
        "schema_version": "v3-phase2-closeout-cli-v1",
        "command": command,
        "outcome": outcome,
        "code": code,
        "offline_calls": {
            "provider": 0,
            "embedding": 0,
            "mcp": 0,
            "network": 0,
            "egress": 0,
            "paid": 0,
        },
        "paths_disclosed": False,
        **fields,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        roots = tuple(
            _path(getattr(args, key))
            for key in (
                "receipt_root",
                "t001_study_root",
                "t001_work_root",
                "t002_study_root",
                "t002_work_root",
            )
        )
        if args.command == "create":
            receipt = create_closeout(*roots, record_created_on=args.record_created_on)
            publish_closeout(roots[0], receipt)
        else:
            receipt = load_closeout(roots[0])
            if not verify_closeout(receipt, *roots[1:]):
                raise Phase2CloseoutError("CLOSEOUT_MISMATCH")
        status, exit_code = (
            _status(
                args.command,
                "ok",
                "CHECKED" if args.command == "check" else "CREATED",
                receipt_hash=receipt.receipt_hash,
                schema_version=receipt.schema_version,
                rounds=2,
                legacy_limitation=receipt.legacy_limitation,
            ),
            0,
        )
    except Phase2CloseoutError as error:
        attention = error.code == "COMMITTED_NEEDS_ATTENTION"
        rolled_back = error.code == "PUBLICATION_ROLLED_BACK"
        fields: dict[str, object] = {}
        if attention:
            fields["publication_state"] = "committed_needs_attention"
        elif rolled_back:
            fields["publication_state"] = "rolled_back"
        status, exit_code = (
            _status(
                getattr(locals().get("args", None), "command", "unknown"),
                "attention_required" if attention else "rejected",
                error.code,
                **fields,
            ),
            3 if attention else 2,
        )
    except (ValueError, OSError) as error:
        status, exit_code = (
            _status(
                getattr(locals().get("args", None), "command", "unknown"),
                "rejected",
                getattr(error, "code", "REJECTED"),
            ),
            2,
        )
    sys.stdout.write(
        json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
