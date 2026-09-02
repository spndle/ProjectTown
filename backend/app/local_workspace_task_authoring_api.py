"""Strict-loopback HTTP adapter for the bounded Phase 4B authoring core.

The adapter deliberately accepts no filesystem paths, command lines, exporter
choices, or authorization actions.  All mutable artifacts are created by the
authoring core beneath a pre-existing external work root.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .local_workspace_task_api import (
    _API,
    LocalWorkspaceTaskService,
    LocalWorkspaceTaskServiceError,
)
from .local_workspace_task_authoring import (
    AuthoringError,
    CatalogV1,
    authoring_projection,
    build_catalog,
    confirm_and_generate,
    create_authoring_draft,
    export_result,
    load_authoring_result,
    publish_or_load_catalog,
    read_authoring_export,
    recover_authoring_state,
)
from .material_workflow import DraftSession, load_external_session
from .safe_files import is_reparse, is_safe_directory

_AUTHORING = f"{_API}/authoring"
_IDEMPOTENCY = "Idempotency-Key"
_SAFE_CODES = frozenset(
    {
        "ATTENTION",
        "CATALOG_INCOMPLETE",
        "CREATE_ONLY_CONFLICT",
        "DRAFT_UNAVAILABLE",
        "IDEMPOTENCY_CONFLICT",
        "INVALID_CONFIRMATION",
        "INVALID_IDEMPOTENCY_KEY",
        "INVALID_README_TARGET",
        "INVALID_SOURCE_ID",
        "INVALID_TASK_ID",
        "INVALID_CATALOG",
        "CATALOG_TAMPERED",
        "EXPORT_UNAVAILABLE",
        "MATERIAL_STALE_OR_MISMATCH",
        "PUBLICATION_ROLLED_BACK",
        "COMMITTED_NEEDS_ATTENTION",
        "RESULT_UNAVAILABLE",
        "TASK_CAPACITY_EXCEEDED",
    }
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DraftInput(_Input):
    task: StrictStr = Field(min_length=1, max_length=4096)
    artifact_kind: Literal["plan", "report", "readme"]
    source_ids: list[StrictStr] = Field(min_length=1, max_length=64)
    readme_target_id: StrictStr | None = Field(default=None, max_length=64)
    constraints: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=16)

    @field_validator("task")
    @classmethod
    def canonical_task(cls, value: str) -> str:
        return _canonical_input_text(value, 4096)

    @field_validator("constraints")
    @classmethod
    def canonical_constraints(cls, value: dict[str, str]) -> dict[str, str]:
        canonical: dict[str, str] = {}
        for key, item in value.items():
            normalized_key = _canonical_input_text(key, 64)
            if normalized_key in canonical:
                raise ValueError("duplicate constraint key")
            canonical[normalized_key] = _canonical_input_text(item, 512)
        return canonical


class GenerateInput(_Input):
    contract_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_phrase: StrictStr = Field(min_length=1, max_length=80)


def _canonical_input_text(value: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or unicodedata.normalize("NFC", value) != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid authoring input")
    return value


class LocalWorkspaceTaskAuthoringService:
    """Path-owning service; API callers see only opaque IDs and hashes."""

    def __init__(self, work_root: Path, material_root: Path) -> None:
        self.work_root = Path(work_root).resolve(strict=True)
        self.material_root = Path(material_root).resolve(strict=True)
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
        try:
            safe = (
                self.work_root != self.material_root
                and not self.work_root.is_relative_to(self.material_root)
                and not self.material_root.is_relative_to(self.work_root)
                and is_safe_directory(self.work_root.lstat())
                and not is_reparse(self.work_root.lstat())
                and is_safe_directory(self.material_root.lstat())
                and not is_reparse(self.material_root.lstat())
                and all(
                    is_safe_directory((self.work_root / name).lstat())
                    and not is_reparse((self.work_root / name).lstat())
                    and (self.work_root / name).resolve(strict=True)
                    == self.work_root / name
                    for name in required
                )
                and self.material_root.resolve(strict=True) == self.material_root
            )
        except OSError as error:
            raise LocalWorkspaceTaskServiceError(
                "INVALID_AUTHORING_ROOT", 503
            ) from error
        if not safe:
            raise LocalWorkspaceTaskServiceError("INVALID_AUTHORING_ROOT", 503)

    @staticmethod
    def _task_id(idempotency_key: str) -> str:
        if (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key)
        ):
            raise LocalWorkspaceTaskServiceError("INVALID_IDEMPOTENCY_KEY", 400)
        return "t-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]

    def _catalog(self) -> CatalogV1:
        try:
            catalog = build_catalog(self.material_root)
            return publish_or_load_catalog(self.work_root, catalog)
        except AuthoringError as error:
            raise _authoring_error(error) from error

    def catalog(self) -> dict[str, object]:
        catalog = self._catalog()
        return {
            "catalog_id": catalog.catalog_id,
            "catalog_hash": catalog.catalog_hash,
            "root_hash": catalog.root_hash,
            "items": [
                {
                    "source_id": entry.source_id,
                    "relative_path": entry.relative_path,
                    "display_name": entry.relative_path.rsplit("/", 1)[-1],
                    "suffix": entry.suffix,
                    "size_bytes": entry.size_bytes,
                }
                for entry in catalog.entries
            ],
        }

    def create_draft(
        self, payload: DraftInput, idempotency_key: str
    ) -> dict[str, object]:
        try:
            catalog = self._catalog()
            task_id = self._task_id(idempotency_key)
            draft = create_authoring_draft(
                self.work_root,
                self.material_root,
                catalog,
                task_id=task_id,
                task=payload.task,
                artifact_kind=payload.artifact_kind,
                source_ids=payload.source_ids,
                idempotency_key=idempotency_key,
                readme_target_id=payload.readme_target_id,
                constraints=payload.constraints,
            )
            state = recover_authoring_state(
                self.work_root, task_id=task_id, material_root=self.material_root
            )
            return {
                **authoring_projection(state),
                "contract_hash": draft.contract_hash,
                "confirmation_phrase": f"CONFIRM {draft.contract_hash}",
            }
        except AuthoringError as error:
            raise _authoring_error(error) from error

    def generate(
        self, task_id: str, payload: GenerateInput, idempotency_key: str
    ) -> dict[str, object]:
        try:
            draft = load_external_session(
                self.work_root / "drafts" / f"{task_id}-draft.json"
            )
            if (
                not isinstance(draft, DraftSession)
                or draft.contract_hash != payload.contract_hash
            ):
                raise AuthoringError("INVALID_CONFIRMATION")
            confirm_and_generate(
                self.work_root,
                self.material_root,
                task_id=task_id,
                confirmation_phrase=payload.confirmation_phrase,
                idempotency_key=idempotency_key,
            )
            return self.state(task_id)
        except AuthoringError as error:
            raise _authoring_error(error) from error
        except Exception as error:  # load parser errors are intentionally opaque.
            raise LocalWorkspaceTaskServiceError("DRAFT_UNAVAILABLE", 409) from error

    def state(self, task_id: str) -> dict[str, object]:
        try:
            state = recover_authoring_state(
                self.work_root, task_id=task_id, material_root=self.material_root
            )
            result_state: str | None = None
            if state.result_hash is not None:
                _verified_state, result = load_authoring_result(
                    self.work_root, self.material_root, task_id=task_id
                )
                result_state = result.state
            return {**authoring_projection(state), "result_state": result_state}
        except AuthoringError as error:
            raise _authoring_error(error) from error
        except Exception as error:
            raise LocalWorkspaceTaskServiceError("ATTENTION", 409) from error

    def preview(self, task_id: str) -> dict[str, str]:
        try:
            _state, result = load_authoring_result(
                self.work_root, self.material_root, task_id=task_id
            )
            return {
                "preview_markdown": result.preview_markdown,
                "result_state": result.state,
            }
        except AuthoringError as error:
            raise _authoring_error(error) from error
        except Exception as error:
            raise LocalWorkspaceTaskServiceError("RESULT_UNAVAILABLE", 409) from error

    def export(
        self,
        task_id: str,
        format_name: Literal["markdown", "pdf"],
        idempotency_key: str,
    ) -> dict[str, object]:
        try:
            _state, result = load_authoring_result(
                self.work_root, self.material_root, task_id=task_id
            )
            if result.state != "generated" or result.conflicts:
                raise AuthoringError("ATTENTION")
            export_result(
                self.work_root,
                self.material_root,
                task_id=task_id,
                format=format_name,
                idempotency_key=idempotency_key,
            )
            return self.state(task_id)
        except AuthoringError as error:
            raise _authoring_error(error) from error
        except Exception as error:
            raise LocalWorkspaceTaskServiceError("RESULT_UNAVAILABLE", 409) from error

    def download(self, task_id: str, format_name: Literal["markdown", "pdf"]) -> bytes:
        try:
            return read_authoring_export(
                self.work_root,
                self.material_root,
                task_id=task_id,
                format=format_name,
            )
        except AuthoringError as error:
            raise _authoring_error(error) from error


def _authoring_error(error: AuthoringError) -> LocalWorkspaceTaskServiceError:
    code = error.code if error.code in _SAFE_CODES else "ATTENTION"
    if code == "EXPORT_UNAVAILABLE":
        status = 404
    elif code in {
        "INVALID_CONFIRMATION",
        "INVALID_IDEMPOTENCY_KEY",
        "INVALID_SOURCE_ID",
        "INVALID_TASK_ID",
        "INVALID_CATALOG",
        "CATALOG_TAMPERED",
        "INVALID_README_TARGET",
    }:
        status = 400
    elif code in {"CREATE_ONLY_CONFLICT", "IDEMPOTENCY_CONFLICT"}:
        status = 409
    elif code in {"PUBLICATION_ROLLED_BACK", "COMMITTED_NEEDS_ATTENTION"}:
        status = 503
    else:
        status = 409
    return LocalWorkspaceTaskServiceError(code, status)


def install_local_workspace_task_authoring_routes(
    app: FastAPI,
    workspace_service: LocalWorkspaceTaskService,
    authoring_service: LocalWorkspaceTaskAuthoringService,
    origin: str,
) -> None:
    """Install no-more-than-authoring routes under the existing envelope."""

    def protected(request: Request, *, mutation: bool = False) -> None:
        workspace_service.protect_request(request, origin)
        if mutation:
            if request.headers.get("origin") != origin:
                raise LocalWorkspaceTaskServiceError("ORIGIN_REJECTED", 403)
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if content_type != "application/json":
                raise LocalWorkspaceTaskServiceError("CONTENT_TYPE_REJECTED", 415)

    def error(value: LocalWorkspaceTaskServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=value.status_code, content={"error": {"code": value.code}}
        )

    def action(
        request: Request, mutation: bool, callback: Callable[[], Response]
    ) -> Response:
        try:
            protected(request, mutation=mutation)
            return callback()
        except LocalWorkspaceTaskServiceError as value:
            return error(value)

    def key(value: str | None) -> str:
        if value is None:
            raise LocalWorkspaceTaskServiceError("IDEMPOTENCY_REQUIRED", 400)
        return value

    @app.get(f"{_AUTHORING}/catalog")
    async def catalog(request: Request) -> Response:
        return action(request, False, lambda: JSONResponse(authoring_service.catalog()))

    @app.post(f"{_AUTHORING}/drafts")
    async def drafts(
        payload: DraftInput,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias=_IDEMPOTENCY),
    ) -> Response:
        return action(
            request,
            True,
            lambda: JSONResponse(
                authoring_service.create_draft(payload, key(idempotency_key))
            ),
        )

    @app.post(f"{_AUTHORING}/tasks/{{task_id}}/generate")
    async def generate(
        task_id: str,
        payload: GenerateInput,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias=_IDEMPOTENCY),
    ) -> Response:
        return action(
            request,
            True,
            lambda: JSONResponse(
                authoring_service.generate(task_id, payload, key(idempotency_key))
            ),
        )

    @app.get(f"{_AUTHORING}/tasks/{{task_id}}/state")
    async def state(task_id: str, request: Request) -> Response:
        return action(
            request, False, lambda: JSONResponse(authoring_service.state(task_id))
        )

    @app.get(f"{_AUTHORING}/tasks/{{task_id}}/preview")
    async def preview(task_id: str, request: Request) -> Response:
        return action(
            request, False, lambda: JSONResponse(authoring_service.preview(task_id))
        )

    @app.post(f"{_AUTHORING}/tasks/{{task_id}}/exports/{{format_name}}")
    async def exports(
        task_id: str,
        format_name: Literal["markdown", "pdf"],
        request: Request,
        idempotency_key: str | None = Header(default=None, alias=_IDEMPOTENCY),
    ) -> Response:
        return action(
            request,
            True,
            lambda: JSONResponse(
                authoring_service.export(task_id, format_name, key(idempotency_key))
            ),
        )

    @app.get(f"{_AUTHORING}/tasks/{{task_id}}/downloads/{{format_name}}")
    async def downloads(
        task_id: str, format_name: Literal["markdown", "pdf"], request: Request
    ) -> Response:
        def response() -> Response:
            data = authoring_service.download(task_id, format_name)
            media_type = (
                "text/markdown" if format_name == "markdown" else "application/pdf"
            )
            suffix = "md" if format_name == "markdown" else "pdf"
            return Response(
                content=data,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{task_id}.{suffix}"'
                },
            )

        return action(request, False, response)
