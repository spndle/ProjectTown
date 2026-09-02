from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent import RuleBasedAgent
from .config import Settings
from .database import Database
from .errors import AppError
from .local_settings import (
    LocalSettingsService,
    install_local_settings_routes,
    local_settings_route_enabled,
)
from .local_workspace_task_api import (
    LocalWorkspaceTaskService,
    install_local_workspace_task_routes,
)
from .local_workspace_task_authoring_api import (
    LocalWorkspaceTaskAuthoringService,
    install_local_workspace_task_authoring_routes,
)
from .models import (
    HealthResponse,
    Quest,
    QuestCreate,
    QuestList,
    RunAccepted,
    TemplateList,
    TraceList,
)
from .service import QuestService
from .telemetry import NoOpTelemetry, Telemetry
from .tools import Sandbox, ToolRegistry, build_default_registry
from .v1.api import install_v1_routes
from .v1.gateway import READ_ONLY_TOOLS, ToolGateway
from .v1.mcp_adapter import McpInstallResult, install_local_mcp_adapter
from .v1.service import V1QuestService
from .v1.storage import V1Storage
from .v3_loopback_api import install_loopback_routes
from .v3_loopback_service import LoopbackService

logger = logging.getLogger("projecttown")


def create_app(
    config: Settings | Mapping[str, Any] | None = None,
    *,
    agent: RuleBasedAgent | None = None,
    database: Database | None = None,
    tool_registry: ToolRegistry | None = None,
    telemetry: Telemetry | None = None,
    local_settings_service: LocalSettingsService | None = None,
    local_mcp_servers: Mapping[str, Any] | None = None,
    loopback_service: LoopbackService | None = None,
    local_workspace_task_service: LocalWorkspaceTaskService | None = None,
    local_workspace_task_authoring_service: LocalWorkspaceTaskAuthoringService
    | None = None,
) -> FastAPI:
    """Build a ProjectTown FastAPI application with injectable dependencies."""

    if config is None:
        settings = Settings.from_env()
    elif isinstance(config, Settings):
        settings = config
    elif isinstance(config, Mapping):
        settings = Settings.from_mapping(config)
    else:
        raise TypeError("config must be Settings, a mapping, or None")

    if settings.enable_local_mcp and not local_mcp_servers:
        raise ValueError("enable_local_mcp requires explicit local_mcp_servers")
    if settings.enable_v3_loopback_ui and settings.v3_work_root is None:
        raise ValueError("enable_v3_loopback_ui requires v3_work_root")
    if (
        settings.enable_local_workspace_task
        and settings.local_workspace_task_root is None
    ):
        raise ValueError(
            "enable_local_workspace_task requires local_workspace_task_root"
        )
    if (
        settings.enable_local_workspace_task_create
        and settings.local_workspace_task_material_root is None
    ):
        raise ValueError(
            "enable_local_workspace_task_create requires local_workspace_task_material_root"
        )

    settings.sandbox_root.mkdir(parents=True, exist_ok=True)
    db = database or Database(settings.database_path)
    # The feature flag reserves configuration for a future explicit exporter.
    # Until then, an uninjected application remains a true no-op: no thread and
    # no misleading "exported" discard counter.
    runtime_telemetry = telemetry or NoOpTelemetry()
    planner = agent or RuleBasedAgent()
    sandbox = Sandbox(settings.sandbox_root, settings.max_file_bytes)
    tools = tool_registry or build_default_registry(sandbox)
    mcp_install = McpInstallResult(frozenset(), frozenset(), frozenset())
    if settings.enable_local_mcp:
        assert local_mcp_servers is not None
        mcp_install = install_local_mcp_adapter(tools, local_mcp_servers)
    service = QuestService(
        database=db,
        agent=planner,
        sandbox=sandbox,
        tools=tools,
        max_workers=settings.max_workers,
    )
    runtime_storage: V1Storage | None = None
    runtime_service: V1QuestService | None = None
    if settings.enable_v1_runtime:
        runtime_storage = V1Storage(settings.database_path)
        runtime_gateway = ToolGateway(
            tools,
            runtime_storage,
            allowlist=set(settings.tool_allowlist) | set(mcp_install.allowlist),
            high_risk_tools=set(settings.high_risk_tools)
            | set(mcp_install.high_risk_tools),
            read_only_tools=set(READ_ONLY_TOOLS) | set(mcp_install.read_only_tools),
        )
        runtime_service = V1QuestService(
            storage=runtime_storage,
            agent=planner,
            sandbox=sandbox,
            tools=tools,
            gateway=runtime_gateway,
            max_workers=settings.runtime_max_workers,
            lease_seconds=settings.execution_lease_seconds,
            watchdog_threshold=settings.watchdog_threshold,
        )
    settings_service: LocalSettingsService | None = None
    if local_settings_route_enabled(settings):
        settings_service = local_settings_service or LocalSettingsService(
            container_mode=_is_container_settings_mode(settings),
            trusted_peer=settings.local_settings_trusted_peer,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        try:
            if settings_service is not None:
                settings_service.start()
            yield
        finally:
            # Active local MCP sessions own child process trees.  Stop them
            # before waiting for runtime workers, so shutdown remains bounded.
            mcp_install.cancel_all()
            if runtime_service is not None:
                runtime_service.close(wait=True)
            service.close(wait=True)
            if runtime_storage is not None:
                runtime_storage.close()
            if settings_service is not None:
                settings_service.close()
            try:
                runtime_telemetry.close()
            except Exception:  # noqa: BLE001 - shutdown telemetry is non-critical.
                logger.error("ProjectTown telemetry shutdown failed")
            db.close()

    application = FastAPI(
        title="ProjectTown API",
        description=(
            "ProjectTown v1.0 event-sourced Agent runtime with a frozen "
            "v0.1 compatibility API"
        ),
        version=settings.version,
        # Debug responses are handled by the sanitizing exception handler below;
        # Starlette's traceback response can echo secrets from exception text.
        debug=False,
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.database = db
    application.state.agent = planner
    application.state.sandbox = sandbox
    application.state.tools = tools
    application.state.mcp_install = mcp_install
    application.state.quest_service = service
    application.state.runtime_storage = runtime_storage
    application.state.runtime_service = runtime_service
    application.state.telemetry = runtime_telemetry
    application.state.local_settings_service = settings_service
    application.state.loopback_service = None
    application.state.local_workspace_task_service = None
    application.state.local_workspace_task_authoring_service = None

    @application.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        correlation_id = uuid.uuid4().hex
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        with runtime_telemetry.bind_trace(correlation_id):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            duration_ms = int((time.perf_counter() - started) * 1000)
            response.headers["X-Process-Time-Ms"] = str(duration_ms)
            runtime_telemetry.emit_http_request(
                correlation_id=correlation_id,
                method=request.method,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        authoring_request = request.url.path.startswith("/api/workspace/authoring")
        return _error_response(
            request,
            status_code=422,
            code=(
                "AUTHORING_VALIDATION_REJECTED"
                if authoring_request
                else "VALIDATION_ERROR"
            ),
            message=(
                "Authoring request validation failed"
                if authoring_request
                else "Request validation failed"
            ),
            details=None if authoring_request else jsonable_encoder(exc.errors()),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "ROUTE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = "Route was not found" if exc.status_code == 404 else str(exc.detail)
        return _error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=message,
            details={"path": request.url.path},
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error(
            "Unhandled ProjectTown API error type=%s correlation_id=%s",
            type(exc).__name__,
            getattr(request.state, "correlation_id", "unknown"),
        )
        details = {"exception_type": type(exc).__name__} if settings.debug else None
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected server error occurred",
            details=details,
        )

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> dict[str, str]:
        database_status = "ok" if db.ping() else "unavailable"
        return {
            "status": "ok" if database_status == "ok" else "degraded",
            "version": settings.version,
            "agent": planner.name,
            "database": database_status,
        }

    @application.get(
        f"{settings.api_prefix}/templates",
        response_model=TemplateList,
        tags=["templates"],
    )
    def list_templates() -> dict[str, Any]:
        items = planner.list_templates()
        return {"items": items, "total": len(items)}

    @application.post(
        f"{settings.api_prefix}/quests",
        response_model=Quest,
        status_code=status.HTTP_201_CREATED,
        tags=["quests"],
    )
    def create_quest(payload: QuestCreate) -> dict[str, Any]:
        return service.create_quest(
            goal=payload.goal,
            template_id=payload.template_id,
            workspace=payload.workspace,
        )

    @application.get(
        f"{settings.api_prefix}/quests",
        response_model=QuestList,
        tags=["quests"],
    )
    def list_quests() -> dict[str, Any]:
        items = service.list_quests()
        return {"items": items, "total": len(items)}

    @application.get(
        f"{settings.api_prefix}/quests/{{quest_id}}",
        response_model=Quest,
        tags=["quests"],
    )
    def get_quest(quest_id: str) -> dict[str, Any]:
        return service.get_quest(quest_id)

    @application.post(
        f"{settings.api_prefix}/quests/{{quest_id}}/run",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["quests"],
    )
    def run_quest(quest_id: str) -> dict[str, Any]:
        quest = service.start_quest(quest_id)
        return {
            "quest_id": quest["id"],
            "status": "running",
            "message": "Quest execution started",
        }

    @application.get(
        f"{settings.api_prefix}/quests/{{quest_id}}/traces",
        response_model=TraceList,
        tags=["traces"],
    )
    def get_traces(quest_id: str) -> dict[str, Any]:
        items = service.get_traces(quest_id)
        return {"items": items, "total": len(items)}

    if runtime_service is not None and runtime_storage is not None:
        benchmark_output_root = (
            settings.sandbox_root / ".projecttown-benchmark"
            if str(settings.database_path) == ":memory:"
            else settings.database_path.parent / "benchmark-results"
        )
        application.state.benchmark_manager = install_v1_routes(
            application,
            prefix=settings.runtime_api_prefix,
            service=runtime_service,
            storage=runtime_storage,
            benchmark_output_root=benchmark_output_root,
            websocket_poll_seconds=settings.websocket_poll_seconds,
        )

    if settings_service is not None:
        install_local_settings_routes(application, settings_service)

    if settings.enable_v3_loopback_ui:
        runtime_loopback = loopback_service or LoopbackService(settings.v3_work_root)
        application.state.loopback_service = runtime_loopback
        install_loopback_routes(application, runtime_loopback, settings.v3_origin)
    if settings.enable_local_workspace_task:
        workspace_service = local_workspace_task_service or LocalWorkspaceTaskService(
            settings.local_workspace_task_root
        )
        application.state.local_workspace_task_service = workspace_service
        install_local_workspace_task_routes(
            application, workspace_service, settings.v3_origin
        )
        if settings.enable_local_workspace_task_create:
            authoring_service = (
                local_workspace_task_authoring_service
                or LocalWorkspaceTaskAuthoringService(
                    settings.local_workspace_task_root,
                    settings.local_workspace_task_material_root,
                )
            )
            application.state.local_workspace_task_authoring_service = authoring_service
            install_local_workspace_task_authoring_routes(
                application, workspace_service, authoring_service, settings.v3_origin
            )

    return application


def _is_container_settings_mode(settings: Settings) -> bool:
    from .local_settings import _is_container_environment

    return _is_container_environment() and settings.allow_container_local_settings


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": jsonable_encoder(details),
                "request_id": request_id,
            }
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response
