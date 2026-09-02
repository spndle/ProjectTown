"""Deterministic, artifact-backed verification for the v1 runtime."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..runtime import stable_hash
from ..tools import Sandbox, ToolRegistry
from .models import AcceptanceCriterion, Evidence, VerificationResult


class Verifier:
    """Re-read WorldState at verification time; executor claims are ignored."""

    name = "projecttown.deterministic"
    version = "1.0"

    def __init__(
        self, sandbox: Sandbox, tool_registry: ToolRegistry | None = None
    ) -> None:
        self.sandbox = sandbox
        self.tool_registry = tool_registry

    def verify_read_only_tool(
        self,
        *,
        criterion_id: str,
        tool_name: str,
        workspace: str,
        arguments: dict[str, Any],
        quest_id: str,
        milestone_id: str,
        action_attempt: str,
        event_sequence: int,
    ) -> VerificationResult:
        """Independently repeat an allowlisted read-only observation."""

        if self.tool_registry is None:
            raise RuntimeError("tool registry is required for tool observation")
        if tool_name not in {
            "list_directory",
            "read_file",
            "check_markdown",
            "check_python_syntax",
        }:
            raise ValueError("only read-only tools can be re-run by the verifier")
        try:
            result = self.tool_registry.execute(tool_name, workspace, arguments)
            passed = isinstance(result, dict)
            reason = "ok" if passed else "tool observation was not an object"
        except ToolError as exc:
            result = {"error": exc.as_dict()}
            passed = False
            reason = exc.message
        result_hash = stable_hash(result)
        evidence_id = self._evidence_id(
            quest_id, criterion_id, result_hash, action_attempt, event_sequence
        )
        evidence = Evidence(
            id=evidence_id,
            quest_id=quest_id,
            milestone_id=milestone_id,
            criterion_id=criterion_id,
            verifier=self.name,
            verifier_version=self.version,
            artifact_path=(
                str(arguments.get("path")) if arguments.get("path") else None
            ),
            artifact_hash=result_hash,
            action_attempt=action_attempt,
            source_event_sequence=event_sequence,
            passed=passed,
            details={"tool_name": tool_name, "result_keys": sorted(result)},
        )
        return VerificationResult(
            id=stable_hash(
                {
                    "criterion_id": criterion_id,
                    "evidence_id": evidence_id,
                    "passed": passed,
                }
            )[:24],
            quest_id=quest_id,
            milestone_id=milestone_id,
            criterion_id=criterion_id,
            verifier=self.name,
            verifier_version=self.version,
            passed=passed,
            evidence=evidence,
            reason=reason,
        )

    def verify(
        self,
        criterion: AcceptanceCriterion,
        workspace: str,
        *,
        quest_id: str | None = None,
        milestone_id: str | None = None,
        criterion_version: int = 1,
        evidence: Evidence | None = None,
        action_attempt: str | None = None,
        event_sequence: int | None = None,
    ) -> VerificationResult:
        path = criterion.path
        artifact_hash: str | None = None
        details: dict[str, Any] = {}
        passed = False
        reason = ""

        try:
            if criterion.kind == "diff_scope":
                passed, reason, details = self._diff_scope(criterion)
                artifact_hash = stable_hash(
                    {
                        "allowed_paths": criterion.allowed_paths,
                        "changed_paths": criterion.changed_paths,
                    }
                )
            else:
                if not path:
                    raise ValueError("criterion path is required")
                artifact = self.sandbox.resolve(workspace, path, must_exist=False)
                if artifact.is_file():
                    size_bytes = artifact.stat().st_size
                    details["size_bytes"] = size_bytes
                    if size_bytes > self.sandbox.max_file_bytes:
                        raise ValueError("artifact exceeds verifier size limit")
                    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if criterion.kind == "file_exists_nonempty":
                    passed = artifact.is_file() and artifact.stat().st_size > 0
                    reason = "ok" if passed else "missing or empty artifact"
                elif criterion.kind == "markdown":
                    passed, reason = self._markdown(artifact)
                elif criterion.kind == "python_syntax":
                    passed, reason = self._python(artifact)
                elif criterion.kind == "json_schema":
                    passed, reason = self._json_schema(
                        artifact, criterion.required_keys
                    )
        except (OSError, ToolError, UnicodeError, ValueError) as exc:
            reason = str(exc)
            passed = False

        evidence_id = self._evidence_id(
            quest_id,
            criterion.id,
            artifact_hash,
            action_attempt,
            event_sequence,
        )
        current = Evidence(
            id=evidence_id,
            quest_id=quest_id,
            milestone_id=milestone_id,
            criterion_id=criterion.id,
            criterion_version=criterion_version,
            verifier=self.name,
            verifier_version=self.version,
            artifact_path=path,
            artifact_hash=artifact_hash,
            action_attempt=action_attempt,
            source_event_sequence=event_sequence,
            passed=passed,
            details=details,
        )

        if evidence is not None and not self._same_source(
            evidence,
            current,
            action_attempt=action_attempt,
            event_sequence=event_sequence,
        ):
            passed = False
            reason = "stale or tampered evidence"
            current = current.model_copy(update={"passed": False})

        result_id = stable_hash(
            {
                "criterion_id": criterion.id,
                "criterion_version": criterion_version,
                "evidence_id": current.id,
                "passed": passed,
            }
        )[:24]
        return VerificationResult(
            id=result_id,
            quest_id=quest_id,
            milestone_id=milestone_id,
            criterion_id=criterion.id,
            criterion_version=criterion_version,
            verifier=self.name,
            verifier_version=self.version,
            passed=passed,
            evidence=current,
            reason=reason or None,
        )

    def verify_all(
        self,
        criteria: Iterable[AcceptanceCriterion],
        workspace: str,
        **kwargs: Any,
    ) -> tuple[bool, list[VerificationResult]]:
        criterion_list = list(criteria)
        results = [self.verify(item, workspace, **kwargs) for item in criterion_list]
        passed = all(
            result.passed
            for criterion, result in zip(criterion_list, results, strict=True)
            if criterion.required
        )
        return passed, results

    verify_criterion = verify

    @staticmethod
    def _same_source(
        previous: Evidence,
        current: Evidence,
        *,
        action_attempt: str | None,
        event_sequence: int | None,
    ) -> bool:
        required_matches = (
            previous.criterion_id == current.criterion_id
            and previous.criterion_version == current.criterion_version
            and previous.verifier == current.verifier
            and previous.verifier_version == current.verifier_version
            and previous.artifact_hash == current.artifact_hash
            and previous.artifact_path == current.artifact_path
        )
        if action_attempt is not None:
            required_matches = (
                required_matches and previous.action_attempt == action_attempt
            )
        if event_sequence is not None:
            required_matches = (
                required_matches and previous.source_event_sequence == event_sequence
            )
        return required_matches

    @classmethod
    def _diff_scope(
        cls, criterion: AcceptanceCriterion
    ) -> tuple[bool, str, dict[str, Any]]:
        allowed = {cls._normalize_path(path) for path in criterion.allowed_paths}
        changed = {cls._normalize_path(path) for path in criterion.changed_paths}
        outside = sorted(
            path
            for path in changed
            if not any(
                path == allowed_path or path.startswith(allowed_path.rstrip("/") + "/")
                for allowed_path in allowed
            )
        )
        return (
            not outside,
            "ok" if not outside else "changed paths outside allowed scope",
            {"outside_paths": outside, "changed_count": len(changed)},
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        return Path(path).as_posix().lstrip("./")

    @staticmethod
    def _markdown(path: Path) -> tuple[bool, str]:
        if not path.is_file():
            return False, "missing markdown artifact"
        text = path.read_text(encoding="utf-8")
        fences = sum(
            line.lstrip().startswith(("```", "~~~")) for line in text.splitlines()
        )
        passed = bool(text.strip()) and fences % 2 == 0
        return passed, "ok" if passed else "empty or unclosed markdown fence"

    @staticmethod
    def _python(path: Path) -> tuple[bool, str]:
        if not path.is_file():
            return False, "missing python artifact"
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            return False, str(exc)
        return True, "ok"

    @staticmethod
    def _json_schema(path: Path, required: list[str]) -> tuple[bool, str]:
        if not path.is_file():
            return False, "missing json artifact"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return False, str(exc)
        missing = [
            key for key in required if not isinstance(value, dict) or key not in value
        ]
        return (
            not missing,
            "ok" if not missing else f"missing required keys: {', '.join(missing)}",
        )

    @staticmethod
    def _evidence_id(
        quest_id: str | None,
        criterion: str,
        digest: str | None,
        attempt: str | None,
        sequence: int | None,
    ) -> str:
        raw = (
            f"{quest_id or ''}|{criterion}|{digest or ''}|{attempt or ''}|"
            f"{sequence if sequence is not None else ''}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
