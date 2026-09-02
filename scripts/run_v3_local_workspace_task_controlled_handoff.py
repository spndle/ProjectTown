"""CLI for the Phase 4D bind-only controlled handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.local_workspace_task_controlled_handoff import (
    ControlledHandoffError,
    create_controlled_handoff,
    load_controlled_handoff,
    verify_controlled_handoff,
)
from scripts._v3_cli_common import CliError, CliParser, canonical_absolute_path

_SCHEMA = "v3-local-workspace-task-controlled-handoff-cli-status-v1"
_CALLS = {
    "provider": 0,
    "image": 0,
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
        "authorization_included": False,
        "offline_calls": _CALLS,
        **fields,
    }


def _parser() -> CliParser:
    parser = CliParser(add_help=True)
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=CliParser
    )
    create = commands.add_parser("create", add_help=True)
    for arg in (
        "--work-root",
        "--material-root",
        "--evidence-root",
        "--task-id",
        "--binding",
        "--plan",
        "--proposal",
        "--handoff-out",
    ):
        create.add_argument(arg, required=True)
    check = commands.add_parser("check", add_help=True)
    for arg in ("--work-root", "--material-root", "--evidence-root", "--handoff"):
        check.add_argument(arg, required=True)
    return parser


def _fields(value: object) -> dict[str, object]:
    return {
        "schema": value.schema_version,
        "state": value.state,
        "handoff_hash": value.handoff_hash,
        "task_id": value.task_id,
        "target_relative_path": value.target_relative_path,
        "handoff_semantics": value.handoff_semantics,
        "create_only": True,
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    work = canonical_absolute_path(args.work_root, "INVALID_WORK_ROOT")
    material = canonical_absolute_path(args.material_root, "INVALID_MATERIAL_ROOT")
    evidence = canonical_absolute_path(args.evidence_root, "INVALID_EVIDENCE_ROOT")
    if args.command == "create":
        binding = canonical_absolute_path(args.binding, "INVALID_BINDING_PATH")
        plan = canonical_absolute_path(args.plan, "INVALID_PLAN_PATH")
        proposal = canonical_absolute_path(args.proposal, "INVALID_PROPOSAL_PATH")
        value = create_controlled_handoff(
            work,
            material,
            evidence,
            task_id=args.task_id,
            binding_path=binding,
            plan_path=plan,
            proposal_path=proposal,
            output=canonical_absolute_path(args.handoff_out, "INVALID_OUTPUT_PATH"),
        )
        return _status("create", "ok", "HANDOFF_CREATED", **_fields(value))
    path = canonical_absolute_path(args.handoff, "INVALID_HANDOFF_PATH")
    value = load_controlled_handoff(path)
    if not verify_controlled_handoff(work, material, evidence, path):
        raise CliError("HANDOFF_BLOCKED")
    return _status("check", "ok", "HANDOFF_VERIFIED", **_fields(value))


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    command = raw[0] if raw and raw[0] in {"create", "check"} else "unknown"
    try:
        args = _parser().parse_args(raw)
        command = args.command
        status, exit_code = _run(args), 0
    except ControlledHandoffError as error:
        outcome, exit_code = (
            ("attention_required", 3)
            if error.code == "COMMITTED_NEEDS_ATTENTION"
            else ("rejected", 2)
        )
        status = _status(command, outcome, error.code)
    except (CliError, OSError, TypeError, ValueError) as error:
        status, exit_code = (
            _status(command, "rejected", getattr(error, "code", "REJECTED")),
            2,
        )
    sys.stdout.write(
        json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
