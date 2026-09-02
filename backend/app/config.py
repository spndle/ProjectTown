from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_TRUE_ENV_BOOL_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_ENV_BOOL_TOKENS = frozenset({"0", "false", "no", "off"})
_BOOLEAN_SETTING_FIELDS = (
    "enable_v1_runtime",
    "enable_local_mcp",
    "telemetry_enabled",
    "enable_local_settings_control",
    "allow_container_local_settings",
    "enable_v3_loopback_ui",
    "enable_local_workspace_task",
    "enable_local_workspace_task_create",
    "debug",
)
_VALID_PROFILES = frozenset({"production", "development", "test"})
_VALID_SECRET_SOURCES = frozenset({"environment", "local_file"})


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in _TRUE_ENV_BOOL_TOKENS:
        return True
    if token in _FALSE_ENV_BOOL_TOKENS:
        return False
    raise ValueError(f"{name} must be one of: 1, true, yes, on, 0, false, no, off")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration.

    Paths are absolute by the time the instance is constructed. Tests and other
    hosts can inject either a ``Settings`` object or a plain mapping into
    :func:`create_app`.
    """

    database_path: Path = PROJECT_ROOT / "data" / "projecttown.db"
    sandbox_root: Path = PROJECT_ROOT / "sandbox"
    api_prefix: str = "/api/v1"
    runtime_api_prefix: str = "/api/v2"
    max_workers: int = 4
    runtime_max_workers: int = 2
    max_file_bytes: int = 1_000_000
    execution_lease_seconds: float = 30.0
    websocket_poll_seconds: float = 0.1
    watchdog_threshold: int = 3
    tool_allowlist: tuple[str, ...] = (
        "check_markdown",
        "check_python_syntax",
        "list_directory",
        "read_file",
        "write_file",
    )
    high_risk_tools: tuple[str, ...] = ()
    enable_v1_runtime: bool = True
    enable_local_mcp: bool = False
    telemetry_enabled: bool = False
    telemetry_queue_size: int = 128
    telemetry_export_timeout_seconds: float = 0.05
    telemetry_sample_every_n: int = 1
    enable_local_settings_control: bool = False
    allow_container_local_settings: bool = False
    enable_v3_loopback_ui: bool = False
    v3_work_root: Path | None = None
    enable_local_workspace_task: bool = False
    enable_local_workspace_task_create: bool = False
    local_workspace_task_root: Path | None = None
    local_workspace_task_material_root: Path | None = None
    v3_origin: str = "http://127.0.0.1:8000"
    local_settings_trusted_peer: str = ""
    profile: str = "production"
    secret_source: str = "environment"
    debug: bool = False
    version: str = "3.0.0"

    def __post_init__(self) -> None:
        for field_name in _BOOLEAN_SETTING_FIELDS:
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")  # noqa: TRY004
        if not isinstance(self.profile, str) or self.profile not in _VALID_PROFILES:
            raise ValueError("profile must be one of: production, development, test")
        if (
            not isinstance(self.secret_source, str)
            or self.secret_source not in _VALID_SECRET_SOURCES
        ):
            raise ValueError("secret_source must be one of: environment, local_file")
        database_path = self.database_path
        if str(database_path) != ":memory:":
            database_path = Path(database_path).expanduser().resolve()
        sandbox_root = Path(self.sandbox_root).expanduser().resolve()
        work_root = (
            None
            if self.v3_work_root is None
            else Path(self.v3_work_root)
            .expanduser()
            .resolve(strict=self.enable_v3_loopback_ui)
        )
        workspace_root = (
            None
            if self.local_workspace_task_root is None
            else Path(self.local_workspace_task_root)
            .expanduser()
            .resolve(strict=self.enable_local_workspace_task)
        )
        workspace_material_root = (
            None
            if self.local_workspace_task_material_root is None
            else Path(self.local_workspace_task_material_root)
            .expanduser()
            .resolve(strict=self.enable_local_workspace_task_create)
        )
        api_prefix = "/" + self.api_prefix.strip("/")
        runtime_api_prefix = "/" + self.runtime_api_prefix.strip("/")
        if runtime_api_prefix == api_prefix:
            raise ValueError("runtime_api_prefix must differ from api_prefix")
        if self.max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if self.runtime_max_workers < 1:
            raise ValueError("runtime_max_workers must be at least 1")
        if self.max_file_bytes < 1:
            raise ValueError("max_file_bytes must be at least 1")
        if self.execution_lease_seconds <= 0:
            raise ValueError("execution_lease_seconds must be positive")
        if self.websocket_poll_seconds <= 0:
            raise ValueError("websocket_poll_seconds must be positive")
        if self.watchdog_threshold < 2:
            raise ValueError("watchdog_threshold must be at least 2")
        if not 1 <= self.telemetry_queue_size <= 4_096:
            raise ValueError("telemetry_queue_size must be between 1 and 4096")
        if not 0 < self.telemetry_export_timeout_seconds <= 5.0:
            raise ValueError("telemetry_export_timeout_seconds must be between 0 and 5")
        if not 1 <= self.telemetry_sample_every_n <= 10_000:
            raise ValueError("telemetry_sample_every_n must be between 1 and 10000")
        if not self.tool_allowlist:
            raise ValueError("tool_allowlist must not be empty")
        trusted_peer = self.local_settings_trusted_peer.strip()
        if trusted_peer:
            try:
                parsed_peer = ipaddress.ip_address(trusted_peer)
            except ValueError as error:
                raise ValueError(
                    "local_settings_trusted_peer must be an IPv4 address"
                ) from error
            if parsed_peer.version != 4 or str(parsed_peer) != trusted_peer:
                raise ValueError(
                    "local_settings_trusted_peer must be a canonical IPv4 address"
                )
        if self.allow_container_local_settings and not trusted_peer:
            raise ValueError(
                "local_settings_trusted_peer is required for container local settings"
            )
        if self.enable_v3_loopback_ui:
            if Path("/.dockerenv").exists() or os.getenv("container", "").lower() in {
                "docker",
                "podman",
                "container",
            }:
                raise ValueError("enable_v3_loopback_ui is native-only")
            if work_root is None or not work_root.is_dir() or work_root.is_symlink():
                raise ValueError(
                    "enable_v3_loopback_ui requires an existing canonical v3_work_root"
                )
            try:
                directories_safe = all(
                    not candidate.is_symlink()
                    and candidate.resolve(strict=True) == candidate
                    and candidate.is_dir()
                    for candidate in (work_root / "bindings", work_root / "idempotency")
                )
            except OSError:
                directories_safe = False
            if not directories_safe:
                raise ValueError(
                    "v3_work_root requires bindings and idempotency directories"
                )
        if self.enable_v3_loopback_ui or self.enable_local_workspace_task:
            import re

            if (
                re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", self.v3_origin)
                is None
            ):
                raise ValueError("v3_origin must be http://127.0.0.1:<port>")
            if int(self.v3_origin.rsplit(":", 1)[1]) > 65535:
                raise ValueError("v3_origin port must be between 1 and 65535")
        if self.enable_local_workspace_task:
            if Path("/.dockerenv").exists() or os.getenv("container", "").lower() in {
                "docker",
                "podman",
                "container",
            }:
                raise ValueError("enable_local_workspace_task is native-only")
            if (
                workspace_root is None
                or not workspace_root.is_dir()
                or workspace_root.is_symlink()
            ):
                raise ValueError(
                    "enable_local_workspace_task requires an existing canonical local_workspace_task_root"
                )
            try:
                safe_bindings = workspace_root / "bindings"
                if not (
                    safe_bindings.is_dir()
                    and not safe_bindings.is_symlink()
                    and safe_bindings.resolve(strict=True) == safe_bindings
                ):
                    raise OSError("unsafe")
            except OSError as error:
                raise ValueError(
                    "local_workspace_task_root requires a safe bindings directory"
                ) from error
        if self.enable_local_workspace_task_create:
            if not self.enable_local_workspace_task:
                raise ValueError(
                    "enable_local_workspace_task_create requires enable_local_workspace_task"
                )
            if workspace_material_root is None:
                raise ValueError(
                    "enable_local_workspace_task_create requires local_workspace_task_material_root"
                )
            if (
                not workspace_material_root.is_dir()
                or workspace_material_root.is_symlink()
                or workspace_material_root.resolve(strict=True)
                != workspace_material_root
            ):
                raise ValueError(
                    "local_workspace_task_material_root must be an existing canonical directory"
                )
            if (
                workspace_root is None
                or workspace_root == workspace_material_root
                or workspace_root.is_relative_to(workspace_material_root)
                or workspace_material_root.is_relative_to(workspace_root)
            ):
                raise ValueError("local workspace task roots must be disjoint")
            try:
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
                directories_safe = all(
                    (workspace_root / name).is_dir()
                    and not (workspace_root / name).is_symlink()
                    and (workspace_root / name).resolve(strict=True)
                    == workspace_root / name
                    for name in required
                )
            except OSError:
                directories_safe = False
            if not directories_safe:
                raise ValueError(
                    "local_workspace_task_root requires pre-existing safe authoring directories"
                )
        object.__setattr__(self, "database_path", database_path)
        object.__setattr__(self, "sandbox_root", sandbox_root)
        object.__setattr__(self, "v3_work_root", work_root)
        object.__setattr__(self, "local_workspace_task_root", workspace_root)
        object.__setattr__(
            self, "local_workspace_task_material_root", workspace_material_root
        )
        object.__setattr__(self, "api_prefix", api_prefix)
        object.__setattr__(self, "runtime_api_prefix", runtime_api_prefix)
        object.__setattr__(self, "local_settings_trusted_peer", trusted_peer)
        object.__setattr__(
            self, "tool_allowlist", tuple(sorted(set(self.tool_allowlist)))
        )
        object.__setattr__(
            self, "high_risk_tools", tuple(sorted(set(self.high_risk_tools)))
        )

    @classmethod
    def from_env(cls) -> Settings:
        database = os.getenv(
            "PROJECTTOWN_DATABASE_PATH", str(PROJECT_ROOT / "data" / "projecttown.db")
        )
        sandbox = os.getenv("PROJECTTOWN_SANDBOX_ROOT", str(PROJECT_ROOT / "sandbox"))
        return cls(
            database_path=Path(database),
            sandbox_root=Path(sandbox),
            api_prefix=os.getenv("PROJECTTOWN_API_PREFIX", "/api/v1"),
            runtime_api_prefix=os.getenv("PROJECTTOWN_RUNTIME_API_PREFIX", "/api/v2"),
            max_workers=int(os.getenv("PROJECTTOWN_MAX_WORKERS", "4")),
            runtime_max_workers=int(os.getenv("PROJECTTOWN_RUNTIME_MAX_WORKERS", "2")),
            max_file_bytes=int(os.getenv("PROJECTTOWN_MAX_FILE_BYTES", "1000000")),
            execution_lease_seconds=float(
                os.getenv("PROJECTTOWN_EXECUTION_LEASE_SECONDS", "30")
            ),
            websocket_poll_seconds=float(
                os.getenv("PROJECTTOWN_WEBSOCKET_POLL_SECONDS", "0.1")
            ),
            watchdog_threshold=int(os.getenv("PROJECTTOWN_WATCHDOG_THRESHOLD", "3")),
            tool_allowlist=tuple(
                item.strip()
                for item in os.getenv(
                    "PROJECTTOWN_TOOL_ALLOWLIST",
                    "check_markdown,check_python_syntax,list_directory,read_file,write_file",
                ).split(",")
                if item.strip()
            ),
            high_risk_tools=tuple(
                item.strip()
                for item in os.getenv("PROJECTTOWN_HIGH_RISK_TOOLS", "").split(",")
                if item.strip()
            ),
            enable_v1_runtime=_env_bool("PROJECTTOWN_ENABLE_V1_RUNTIME", True),
            enable_local_mcp=_env_bool("PROJECTTOWN_ENABLE_LOCAL_MCP", False),
            telemetry_enabled=_env_bool("PROJECTTOWN_TELEMETRY_ENABLED", False),
            telemetry_queue_size=int(
                os.getenv("PROJECTTOWN_TELEMETRY_QUEUE_SIZE", "128")
            ),
            telemetry_export_timeout_seconds=float(
                os.getenv("PROJECTTOWN_TELEMETRY_EXPORT_TIMEOUT_SECONDS", "0.05")
            ),
            telemetry_sample_every_n=int(
                os.getenv("PROJECTTOWN_TELEMETRY_SAMPLE_EVERY_N", "1")
            ),
            enable_local_settings_control=_env_bool(
                "PROJECTTOWN_ENABLE_LOCAL_SETTINGS_CONTROL", False
            ),
            allow_container_local_settings=_env_bool(
                "PROJECTTOWN_ALLOW_CONTAINER_LOCAL_SETTINGS", False
            ),
            enable_v3_loopback_ui=_env_bool("PROJECTTOWN_ENABLE_V3_LOOPBACK_UI", False),
            v3_work_root=(
                Path(os.environ["PROJECTTOWN_V3_WORK_ROOT"])
                if "PROJECTTOWN_V3_WORK_ROOT" in os.environ
                else None
            ),
            enable_local_workspace_task=_env_bool(
                "PROJECTTOWN_ENABLE_LOCAL_WORKSPACE_TASK", False
            ),
            enable_local_workspace_task_create=_env_bool(
                "PROJECTTOWN_ENABLE_LOCAL_WORKSPACE_TASK_CREATE", False
            ),
            local_workspace_task_root=(
                Path(os.environ["PROJECTTOWN_LOCAL_WORKSPACE_TASK_ROOT"])
                if "PROJECTTOWN_LOCAL_WORKSPACE_TASK_ROOT" in os.environ
                else None
            ),
            local_workspace_task_material_root=(
                Path(os.environ["PROJECTTOWN_LOCAL_WORKSPACE_TASK_MATERIAL_ROOT"])
                if "PROJECTTOWN_LOCAL_WORKSPACE_TASK_MATERIAL_ROOT" in os.environ
                else None
            ),
            v3_origin=os.getenv("PROJECTTOWN_V3_ORIGIN", "http://127.0.0.1:8000"),
            local_settings_trusted_peer=os.getenv(
                "PROJECTTOWN_LOCAL_SETTINGS_TRUSTED_PEER", ""
            ),
            profile=os.getenv("PROJECTTOWN_PROFILE", "production"),
            secret_source=os.getenv("PROJECTTOWN_SECRET_SOURCE", "environment"),
            debug=_env_bool("PROJECTTOWN_DEBUG", False),
            version=os.getenv("PROJECTTOWN_VERSION", "3.0.0"),
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> Settings:
        allowed = {field.name for field in fields(cls)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        normalized = dict(values)
        for key in (
            "database_path",
            "sandbox_root",
            "v3_work_root",
            "local_workspace_task_root",
            "local_workspace_task_material_root",
        ):
            if key in normalized and not isinstance(normalized[key], Path):
                normalized[key] = Path(normalized[key])
        for key in ("tool_allowlist", "high_risk_tools"):
            if key in normalized and not isinstance(normalized[key], tuple):
                normalized[key] = tuple(normalized[key])
        return cls(**normalized)

    def with_overrides(self, **values: Any) -> Settings:
        return replace(self, **values)
