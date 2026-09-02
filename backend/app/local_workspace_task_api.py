"""Loopback-only, read-only API for registered local material task bindings."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .local_workspace_task import (
    LocalWorkspaceTaskError,
    TaskBinding,
    load_binding,
    verify_binding,
)
from .safe_files import is_safe_directory

_API = "/api/workspace"
_UI = "/workspace"
_AUTHORING = f"{_API}/authoring"
_AUTHORING_MAX_BODY_BYTES = 64 * 1024
_COOKIE = "projecttown_workspace_session"
_CSRF = "X-ProjectTown-Workspace-CSRF"
_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class LocalWorkspaceTaskServiceError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code, self.status_code = code, status_code
        super().__init__(code)


class LocalWorkspaceTaskService:
    def __init__(self, work_root: Path, *, allow_test_client: bool = False) -> None:
        self.work_root = Path(work_root).resolve(strict=True)
        self.bindings_dir = self.work_root / "bindings"
        try:
            safe = all(
                is_safe_directory(path.lstat()) and path.resolve(strict=True) == path
                for path in (self.work_root, self.bindings_dir)
            )
        except OSError as error:
            raise LocalWorkspaceTaskServiceError("INVALID_WORK_ROOT", 503) from error
        if not safe:
            raise LocalWorkspaceTaskServiceError("INVALID_WORK_ROOT", 503)
        self.allow_test_client = allow_test_client
        # csrf, last-use (idle), creation (absolute).  Session state is process
        # local by design: restarting the service invalidates browser sessions.
        self._sessions: dict[str, tuple[str, float, float]] = {}
        self._lock = threading.RLock()

    def client_allowed(self, host: str | None) -> bool:
        return host == "127.0.0.1" or (self.allow_test_client and host == "testclient")

    def bootstrap(self) -> tuple[str, str]:
        with self._lock:
            now = time.monotonic()
            # A bounded map prevents a loopback tab flood from becoming an
            # unbounded in-process allocation.  Discard only expired sessions.
            for token, value in tuple(self._sessions.items()):
                if now - value[1] > 15 * 60 or now - value[2] > 60 * 60:
                    self._sessions.pop(token, None)
            if len(self._sessions) >= 128:
                raise LocalWorkspaceTaskServiceError("SESSION_CAPACITY", 429)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._sessions[token] = (csrf, now, now)
            return token, csrf

    def verify_session(self, token: str | None, csrf: str | None) -> None:
        with self._lock:
            value = self._sessions.get(token or "")
            now = time.monotonic()
            if value is None or now - value[1] > 15 * 60 or now - value[2] > 60 * 60:
                self._sessions.pop(token or "", None)
                raise LocalWorkspaceTaskServiceError("SESSION_REQUIRED", 401)
            if csrf is None or not secrets.compare_digest(value[0], csrf):
                raise LocalWorkspaceTaskServiceError("CSRF_REJECTED", 403)
            self._sessions[token or ""] = (value[0], now, value[2])

    def protect_request(
        self,
        request: Request,
        origin: str,
        *,
        bootstrap: bool = False,
        static: bool = False,
    ) -> None:
        """Shared strict-loopback envelope for Phase 4A and 4B routes."""
        if not self.client_allowed(request.client.host if request.client else None):
            raise LocalWorkspaceTaskServiceError("LOOPBACK_CLIENT_REQUIRED", 403)
        if request.headers.get("host") != origin.removeprefix("http://"):
            raise LocalWorkspaceTaskServiceError("HOST_REJECTED", 403)
        if any(
            name.lower()
            in {"forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}
            for name in request.headers
        ):
            raise LocalWorkspaceTaskServiceError("FORWARDED_HEADERS_REJECTED", 403)
        received_origin = request.headers.get("origin")
        if (
            (bootstrap or static)
            and received_origin is not None
            and received_origin != origin
        ):
            raise LocalWorkspaceTaskServiceError("ORIGIN_REJECTED", 403)
        if not bootstrap and not static:
            self.verify_session(
                request.cookies.get(_COOKIE), request.headers.get(_CSRF)
            )

    def _binding(self, task_id: str) -> tuple[TaskBinding, Any]:
        if len(task_id) != 64 or any(
            char not in "0123456789abcdef" for char in task_id
        ):
            raise LocalWorkspaceTaskServiceError("TASK_ID_REJECTED", 400)
        try:
            binding = load_binding(self.bindings_dir / f"{task_id}.json")
            result = verify_binding(binding, self.work_root)
        except LocalWorkspaceTaskError as error:
            raise LocalWorkspaceTaskServiceError(error.code, 409) from error
        return binding, result

    def bindings(self) -> list[dict[str, str]]:
        try:
            paths = sorted(self.bindings_dir.iterdir())
        except OSError as error:
            raise LocalWorkspaceTaskServiceError("INVALID_BINDING", 409) from error
        items: list[dict[str, str]] = []
        for path in paths:
            if path.suffix != ".json" or not path.is_file():
                raise LocalWorkspaceTaskServiceError("INVALID_BINDING", 409)
            binding, _ = self._binding(path.stem)
            items.append(
                {
                    "task_id": binding.task_id,
                    "task_label": binding.task_label,
                    "artifact_kind": binding.artifact_kind,
                    "freshness": "verified",
                }
            )
        return items

    def detail(self, task_id: str) -> dict[str, Any]:
        binding, result = self._binding(task_id)
        return {
            "task_id": binding.task_id,
            "task_label": binding.task_label,
            "artifact_kind": binding.artifact_kind,
            "binding_hash": binding.binding_hash,
            "result_hash": result.session_hash,
            "artifact_hash": result.artifact_hash,
            "preview_hash": result.preview_hash,
            "citations": [
                {
                    "id": item.id,
                    "source": item.relative_path,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "preview": item.preview,
                }
                for item in result.citations
            ],
        }

    def preview(self, task_id: str) -> dict[str, str]:
        _binding, result = self._binding(task_id)
        return {"preview_markdown": result.preview_markdown}


def install_local_workspace_task_routes(
    app: FastAPI, service: LocalWorkspaceTaskService, origin: str
) -> None:
    assets = Path(__file__).parent / "static" / "workspace"

    def protected(
        request: Request, *, bootstrap: bool = False, static: bool = False
    ) -> None:
        service.protect_request(request, origin, bootstrap=bootstrap, static=static)

    def error(value: LocalWorkspaceTaskServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=value.status_code, content={"error": {"code": value.code}}
        )

    @app.middleware("http")
    async def workspace_headers(
        request: Request, call_next: Callable[..., Any]
    ) -> Response:
        if request.url.path.startswith(_API) or request.url.path.startswith(_UI):
            try:
                if request.url.path.startswith(_UI):
                    protected(request, static=True)
                elif request.url.path.startswith(_AUTHORING):
                    # Do this before FastAPI parses a mutation body, so an
                    # untrusted browser cannot use validation as a session or
                    # request-shape oracle.
                    protected(request)
                    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                        if request.headers.get("origin") != origin:
                            raise LocalWorkspaceTaskServiceError("ORIGIN_REJECTED", 403)
                        content_type = request.headers.get("content-type", "").split(
                            ";", 1
                        )[0]
                        if content_type != "application/json":
                            raise LocalWorkspaceTaskServiceError(
                                "CONTENT_TYPE_REJECTED", 415
                            )
                        content_length = request.headers.get("content-length")
                        if content_length is None:
                            raise LocalWorkspaceTaskServiceError(
                                "CONTENT_LENGTH_REQUIRED", 411
                            )
                        if (
                            not content_length.isascii()
                            or not content_length.isdecimal()
                            or (
                                len(content_length) > 1
                                and content_length.startswith("0")
                            )
                        ):
                            raise LocalWorkspaceTaskServiceError(
                                "REQUEST_TOO_LARGE", 413
                            )
                        if int(content_length) > _AUTHORING_MAX_BODY_BYTES:
                            raise LocalWorkspaceTaskServiceError(
                                "REQUEST_TOO_LARGE", 413
                            )
                response = await call_next(request)
            except LocalWorkspaceTaskServiceError as error_value:
                response = error(error_value)
            response.headers.update(
                {
                    "Content-Security-Policy": _CSP,
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                }
            )
            return response
        return await call_next(request)

    @app.post(f"{_API}/session")
    async def session(request: Request) -> Response:
        protected(request, bootstrap=True)
        token, csrf = service.bootstrap()
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(_COOKIE, token, httponly=True, samesite="strict", path=_API)
        return response

    @app.get(f"{_API}/tasks")
    async def tasks(request: Request) -> Response:
        protected(request)
        return JSONResponse({"items": service.bindings()})

    @app.get(f"{_API}/tasks/{{task_id}}")
    async def detail(task_id: str, request: Request) -> Response:
        protected(request)
        return JSONResponse(service.detail(task_id))

    @app.get(f"{_API}/tasks/{{task_id}}/preview")
    async def preview(task_id: str, request: Request) -> Response:
        protected(request)
        return JSONResponse(service.preview(task_id))

    @app.get(_UI)
    @app.get(f"{_UI}/")
    async def index(request: Request) -> Response:
        return FileResponse(assets / "index.html", media_type="text/html")

    @app.get(f"{_UI}/{{asset}}")
    async def asset(asset: str, request: Request) -> Response:
        if asset not in {"app.js", "app.css"}:
            return Response(status_code=404)
        return FileResponse(assets / asset)
