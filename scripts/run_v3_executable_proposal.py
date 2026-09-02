"""CLI for Phase 3B read-only complete post-image proposals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from backend.app.executable_proposal import (
    ExecutableProposalError,
    create_executable_proposal,
    load_executable_proposal,
    verify_executable_proposal,
)
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
)
from scripts._v3_cli_common import CliError as _CliError
from scripts._v3_cli_common import CliParser as _Parser
from scripts._v3_cli_common import canonical_absolute_path as _path

_SCHEMA = "v3-executable-proposal-cli-status-v1"
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
        "offline_calls": _CALLS,
        **fields,
    }


def _build_parser() -> _Parser:
    parser = _Parser(add_help=True)
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_Parser
    )
    for command, output in (("create", "--proposal-out"), ("check", "--proposal")):
        item = commands.add_parser(command, add_help=True)
        for arg in ("--root", "--result", "--target", "--plan", output):
            item.add_argument(arg, required=True)
    return parser


def _fields(proposal: object) -> dict[str, object]:
    return {
        "schema": proposal.schema_version,
        "state": proposal.state,
        "proposal_hash": proposal.proposal_hash,
        "post_image_sha256": proposal.post_image_sha256,
        "post_image_size_bytes": proposal.post_image_size_bytes,
        "target_relative_path": proposal.target_relative_path,
        "deferred_gates": list(proposal.deferred_gates),
        "create_only": True,
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    root, result, target, plan = (
        _path(args.root, "INVALID_ROOT"),
        _path(args.result, "INVALID_SESSION_PATH"),
        _path(args.target, "INVALID_TARGET_PATH"),
        _path(args.plan, "INVALID_PLAN_PATH"),
    )
    if args.command == "create":
        value = create_executable_proposal(
            root, result, target, plan, _path(args.proposal_out, "INVALID_OUTPUT_PATH")
        )
        return _status("create", "ok", "PROPOSAL_CREATED", **_fields(value))
    value = load_executable_proposal(
        _path(args.proposal, "INVALID_PROPOSAL_PATH"), material_root=root
    )
    if not verify_executable_proposal(root, value, result, target, plan):
        raise _CliError("PROPOSAL_BLOCKED")
    return _status("check", "ok", "PROPOSAL_VERIFIED", **_fields(value))


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    command = raw[0] if raw and raw[0] in {"create", "check"} else "unknown"
    try:
        args = _build_parser().parse_args(raw)
        command = args.command
        status, code = _run(args), 0
    except PublicationAttentionError as error:
        status, code = _status(command, "attention_required", error.code), 3
    except PublicationRollbackError as error:
        status, code = _status(command, "rejected", error.code), 2
    except (ExecutableProposalError, _CliError) as error:
        status, code = (
            _status(command, "rejected", getattr(error, "code", "REJECTED")),
            2,
        )
    except (OSError, TypeError, ValueError):
        status, code = _status(command, "rejected", "REJECTED"), 2
    sys.stdout.write(
        json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
