"""Phase 3B: deterministic, read-only complete README post-image proposals.

The record deliberately contains the *entire* proposed target bytes.  It is not
an apply operation: the target is only read and every later write/backup/user
gate remains explicitly deferred.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import stat
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .controlled_apply import (
    ApplyPlan,
    ControlledApplyError,
    load_apply_plan,
    verify_apply_plan,
)
from .material_workflow import (
    MaterialWorkflowError,
    PublicationAttentionError,
    PublicationRollbackError,
    ResultSession,
    load_session,
    publish_new_file,
    revalidate_result_sources,
    serialize_session,
    verify_result_integrity,
)
from .safe_files import is_reparse, is_safe_directory, read_stable_regular_file

PROPOSAL_SCHEMA_VERSION = "v3-material-executable-proposal-v1"
PROPOSAL_HASH_DOMAIN = "projecttown/v3/material-executable-proposal/v1"
POST_IMAGE_PRODUCER_VERSION = "projecttown-readme-append-composer-v1"
_STATE = "proposal_complete_awaiting_apply_authorization"
_MAX_POST_IMAGE_BYTES = 1_048_576
_MAX_PROPOSAL_BYTES = 2 * _MAX_POST_IMAGE_BYTES
_MARKER_PREFIX = "projecttown:managed-readme-section:v1"
_START_MARKER = f"<!-- {_MARKER_PREFIX}:start -->"
_END_MARKER = f"<!-- {_MARKER_PREFIX}:end -->"
_DEFERRED_GATES = (
    "explicit_user_authorization",
    "backup_and_compare_and_swap",
    "write_receipt_and_recovery",
)
_BIDI = (
    {0x061C, 0x200E, 0x200F} | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
)
_ABSOLUTE_PATH = re.compile(r"(?:(?:[A-Za-z]:[\\/])|(?:\\\\|//)|/)[^\s`<>]+")


class ExecutableProposalError(ValueError):
    """Stable, path-free rejection for the Phase 3B contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("executable proposal rejected")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExecutableProposal(_Model):
    schema_version: Literal["v3-material-executable-proposal-v1"]
    hash_domain: Literal["projecttown/v3/material-executable-proposal/v1"]
    state: Literal["proposal_complete_awaiting_apply_authorization"]
    producer_version: Literal["projecttown-readme-append-composer-v1"]
    composition_version: Literal["append-original-prefix-v1"]
    diff_version: Literal["projecttown-unified-diff-display-v1"]
    proposal_semantics: Literal["complete_post_image_bytes_not_executed"]
    write_performed: Literal[False]
    apply_plan_schema_version: Literal["v3-material-apply-plan-v1"]
    apply_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    apply_plan_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_schema_version: Literal["v3-material-result-session-v1"]
    result_session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_relative_path: str = Field(min_length=1, max_length=4096)
    target_before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_before_size_bytes: int = Field(ge=0)
    target_before_device: int = Field(ge=0)
    target_before_inode: int = Field(ge=0)
    selected_scope: tuple[str, ...] = Field(min_length=1)
    proposed_write_scope: tuple[str, ...]
    scope_guard: Literal["append_only_after_original_bytes"]
    encoding: Literal["utf-8"]
    bom_policy: Literal["preserve-leading-utf8-bom"]
    newline_policy: Literal["inherit-crlf-or-use-lf"]
    unicode_policy: Literal["original-bytes-preserved-generated-nfc"]
    terminal_newline_policy: Literal["generated-section-exactly-one-eol"]
    marker_version: Literal["projecttown:managed-readme-section:v1"]
    append_offset_bytes: int = Field(ge=0)
    appended_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    appended_bytes_size: int = Field(gt=0)
    post_image_base64: str = Field(min_length=1)
    post_image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_image_size_bytes: int = Field(gt=0, le=_MAX_POST_IMAGE_BYTES)
    display_diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_diff_size_bytes: int = Field(ge=0)
    deferred_gates: tuple[str, ...]
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_relative(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != unicodedata.normalize("NFC", value)
    ):
        return False
    if "\x00" in value or "\\" in value or value.startswith("/") or ":" in value:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _proposal_hash(payload: dict[str, object]) -> str:
    return _sha(
        PROPOSAL_HASH_DOMAIN.encode("ascii") + b"\x00" + _canonical_json(payload)
    )


def _safe_external(path: Path, root: Path, code: str) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ExecutableProposalError(code)
    try:
        meta, parent = path.lstat(), path.parent.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise ExecutableProposalError(code) from error
    if (
        canonical != path
        or not stat.S_ISREG(meta.st_mode)
        or is_reparse(meta)
        or not is_safe_directory(parent)
    ):
        raise ExecutableProposalError(code)
    try:
        canonical.relative_to(root)
    except ValueError:
        pass
    else:
        raise ExecutableProposalError(code)
    stable = read_stable_regular_file(
        path, meta, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise ExecutableProposalError("UNSTABLE_PROPOSAL")
    return stable[2]


def _validate_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ExecutableProposalError("INVALID_ROOT")
    try:
        metadata = root.lstat()
        canonical = root.resolve(strict=True)
    except OSError as error:
        raise ExecutableProposalError("ROOT_UNAVAILABLE") from error
    if canonical != root or not is_safe_directory(metadata):
        raise ExecutableProposalError("INVALID_ROOT")
    return root


def _target_bytes(root: Path, target: Path, plan: ApplyPlan) -> tuple[bytes, int, int]:
    if not isinstance(target, Path) or not target.is_absolute():
        raise ExecutableProposalError("INVALID_TARGET_PATH")
    expected = root.joinpath(*plan.target_relative_path.split("/"))
    try:
        meta = target.lstat()
        canonical, wanted = target.resolve(strict=True), expected.resolve(strict=True)
    except OSError as error:
        raise ExecutableProposalError("TARGET_UNAVAILABLE") from error
    if (
        canonical != target
        or canonical != wanted
        or not stat.S_ISREG(meta.st_mode)
        or is_reparse(meta)
    ):
        raise ExecutableProposalError("INVALID_TARGET_PATH")
    stable = read_stable_regular_file(
        target, meta, capture_bytes=True, require_single_link=True
    )
    if stable is None or stable[2] is None:
        raise ExecutableProposalError("UNSTABLE_TARGET")
    data = stable[2]
    if (_sha(data), len(data), int(meta.st_dev), int(meta.st_ino)) != (
        plan.target_sha256,
        plan.target_size_bytes,
        plan.target_device,
        plan.target_inode,
    ):
        raise ExecutableProposalError("TARGET_BINDING_CHANGED")
    return data, int(meta.st_dev), int(meta.st_ino)


def _validate_original(data: bytes) -> tuple[bytes, str]:
    if len(data) > _MAX_POST_IMAGE_BYTES:
        raise ExecutableProposalError("POST_IMAGE_LIMIT_EXCEEDED")
    if data.count(b"\xef\xbb\xbf") > (1 if data.startswith(b"\xef\xbb\xbf") else 0):
        raise ExecutableProposalError("INVALID_BOM")
    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ExecutableProposalError("INVALID_UTF8") from error
    if _MARKER_PREFIX in text:
        raise ExecutableProposalError("MANAGED_MARKER_PRESENT")
    if any(
        ord(ch) in _BIDI
        or ord(ch) == 0
        or (ord(ch) < 32 and ch not in "\n\r\t")
        or 127 <= ord(ch) <= 159
        for ch in text
    ):
        raise ExecutableProposalError("UNSAFE_ORIGINAL_TEXT")
    bare_cr = re.search(r"\r(?!\n)", text)
    if bare_cr or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise ExecutableProposalError("MIXED_NEWLINES")
    return data[:3] if data.startswith(
        b"\xef\xbb\xbf"
    ) else b"", "\r\n" if "\r\n" in text else "\n"


def _inert(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = _ABSOLUTE_PATH.sub("[local-path-redacted]", value)
    value = "".join(
        " "
        if ord(ch) in _BIDI
        or ord(ch) == 0xFEFF
        or ord(ch) < 32
        and ch not in "\n\t"
        or 127 <= ord(ch) <= 159
        else ch
        for ch in value
    )
    return value.replace("`", "\\`").replace("\t", " ").replace("\n", " ").strip()


def _section(result: ResultSession, eol: str) -> bytes:
    if not result.citations:
        raise ExecutableProposalError("PROPOSAL_NOT_GROUNDED")
    lines = [
        _START_MARKER,
        "## Task-driven update proposal",
        "",
        f"**Task:** {_inert(result.draft.task)}",
        "",
        "### Source-grounded statements",
    ]
    for citation in result.citations:
        displayed_path = _inert(citation.relative_path)
        lines.append(
            f"- [{citation.id}] {_inert(citation.preview)} ({displayed_path}, lines {citation.line_start}-{citation.line_end})"
        )
    if result.draft.constraints:
        lines += ["", "### Declared constraints"]
        lines += [
            f"- {_inert(key)}: {_inert(value)}"
            for key, value in result.draft.constraints
        ]
    lines += [_END_MARKER, ""]
    return unicodedata.normalize("NFC", eol.join(lines)).encode("utf-8")


def _compose(before: bytes, result: ResultSession) -> tuple[bytes, bytes, str]:
    _bom, eol = _validate_original(before)
    appended = _section(result, eol)
    # Preserve every original byte; append enough selected EOLs to make a blank line.
    separator = eol.encode("ascii") * (2 if not before.endswith((b"\n", b"\r")) else 1)
    post = before + separator + appended
    if len(post) > _MAX_POST_IMAGE_BYTES:
        raise ExecutableProposalError("POST_IMAGE_LIMIT_EXCEEDED")
    return post, separator + appended, eol


def _display_diff(before: bytes, post: bytes, path: str) -> bytes:
    left = before.decode("utf-8-sig").replace("\r\n", "\n").splitlines(keepends=True)
    right = post.decode("utf-8-sig").replace("\r\n", "\n").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(left, right, fromfile=path, tofile=path, lineterm="\n")
    ).encode("utf-8")


def _validate(proposal: ExecutableProposal) -> bool:
    if not isinstance(proposal, ExecutableProposal):
        return False
    data = proposal.model_dump(mode="json")
    supplied = data.pop("proposal_hash")
    try:
        post = base64.b64decode(
            proposal.post_image_base64.encode("ascii"), validate=True
        )
    except (ValueError, UnicodeEncodeError):
        return False
    if base64.b64encode(post).decode("ascii") != proposal.post_image_base64:
        return False
    if (
        proposal.proposed_write_scope != (proposal.target_relative_path,)
        or proposal.deferred_gates != _DEFERRED_GATES
        or proposal.selected_scope != tuple(sorted(proposal.selected_scope))
        or len(set(proposal.selected_scope)) != len(proposal.selected_scope)
        or proposal.target_relative_path not in proposal.selected_scope
        or not _canonical_relative(proposal.target_relative_path)
        or not all(_canonical_relative(item) for item in proposal.selected_scope)
    ):
        return False
    if (
        len(post) != proposal.post_image_size_bytes
        or _sha(post) != proposal.post_image_sha256
        or proposal.append_offset_bytes != proposal.target_before_size_bytes
    ):
        return False
    if proposal.append_offset_bytes > len(post):
        return False
    before, appended = (
        post[: proposal.append_offset_bytes],
        post[proposal.append_offset_bytes :],
    )
    try:
        _bom, eol = _validate_original(before)
    except ExecutableProposalError:
        return False
    eol_bytes = eol.encode("ascii")
    separator = eol_bytes if before.endswith(eol_bytes) else eol_bytes * 2
    generated = appended[len(separator) :]
    if (
        _sha(before) != proposal.target_before_sha256
        or len(before) != proposal.target_before_size_bytes
        or _sha(appended) != proposal.appended_bytes_sha256
        or len(appended) != proposal.appended_bytes_size
        or not appended.startswith(
            separator + _START_MARKER.encode("ascii") + eol_bytes
        )
        or generated.count(_START_MARKER.encode("ascii")) != 1
        or generated.count(_END_MARKER.encode("ascii")) != 1
        or not generated.endswith(_END_MARKER.encode("ascii") + eol_bytes)
        or generated.endswith(_END_MARKER.encode("ascii") + eol_bytes + eol_bytes)
    ):
        return False
    try:
        generated_text = generated.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return False
    if (
        b"\xef\xbb\xbf" in generated
        or unicodedata.normalize("NFC", generated_text) != generated_text
        or (
            eol == "\r\n"
            and any(
                character in "\r\n" for character in generated_text.replace("\r\n", "")
            )
        )
        or (eol == "\n" and "\r" in generated_text)
        or any(
            ord(ch) in _BIDI
            or ord(ch) == 0xFEFF
            or ord(ch) == 0
            or (ord(ch) < 32 and ch not in "\n\r\t")
            or 127 <= ord(ch) <= 159
            for ch in generated_text
        )
    ):
        return False
    try:
        diff = _display_diff(before, post, proposal.target_relative_path)
    except UnicodeDecodeError:
        return False
    if (
        _sha(diff) != proposal.display_diff_sha256
        or len(diff) != proposal.display_diff_size_bytes
    ):
        return False
    return supplied == _proposal_hash(data)


def serialize_executable_proposal(proposal: ExecutableProposal) -> bytes:
    if not isinstance(proposal, ExecutableProposal) or not _validate(proposal):
        raise ExecutableProposalError("INVALID_PROPOSAL")
    data = _canonical_json(proposal.model_dump(mode="json"))
    if len(data) > _MAX_PROPOSAL_BYTES:
        raise ExecutableProposalError("PROPOSAL_LIMIT_EXCEEDED")
    return data


def parse_executable_proposal_bytes(data: bytes) -> ExecutableProposal:
    if not isinstance(data, bytes) or len(data) > _MAX_PROPOSAL_BYTES:
        raise ExecutableProposalError("INVALID_PROPOSAL")
    try:

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        raw = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
        if not isinstance(raw, dict):
            raise TypeError("proposal must be an object")
        for name in ("selected_scope", "proposed_write_scope", "deferred_gates"):
            if name in raw:
                if not isinstance(raw[name], list):
                    raise TypeError("proposal sequence must be an array")
                raw[name] = tuple(raw[name])
        proposal = ExecutableProposal.model_validate(raw)
    except (UnicodeDecodeError, TypeError, ValueError, ValidationError) as error:
        raise ExecutableProposalError("INVALID_PROPOSAL") from error
    if data != _canonical_json(proposal.model_dump(mode="json")) or not _validate(
        proposal
    ):
        raise ExecutableProposalError("INVALID_PROPOSAL")
    return proposal


def load_executable_proposal(path: Path, *, material_root: Path) -> ExecutableProposal:
    root = _validate_root(material_root)
    return parse_executable_proposal_bytes(
        _safe_external(path, root, "INVALID_PROPOSAL_PATH")
    )


def _result(root: Path, result_path: Path) -> tuple[ResultSession, str]:
    try:
        result = load_session(root, result_path)
    except MaterialWorkflowError as error:
        raise ExecutableProposalError(error.code) from error
    if (
        not isinstance(result, ResultSession)
        or result.state != "generated"
        or not result.citations
        or not verify_result_integrity(result)
        or result.draft.artifact_kind != "readme"
        or result.draft.readme_target is None
        or bool(result.conflicts)
        or not revalidate_result_sources(root, result)
    ):
        raise ExecutableProposalError("RESULT_NOT_FRESH_OR_GROUNDED")
    raw = _safe_external(result_path, root, "INVALID_SESSION_PATH")
    if raw != serialize_session(result):
        raise ExecutableProposalError("RESULT_CHANGED_DURING_PREPARE")
    return result, _sha(raw)


def create_executable_proposal(
    root: Path, result_path: Path, target: Path, plan_path: Path, output: Path
) -> ExecutableProposal:
    try:
        plan = load_apply_plan(plan_path, material_root=root)
    except ControlledApplyError as error:
        raise ExecutableProposalError(error.code) from error
    if not verify_apply_plan(root, plan, result_path, target):
        raise ExecutableProposalError("PREFLIGHT_BLOCKED")
    plan_bytes = _safe_external(plan_path, root, "INVALID_PLAN_PATH")
    result, result_bytes_hash = _result(root, result_path)
    before, device, inode = _target_bytes(root, target, plan)
    post, appended, _eol = _compose(before, result)
    diff = _display_diff(before, post, plan.target_relative_path)
    payload: dict[str, object] = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "hash_domain": PROPOSAL_HASH_DOMAIN,
        "state": _STATE,
        "producer_version": POST_IMAGE_PRODUCER_VERSION,
        "composition_version": "append-original-prefix-v1",
        "diff_version": "projecttown-unified-diff-display-v1",
        "proposal_semantics": "complete_post_image_bytes_not_executed",
        "write_performed": False,
        "apply_plan_schema_version": plan.schema_version,
        "apply_plan_hash": plan.plan_hash,
        "apply_plan_bytes_sha256": _sha(plan_bytes),
        "result_schema_version": result.schema_version,
        "result_session_hash": result.session_hash,
        "result_bytes_sha256": result_bytes_hash,
        "artifact_hash": result.artifact_hash,
        "preview_hash": result.preview_hash,
        "target_relative_path": plan.target_relative_path,
        "target_before_sha256": _sha(before),
        "target_before_size_bytes": len(before),
        "target_before_device": device,
        "target_before_inode": inode,
        "selected_scope": plan.selected_scope,
        "proposed_write_scope": (plan.target_relative_path,),
        "scope_guard": "append_only_after_original_bytes",
        "encoding": "utf-8",
        "bom_policy": "preserve-leading-utf8-bom",
        "newline_policy": "inherit-crlf-or-use-lf",
        "unicode_policy": "original-bytes-preserved-generated-nfc",
        "terminal_newline_policy": "generated-section-exactly-one-eol",
        "marker_version": _MARKER_PREFIX,
        "append_offset_bytes": len(before),
        "appended_bytes_sha256": _sha(appended),
        "appended_bytes_size": len(appended),
        "post_image_base64": base64.b64encode(post).decode("ascii"),
        "post_image_sha256": _sha(post),
        "post_image_size_bytes": len(post),
        "display_diff_sha256": _sha(diff),
        "display_diff_size_bytes": len(diff),
        "deferred_gates": _DEFERRED_GATES,
    }
    payload["proposal_hash"] = _proposal_hash(payload)
    try:
        proposal = ExecutableProposal.model_validate(payload)
    except ValidationError as error:
        raise ExecutableProposalError("INVALID_PROPOSAL") from error
    # Re-capture every mutable binding immediately before the sole publication.
    try:
        final_plan = load_apply_plan(plan_path, material_root=root)
    except ControlledApplyError as error:
        raise ExecutableProposalError(error.code) from error
    final_plan_bytes = _safe_external(plan_path, root, "INVALID_PLAN_PATH")
    final_result, final_result_bytes_hash = _result(root, result_path)
    final_before, final_device, final_inode = _target_bytes(root, target, final_plan)
    if (
        not _validate(proposal)
        or not verify_apply_plan(root, final_plan, result_path, target)
        or final_plan != plan
        or final_plan_bytes != plan_bytes
        or final_result != result
        or final_result_bytes_hash != result_bytes_hash
        or (final_before, final_device, final_inode) != (before, device, inode)
    ):
        raise ExecutableProposalError("BINDING_CHANGED_DURING_PREPARE")
    try:
        publish_new_file(root, output, serialize_executable_proposal(proposal))
    except (PublicationAttentionError, PublicationRollbackError):
        raise
    except MaterialWorkflowError as error:
        raise ExecutableProposalError(error.code) from error
    # A published record which cannot be reloaded is attention-required, never removed.
    try:
        if not verify_executable_proposal(
            root,
            load_executable_proposal(output, material_root=root),
            result_path,
            target,
            plan_path,
        ):
            raise PublicationAttentionError()
    except (ExecutableProposalError, ControlledApplyError) as error:
        raise PublicationAttentionError() from error
    return proposal


def verify_executable_proposal(
    root: Path,
    proposal: ExecutableProposal,
    result_path: Path,
    target: Path,
    plan_path: Path,
) -> bool:
    try:
        if not _validate(proposal):
            return False
        plan = load_apply_plan(plan_path, material_root=root)
        if not verify_apply_plan(root, plan, result_path, target):
            return False
        plan_bytes = _safe_external(plan_path, root, "INVALID_PLAN_PATH")
        result, result_hash = _result(root, result_path)
        before, device, inode = _target_bytes(root, target, plan)
        post, appended, _ = _compose(before, result)
        diff = _display_diff(before, post, plan.target_relative_path)
        expected = proposal.model_dump(mode="json")
        expected.update(
            {
                "schema_version": PROPOSAL_SCHEMA_VERSION,
                "hash_domain": PROPOSAL_HASH_DOMAIN,
                "state": _STATE,
                "producer_version": POST_IMAGE_PRODUCER_VERSION,
                "composition_version": "append-original-prefix-v1",
                "diff_version": "projecttown-unified-diff-display-v1",
                "proposal_semantics": "complete_post_image_bytes_not_executed",
                "write_performed": False,
                "apply_plan_schema_version": plan.schema_version,
                "apply_plan_hash": plan.plan_hash,
                "apply_plan_bytes_sha256": _sha(plan_bytes),
                "result_schema_version": result.schema_version,
                "result_session_hash": result.session_hash,
                "result_bytes_sha256": result_hash,
                "artifact_hash": result.artifact_hash,
                "preview_hash": result.preview_hash,
                "target_relative_path": plan.target_relative_path,
                "target_before_sha256": _sha(before),
                "target_before_size_bytes": len(before),
                "target_before_device": device,
                "target_before_inode": inode,
                "selected_scope": list(plan.selected_scope),
                "proposed_write_scope": [plan.target_relative_path],
                "scope_guard": "append_only_after_original_bytes",
                "encoding": "utf-8",
                "bom_policy": "preserve-leading-utf8-bom",
                "newline_policy": "inherit-crlf-or-use-lf",
                "unicode_policy": "original-bytes-preserved-generated-nfc",
                "terminal_newline_policy": "generated-section-exactly-one-eol",
                "marker_version": _MARKER_PREFIX,
                "append_offset_bytes": len(before),
                "appended_bytes_sha256": _sha(appended),
                "appended_bytes_size": len(appended),
                "post_image_base64": base64.b64encode(post).decode("ascii"),
                "post_image_sha256": _sha(post),
                "post_image_size_bytes": len(post),
                "display_diff_sha256": _sha(diff),
                "display_diff_size_bytes": len(diff),
                "deferred_gates": list(_DEFERRED_GATES),
            }
        )
        expected["proposal_hash"] = _proposal_hash(
            {key: value for key, value in expected.items() if key != "proposal_hash"}
        )
        return expected == proposal.model_dump(mode="json")
    except (ExecutableProposalError, ControlledApplyError):
        return False


__all__ = [
    "POST_IMAGE_PRODUCER_VERSION",
    "PROPOSAL_HASH_DOMAIN",
    "PROPOSAL_SCHEMA_VERSION",
    "ExecutableProposal",
    "ExecutableProposalError",
    "create_executable_proposal",
    "load_executable_proposal",
    "parse_executable_proposal_bytes",
    "serialize_executable_proposal",
    "verify_executable_proposal",
]
