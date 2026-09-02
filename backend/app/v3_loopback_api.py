"""Native-loopback-only API and static surface for the optional v3 UI."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .v3_loopback_service import LoopbackService, LoopbackServiceError

_API = "/api/v3"
_UI = "/v3"
_COOKIE = "projecttown_v3_session"
_CSRF = "X-ProjectTown-V3-CSRF"
_OPERATION = "X-ProjectTown-V3-Operation"
_IDEMPOTENCY = "Idempotency-Key"
_MAX_BODY = 16 * 1024
_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def install_loopback_routes(
    app: FastAPI, service: LoopbackService, origin: str
) -> None:
    assets = Path(__file__).parent / "static" / "v3"

    def protected(
        request: Request,
        *,
        mutation: bool = False,
        bootstrap: bool = False,
        static: bool = False,
    ) -> None:
        if not service.client_allowed(request.client.host if request.client else None):
            raise LoopbackServiceError("LOOPBACK_CLIENT_REQUIRED", 403)
        if request.headers.get("host") != origin.removeprefix("http://"):
            raise LoopbackServiceError("HOST_REJECTED", 403)
        if any(
            name.lower()
            in {"forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}
            for name in request.headers
        ):
            raise LoopbackServiceError("FORWARDED_HEADERS_REJECTED", 403)
        received_origin = request.headers.get("origin")
        if (bootstrap or mutation) and received_origin != origin:
            raise LoopbackServiceError("ORIGIN_REJECTED", 403)
        if static and received_origin is not None and received_origin != origin:
            raise LoopbackServiceError("ORIGIN_REJECTED", 403)
        if (
            not (bootstrap or mutation or static)
            and received_origin is not None
            and received_origin != origin
        ):
            raise LoopbackServiceError("ORIGIN_REJECTED", 403)
        # Assets are deliberately available before a session exists so the
        # page can present a user-initiated session bootstrap.  They still get
        # the same loopback, Host, Origin and response-header protections.
        if not bootstrap and not static:
            service.verify_session(
                request.cookies.get(_COOKIE),
                request.headers.get(_CSRF),
                mutation=mutation,
            )

    def error(error: LoopbackServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code, content={"error": {"code": error.code}}
        )

    async def body(request: Request) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/json":
            raise LoopbackServiceError("JSON_CONTENT_REQUIRED", 415)
        raw = await request.body()
        if len(raw) > _MAX_BODY:
            raise LoopbackServiceError("BODY_TOO_LARGE", 413)
        try:

            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                value: dict[str, Any] = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate")
                    value[key] = item
                return value

            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise LoopbackServiceError("INVALID_JSON", 400) from exc
        if not isinstance(value, dict):
            raise LoopbackServiceError("INVALID_JSON", 400)
        return value

    @app.middleware("http")
    async def loopback_headers(
        request: Request, call_next: Callable[..., Any]
    ) -> Response:
        if request.url.path.startswith(_API) or request.url.path.startswith(_UI):
            try:
                # Static resources are also local-only and host checked.  They do
                # not require a session so the landing page can bootstrap one.
                if request.url.path.startswith(_UI):
                    protected(request, static=True)
                response = await call_next(request)
            except LoopbackServiceError as exc:
                response = error(exc)
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

    @app.get(f"{_API}/bindings")
    async def bindings(request: Request) -> Response:
        protected(request)
        return JSONResponse({"items": service.bindings()})

    @app.get(f"{_API}/operation")
    async def inspect(request: Request) -> Response:
        protected(request)
        return JSONResponse(service.inspect(request.headers.get(_OPERATION, "")))

    @app.get(f"{_API}/operation/check")
    async def check(request: Request) -> Response:
        protected(request)
        return JSONResponse(service.check(request.headers.get(_OPERATION, "")))

    @app.post(f"{_API}/operation/{{action}}")
    async def mutate(action: str, request: Request) -> Response:
        protected(request, mutation=True)
        if action not in {"apply", "reconcile", "restore"}:
            raise LoopbackServiceError("ACTION_REJECTED", 404)
        value = await body(request)
        if set(value) != {"confirmation"} or not isinstance(value["confirmation"], str):
            raise LoopbackServiceError("INVALID_REQUEST", 400)
        key = request.headers.get(_IDEMPOTENCY)
        if key is None:
            raise LoopbackServiceError("IDEMPOTENCY_REQUIRED", 400)
        status, result = service.mutate(
            request.headers.get(_OPERATION, ""), action, value["confirmation"], key
        )
        return JSONResponse(result, status_code=status)

    @app.get(_UI)
    @app.get(f"{_UI}/")
    async def index(request: Request) -> Response:
        return FileResponse(assets / "index.html", media_type="text/html")

    @app.get(f"{_UI}/{{asset}}")
    async def asset(asset: str, request: Request) -> Response:
        if asset not in {"app.js", "app.css"}:
            return Response(status_code=404)
        return FileResponse(assets / asset)
