"""Offline, create-only command line interface for Phase 1 material workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.material_workflow import (
    DraftSession,
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    ResultSession,
    create_draft,
    generate_result,
    load_external_session,
    load_session,
    publish_new_file,
    render_export,
    render_pdf_export,
    render_preview,
    revalidate_result_sources,
    serialize_session,
    verify_result_integrity,
)
from scripts._v3_cli_common import CliError as _CliError
from scripts._v3_cli_common import CliParser as _Parser
from scripts._v3_cli_common import canonical_absolute_path as _path

_SCHEMA = "v3-material-cli-status-v1"
_CALLS = {"provider": 0, "embedding": 0, "mcp": 0}


def _constraints(values: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    folded: set[str] = set()
    for value in values or []:
        if "=" not in value:
            raise _CliError("INVALID_CONSTRAINTS")
        key, item = value.split("=", 1)
        if not key or not item or key.casefold() in folded:
            raise _CliError("INVALID_CONSTRAINTS")
        folded.add(key.casefold())
        parsed[key] = item
    return parsed


def _status(
    command: str, outcome: str, code: str, **fields: object
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA,
        "command": command,
        "outcome": outcome,
        "code": code,
        "integrity": "not_checked",
        "freshness": "not_checked",
        "confirmation_provenance": "not_checked",
        "outputs": {"draft": False, "result": False, "export": False, "pdf": False},
        "offline_calls": _CALLS,
        **fields,
    }


def _session_fields(session: DraftSession | ResultSession) -> dict[str, object]:
    fields: dict[str, object] = {
        "session_kind": "draft" if isinstance(session, DraftSession) else "result",
        "state": session.state,
        "session_hash": session.session_hash,
    }
    if isinstance(session, DraftSession):
        fields["contract_hash"] = session.contract_hash
        fields["artifact_kind"] = session.artifact_kind
        fields["selection_count"] = len(session.selections)
        if session.readme_target is not None:
            fields["readme_target"] = session.readme_target
    else:
        fields["contract_hash"] = session.confirmed_contract_hash
        fields["artifact_hash"] = session.artifact_hash
        fields["preview_hash"] = session.preview_hash
    return fields


def _has_root(value: str | bytes, root: Path) -> bool:
    data = value if isinstance(value, str) else value.decode("utf-8", "strict")
    return str(root).casefold() in data.casefold()


def _publish_result(root: Path, output: Path, result: ResultSession) -> None:
    # The draft task is user input and intentionally excluded from this check.
    if _has_root(result.artifact_markdown, root) or _has_root(
        result.preview_markdown, root
    ):
        raise _CliError("ABSOLUTE_PATH_IN_OUTPUT")
    publish_new_file(root, output, serialize_session(result))


def _build_parser() -> _Parser:
    parser = _Parser(add_help=True)
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_Parser
    )
    draft = commands.add_parser("draft", add_help=True)
    draft.add_argument("--root", required=True)
    draft.add_argument("--file", action="append", required=True)
    draft.add_argument("--task", required=True)
    draft.add_argument(
        "--artifact-kind", required=True, choices=("plan", "report", "readme")
    )
    draft.add_argument("--readme-target")
    draft.add_argument("--constraint", action="append")
    draft.add_argument(
        "--generator-version",
        default="deterministic-grounded-plan-v2",
        choices=(
            "deterministic-grounded-plan-v2",
            "deterministic-grounded-plan-v3",
            "deterministic-grounded-plan-v4",
            "deterministic-grounded-plan-v5",
            "deterministic-grounded-plan-v6",
            "deterministic-grounded-plan-v7",
            "deterministic-grounded-plan-v8",
            "deterministic-grounded-plan-v9",
        ),
    )
    draft.add_argument("--draft-out", required=True)
    generate = commands.add_parser("generate", add_help=True)
    generate.add_argument("--root", required=True)
    generate.add_argument("--draft", required=True)
    generate.add_argument("--confirmation-hash", required=True)
    generate.add_argument("--result-out", required=True)
    check = commands.add_parser("check", add_help=True)
    check.add_argument("--root", required=True)
    check.add_argument("--session", required=True)
    preview = commands.add_parser("preview", add_help=True)
    preview.add_argument("--result", required=True)
    preview.add_argument("--root")
    export = commands.add_parser("export", add_help=True)
    export.add_argument("--root", required=True)
    export.add_argument("--result", required=True)
    export.add_argument("--export-out", required=True)
    pdf_export = commands.add_parser("pdf-export", add_help=True)
    pdf_export.add_argument("--root", required=True)
    pdf_export.add_argument("--result", required=True)
    pdf_export.add_argument("--pdf-out", required=True)
    pdf_export.add_argument(
        "--pdf-export-version",
        default="v3-material-pdf-export-v1",
        choices=(
            "v3-material-pdf-export-v1",
            "v3-material-pdf-export-v2",
            "v3-material-pdf-export-v3",
            "v3-material-pdf-export-v4",
            "v3-material-pdf-export-v5",
            "v3-material-pdf-export-v6",
            "v3-material-pdf-export-v7",
            "v3-material-pdf-export-v8",
            "v3-material-pdf-export-v9",
        ),
    )
    return parser


def _run(args: argparse.Namespace) -> dict[str, object]:
    command = args.command
    if command == "draft":
        root, output = (
            _path(args.root, "INVALID_ROOT"),
            _path(args.draft_out, "INVALID_OUTPUT_PATH"),
        )
        session = create_draft(
            root,
            args.file,
            task=args.task,
            artifact_kind=args.artifact_kind,
            readme_target=args.readme_target,
            constraints=_constraints(args.constraint),
            generator_version=args.generator_version,
        )
        publish_new_file(root, output, serialize_session(session))
        return _status(
            command,
            "ok",
            "DRAFT_CREATED",
            **_session_fields(session),
            confirmation_provenance="not_confirmed",
            outputs={"draft": True, "result": False, "export": False, "pdf": False},
            user_guidance="Draft JSON is an engineering record. Confirm it, then generate a user preview and PDF.",
        )
    if command == "generate":
        root, draft_path, output = (
            _path(args.root, "INVALID_ROOT"),
            _path(args.draft, "INVALID_SESSION_PATH"),
            _path(args.result_out, "INVALID_OUTPUT_PATH"),
        )
        draft = load_session(root, draft_path)
        if not isinstance(draft, DraftSession):
            raise _CliError("WRONG_SESSION_KIND")
        result = generate_result(root, draft, args.confirmation_hash)
        _publish_result(root, output, result)
        return _status(
            command,
            "ok",
            "RESULT_CREATED",
            **_session_fields(result),
            integrity="self_consistent",
            freshness="fresh",
            confirmation_provenance="explicit_current_invocation",
            outputs={"draft": False, "result": True, "export": False, "pdf": False},
            user_guidance="Result JSON is an engineering record. Use preview for readable content, then pdf-export for the user deliverable.",
        )
    if command == "check":
        root, session_path = (
            _path(args.root, "INVALID_ROOT"),
            _path(args.session, "INVALID_SESSION_PATH"),
        )
        session = load_session(root, session_path)
        integrity = (
            "self_consistent"
            if (
                not isinstance(session, ResultSession)
                or verify_result_integrity(session)
            )
            else "invalid"
        )
        freshness = (
            "not_checked"
            if not isinstance(session, ResultSession)
            else (
                "fresh"
                if revalidate_result_sources(root, session)
                else "stale_or_unavailable"
            )
        )
        return _status(
            command,
            "ok",
            "CHECKED",
            **_session_fields(session),
            integrity=integrity,
            freshness=freshness,
            confirmation_provenance=(
                "unanchored_external_session"
                if isinstance(session, ResultSession)
                else "not_confirmed"
            ),
        )
    if command == "preview":
        result_path = _path(args.result, "INVALID_SESSION_PATH")
        result = load_external_session(result_path)
        if not isinstance(result, ResultSession):
            raise _CliError("WRONG_SESSION_KIND")
        freshness = "not_checked"
        if args.root is not None:
            root = _path(args.root, "INVALID_ROOT")
            if not revalidate_result_sources(root, result):
                raise _CliError("STALE_OR_UNAVAILABLE")
            freshness = "fresh"
        return _status(
            command,
            "ok",
            "PREVIEW_READY",
            **_session_fields(result),
            integrity="self_consistent",
            freshness=freshness,
            confirmation_provenance="unanchored_external_session",
            preview_markdown=render_preview(result),
            user_guidance="This is the user-readable frozen preview; JSON is only the engineering record. Use pdf-export with a fresh root to create a PDF.",
        )
    if command == "export":
        root, result_path, output = (
            _path(args.root, "INVALID_ROOT"),
            _path(args.result, "INVALID_SESSION_PATH"),
            _path(args.export_out, "INVALID_OUTPUT_PATH"),
        )
        result = load_session(root, result_path)
        if not isinstance(result, ResultSession):
            raise _CliError("WRONG_SESSION_KIND")
        artifact = render_export(root, result)
        if _has_root(artifact, root):
            raise _CliError("ABSOLUTE_PATH_IN_OUTPUT")
        publish_new_file(root, output, artifact)
        return _status(
            command,
            "ok",
            "EXPORTED",
            **_session_fields(result),
            integrity="self_consistent",
            freshness="fresh",
            confirmation_provenance="unanchored_external_session",
            outputs={"draft": False, "result": False, "export": True, "pdf": False},
            user_guidance="Markdown export is create-only. Use pdf-export for the primary user-readable PDF deliverable.",
        )
    if command == "pdf-export":
        root, result_path, output = (
            _path(args.root, "INVALID_ROOT"),
            _path(args.result, "INVALID_SESSION_PATH"),
            _path(args.pdf_out, "INVALID_OUTPUT_PATH"),
        )
        result = load_session(root, result_path)
        if not isinstance(result, ResultSession):
            raise _CliError("WRONG_SESSION_KIND")
        artifact = render_pdf_export(
            root, result, export_version=args.pdf_export_version
        )
        publish_new_file(root, output, artifact)
        return _status(
            command,
            "ok",
            "PDF_EXPORTED",
            **_session_fields(result),
            integrity="self_consistent",
            freshness="fresh",
            confirmation_provenance="unanchored_external_session",
            outputs={"draft": False, "result": False, "export": False, "pdf": True},
            pdf_path=str(output),
            pdf_export_version=args.pdf_export_version,
            create_only=True,
            user_guidance="PDF is the user-readable deliverable. JSON remains an engineering record; this PDF was created only after fresh source and conflict checks.",
        )
    raise _CliError()


def main(argv: list[str] | None = None) -> int:
    raw_args = sys.argv[1:] if argv is None else argv
    command = (
        raw_args[0]
        if raw_args
        and raw_args[0]
        in {"draft", "generate", "check", "preview", "export", "pdf-export"}
        else "unknown"
    )
    try:
        args = _build_parser().parse_args(raw_args)
        command = args.command
        status = _run(args)
        exit_code = 0
    except PublicationAttentionError as error:
        status = _status(
            command,
            "attention_required",
            error.code,
            publication_state="committed_needs_attention",
        )
        exit_code = 3
    except PublicationRollbackError as error:
        status = _status(
            command, "rejected", error.code, publication_state="rolled_back"
        )
        exit_code = 2
    except (MaterialWorkflowError, _CliError) as error:
        status = _status(command, "rejected", getattr(error, "code", "REJECTED"))
        exit_code = 2
    except (OSError, TypeError, ValueError):
        status = _status(command, "rejected", "REJECTED")
        exit_code = 2
    sys.stdout.write(
        json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
