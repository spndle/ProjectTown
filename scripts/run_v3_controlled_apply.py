"""CLI for Phase 3A read-only ApplyPlan preparation and checking only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.controlled_apply import (
    ControlledApplyError,
    load_apply_plan,
    prepare_apply_plan,
    verify_apply_plan,
)
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
)
from scripts._v3_cli_common import CliError as _CliError
from scripts._v3_cli_common import CliParser as _Parser
from scripts._v3_cli_common import canonical_absolute_path as _path

_SCHEMA = "v3-controlled-apply-cli-status-v1"
_CALLS = {
    "provider": 0,
    "embedding": 0,
    "mcp": 0,
    "network": 0,
    "egress": 0,
    "paid": 0,
}


def _status(
    command: str, outcome: str, code: str, **fields: object
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA,
        "command": command,
        "outcome": outcome,
        "code": code,
        "write_performed": False,
        "offline_calls": _CALLS,
        **fields,
    }


def _build_parser() -> _Parser:
    parser = _Parser(add_help=True)
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_Parser
    )
    prepare = commands.add_parser("prepare", add_help=True)
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--result", required=True)
    prepare.add_argument("--target", required=True)
    prepare.add_argument("--plan-out", required=True)
    check = commands.add_parser("check", add_help=True)
    check.add_argument("--root", required=True)
    check.add_argument("--result", required=True)
    check.add_argument("--target", required=True)
    check.add_argument("--plan", required=True)
    return parser


def _run(args: argparse.Namespace) -> dict[str, object]:
    root = _path(args.root, "INVALID_ROOT")
    result = _path(args.result, "INVALID_SESSION_PATH")
    target = _path(args.target, "INVALID_TARGET_PATH")
    if args.command == "prepare":
        plan = prepare_apply_plan(
            root, result, target, _path(args.plan_out, "INVALID_OUTPUT_PATH")
        )
        return _status(
            "prepare",
            "ok",
            "PLAN_PREPARED",
            schema=plan.schema_version,
            state=plan.state,
            plan_hash=plan.plan_hash,
            target_relative_path=plan.target_relative_path,
            deferred_gates=list(plan.deferred_gates),
            create_only=True,
        )
    plan = load_apply_plan(_path(args.plan, "INVALID_PLAN_PATH"), material_root=root)
    if not verify_apply_plan(root, plan, result, target):
        raise _CliError("PREFLIGHT_BLOCKED")
    return _status(
        "check",
        "ok",
        "PREFLIGHT_VERIFIED",
        schema=plan.schema_version,
        state=plan.state,
        plan_hash=plan.plan_hash,
        target_relative_path=plan.target_relative_path,
        deferred_gates=list(plan.deferred_gates),
    )


def main(argv: list[str] | None = None) -> int:
    raw_args = sys.argv[1:] if argv is None else argv
    command = (
        raw_args[0] if raw_args and raw_args[0] in {"prepare", "check"} else "unknown"
    )
    try:
        args = _build_parser().parse_args(raw_args)
        command = args.command
        status, exit_code = _run(args), 0
    except PublicationAttentionError as error:
        status, exit_code = _status(command, "attention_required", error.code), 3
    except PublicationRollbackError as error:
        status, exit_code = _status(command, "rejected", error.code), 2
    except (ControlledApplyError, _CliError) as error:
        status, exit_code = (
            _status(command, "rejected", getattr(error, "code", "REJECTED")),
            2,
        )
    except (OSError, TypeError, ValueError):
        status, exit_code = _status(command, "rejected", "REJECTED"), 2
    sys.stdout.write(
        json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
