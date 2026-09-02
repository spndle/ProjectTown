"""Fail-closed provider connection triplet resolution for local development and tests.

The one local file intentionally contains the credential, destination, and
model as an atomic triplet.  It is ignored by Git and Docker; callers must
never serialize a connection, only its non-secret configuration hashes.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import tomllib

_LOCAL_FILE = (
    Path(__file__).resolve().parents[2] / ".secrets" / "model-providers.local.toml"
)
_MAX_LOCAL_FILE_BYTES = 65_536
_MAX_API_KEY_CHARS = 4_096
_MAX_BASE_URL_CHARS = 256
_MAX_MODEL_CHARS = 128
_PROVIDER_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DYNAMIC_PROVIDER_ENV_RE = re.compile(
    r"PROJECTTOWN_[A-Z0-9_]{1,32}_(?:API_KEY|BASE_URL|MODEL)\Z"
)
_KNOWN_PROVIDER_ENV = {
    "openai": ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"),
    "qwen": ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_MODEL"),
}
_EXAMPLE_PLACEHOLDER = "replace-with-local-development-value"
_OPENAI_ALLOWED_HOST = "api.openai.com"
_OPENAI_ALLOWED_PATH = "/v1"
_OPENAI_ALLOWED_MODEL = "gpt-5-mini-2025-08-07"
_QWEN_HOST_SUFFIX = ".cn-beijing.maas.aliyuncs.com"
_QWEN_ALLOWED_PATH = "/api/v1"
_QWEN_ALLOWED_MODEL = "qwen-plus"
_QWEN_WORKSPACE_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_CONNECTION_SCHEMA_VERSION = 3
_ADAPTER_PROTOCOLS = {"openai": "responses-v1", "qwen": "dashscope-generation-v1"}


class SecretResolutionError(RuntimeError):
    """Stable non-secret reason code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"SecretResolutionError(code={self.code!r})"


@dataclass(frozen=True)
class ResolvedProviderConnection:
    provider: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str
    source: str
    destination_config_hash: str
    connection_config_hash: str

    def __repr__(self) -> str:
        return (
            "ResolvedProviderConnection(provider="
            + repr(self.provider)
            + ", model="
            + repr(self.model)
            + ", source="
            + repr(self.source)
            + ", destination_config_hash="
            + repr(self.destination_config_hash)
            + ")"
        )


# Compatibility type name. Resolution always returns URL, key, and model together.
ProviderCredentials = ResolvedProviderConnection


def resolve_provider_connection(
    provider: str, *, environ: Mapping[str, str] | None = None
) -> ResolvedProviderConnection:
    _validate_provider(provider)
    env = os.environ if environ is None else environ
    source = env.get("PROJECTTOWN_SECRET_SOURCE", "environment")
    if source == "environment":
        base_url, api_key, model = _read_environment_connection(provider, env)
    elif source == "local_file":
        if env.get("PROJECTTOWN_PROFILE") not in {"development", "test"}:
            raise SecretResolutionError("SECRET_LOCAL_FILE_PROFILE_DENIED")
        if _has_environment_provider_credentials(env):
            raise SecretResolutionError("SECRET_SOURCE_MIXING_DENIED")
        return _read_local_file_connection(provider)
    else:
        raise SecretResolutionError("SECRET_SOURCE_INVALID")
    return _resolved_connection(provider, base_url, api_key, model, source)


def resolve_provider_credentials(
    provider: str, *, environ: Mapping[str, str] | None = None
) -> ResolvedProviderConnection:
    """Compatibility name; resolves the complete connection, never a key alone."""
    return resolve_provider_connection(provider, environ=environ)


def _validate_provider(provider: str) -> None:
    if not isinstance(provider, str) or not _PROVIDER_RE.fullmatch(provider):
        raise SecretResolutionError("SECRET_PROVIDER_INVALID")


def _read_environment_connection(
    provider: str, environ: Mapping[str, str]
) -> tuple[str, str, str]:
    names = _KNOWN_PROVIDER_ENV.get(provider)
    if names is None:
        raise SecretResolutionError("SECRET_PROVIDER_UNSUPPORTED")
    base_url, api_key, model = (environ.get(name) for name in names)
    if len({base_url is None, api_key is None, model is None}) != 1:
        raise SecretResolutionError("SECRET_CONNECTION_PARTIAL")
    if base_url is None:
        raise SecretResolutionError("SECRET_CONNECTION_REQUIRED")
    return (
        _validate_base_url_value(base_url),
        _validate_api_key(api_key),
        _validate_model_value(model, provider),
    )


def _has_environment_provider_credentials(environ: Mapping[str, str]) -> bool:
    known = {name for names in _KNOWN_PROVIDER_ENV.values() for name in names}
    return any(
        name in known or _DYNAMIC_PROVIDER_ENV_RE.fullmatch(name) for name in environ
    )


def _read_local_file_connection(provider: str) -> ResolvedProviderConnection:
    try:
        info = os.lstat(_LOCAL_FILE)
    except FileNotFoundError:
        raise SecretResolutionError("SECRET_LOCAL_FILE_MISSING") from None
    except OSError:
        raise SecretResolutionError("SECRET_LOCAL_FILE_UNREADABLE") from None
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & reparse):
        raise SecretResolutionError("SECRET_LOCAL_FILE_LINK_DENIED")
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_LOCAL_FILE_BYTES:
        raise SecretResolutionError("SECRET_LOCAL_FILE_INVALID")
    if not _validate_local_file_permissions(_LOCAL_FILE, info):
        raise SecretResolutionError("SECRET_LOCAL_FILE_PERMISSIONS_INVALID")
    try:
        document = tomllib.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise SecretResolutionError("SECRET_LOCAL_FILE_MALFORMED") from None
    return validate_provider_document(document, provider)


def _validate_local_file_permissions(
    path: Path, info: os.stat_result, *, platform: str | None = None
) -> bool:
    if (platform or os.name) == "nt":
        return _windows_acl_allows_only_local_principals(path)
    return (stat.S_IMODE(info.st_mode) & 0o077) == 0


def _windows_acl_allows_only_local_principals(path: Path) -> bool:
    """Probe only the DACL using a fixed executable and a minimal environment."""
    executable = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    script = (
        "$ErrorActionPreference='Stop';$path=$env:PROJECTTOWN_ACL_PROBE_PATH;"
        "if([string]::IsNullOrWhiteSpace($path)){exit 1};$acl=Get-Acl -LiteralPath $path;"
        "$allowed=@([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18','S-1-5-32-544');"
        "foreach($rule in $acl.Access){if($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow){"
        "$sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value;if($allowed -notcontains $sid){exit 1}}};exit 0"
    )
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "PROJECTTOWN_ACL_PROBE_PATH": str(path),
    }
    try:
        completed = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def validate_provider_document(
    document: object,
    provider: str,
    *,
    allow_unconfigured_api_key: bool = False,
) -> ResolvedProviderConnection:
    """Validate one complete v3 provider document without reading ambient state.

    This is the sole schema boundary for future local settings code.  The
    returned connection hides its key in representations and only contains
    normalized destination/model values.  The opt-in empty-key mode exists only
    for a future local settings editor; runtime resolution remains strict.
    """
    _validate_provider(provider)
    if isinstance(document, dict) and document.get("version") == 2:
        raise SecretResolutionError("MODEL_CONFIG_MIGRATION_REQUIRED")
    if (
        not isinstance(document, dict)
        or set(document) != {"version", "providers"}
        or document["version"] != _CONNECTION_SCHEMA_VERSION
        or not isinstance(document["providers"], dict)
    ):
        raise SecretResolutionError("SECRET_LOCAL_FILE_SCHEMA_INVALID")
    providers = document["providers"]
    for name, entry in providers.items():
        _validate_provider(name)
        if not isinstance(entry, dict) or set(entry) != {
            "base_url",
            "api_key",
            "model",
        }:
            raise SecretResolutionError("SECRET_LOCAL_FILE_SCHEMA_INVALID")
        base_url, api_key, model = entry["base_url"], entry["api_key"], entry["model"]
        if name != provider and base_url == "" and api_key == "" and model == "":
            continue
        if name not in _KNOWN_PROVIDER_ENV:
            raise SecretResolutionError("SECRET_PROVIDER_UNSUPPORTED")
        _validate_destination(name, _validate_base_url_value(base_url))
        _validate_api_key(
            api_key, allow_empty=allow_unconfigured_api_key and name == provider
        )
        _validate_model_value(model, name)
    if provider not in providers:
        raise SecretResolutionError("SECRET_PROVIDER_KEY_MISSING")
    entry = providers[provider]
    return _resolved_connection(
        provider,
        entry["base_url"],
        entry["api_key"],
        entry["model"],
        "local_file",
        allow_unconfigured_api_key=allow_unconfigured_api_key,
    )


def _validate_base_url_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value == _EXAMPLE_PLACEHOLDER
        or len(value) > _MAX_BASE_URL_CHARS
        or value != value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise SecretResolutionError("SECRET_BASE_URL_INVALID")
    return value


def _validate_api_key(value: object, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not value and not allow_empty)
        or value == _EXAMPLE_PLACEHOLDER
        or len(value) > _MAX_API_KEY_CHARS
        or value != value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise SecretResolutionError("SECRET_API_KEY_INVALID")
    return value


def _validate_model_value(value: object, provider: str = "openai") -> str:
    if (
        not isinstance(value, str)
        or not value
        or value == _EXAMPLE_PLACEHOLDER
        or len(value) > _MAX_MODEL_CHARS
        or value != value.strip()
        or not _MODEL_RE.fullmatch(value)
    ):
        raise SecretResolutionError("SECRET_MODEL_INVALID")
    allowed_model = {"openai": _OPENAI_ALLOWED_MODEL, "qwen": _QWEN_ALLOWED_MODEL}.get(
        provider
    )
    if allowed_model is None or value != allowed_model:
        raise SecretResolutionError("SECRET_MODEL_UNSUPPORTED")
    return value


def _validate_destination(provider: str, value: str) -> str:
    if any(ord(char) > 127 for char in value) or "\\" in value or "%" in value:
        raise SecretResolutionError("SECRET_BASE_URL_DENIED")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SecretResolutionError("SECRET_BASE_URL_DENIED") from None
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise SecretResolutionError("SECRET_BASE_URL_DENIED")
    if provider == "openai":
        if (
            parsed.hostname != _OPENAI_ALLOWED_HOST
            or parsed.netloc
            not in {_OPENAI_ALLOWED_HOST, f"{_OPENAI_ALLOWED_HOST}:443"}
            or parsed.path not in {_OPENAI_ALLOWED_PATH, f"{_OPENAI_ALLOWED_PATH}/"}
        ):
            raise SecretResolutionError("SECRET_BASE_URL_DENIED")
        return f"{parsed.scheme}://{parsed.hostname}{_OPENAI_ALLOWED_PATH}"
    if provider == "qwen":
        hostname = parsed.hostname
        if (
            hostname is None
            or not hostname.endswith(_QWEN_HOST_SUFFIX)
            or parsed.netloc not in {hostname, f"{hostname}:443"}
            or parsed.path != _QWEN_ALLOWED_PATH
        ):
            raise SecretResolutionError("SECRET_BASE_URL_DENIED")
        workspace = hostname.removesuffix(_QWEN_HOST_SUFFIX)
        if "." in workspace or not _QWEN_WORKSPACE_LABEL_RE.fullmatch(workspace):
            raise SecretResolutionError("SECRET_BASE_URL_DENIED")
        return f"{parsed.scheme}://{hostname}{_QWEN_ALLOWED_PATH}"
    raise SecretResolutionError("SECRET_PROVIDER_UNSUPPORTED")


def _destination_hash(provider: str, base_url: str) -> str:
    return hashlib.sha256(
        f"provider={provider}\nbase_url={base_url}\n".encode()
    ).hexdigest()


def _connection_config_hash(provider: str, base_url: str, model: str) -> str:
    return hashlib.sha256(
        f"schema={_CONNECTION_SCHEMA_VERSION}\nprovider={provider}\nbase_url={base_url}\n"
        f"model={model}\nadapter_protocol={_ADAPTER_PROTOCOLS[provider]}\n".encode()
    ).hexdigest()


def _resolved_connection(
    provider: str,
    base_url: object,
    api_key: object,
    model: object,
    source: str,
    *,
    allow_unconfigured_api_key: bool = False,
) -> ResolvedProviderConnection:
    normalized = _validate_destination(provider, _validate_base_url_value(base_url))
    key = _validate_api_key(api_key, allow_empty=allow_unconfigured_api_key)
    canonical_model = _validate_model_value(model, provider)
    return ResolvedProviderConnection(
        provider=provider,
        base_url=normalized,
        api_key=key,
        model=canonical_model,
        source=source,
        destination_config_hash=_destination_hash(provider, normalized),
        connection_config_hash=_connection_config_hash(
            provider, normalized, canonical_model
        ),
    )


__all__ = [
    "ProviderCredentials",
    "ResolvedProviderConnection",
    "SecretResolutionError",
    "resolve_provider_connection",
    "resolve_provider_credentials",
    "validate_provider_document",
]
