"""Loopback-only local model settings control plane.

This module is deliberately separate from both public Quest API prefixes.  It
only edits the ignored local provider file and never starts a provider client.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import stat
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import PROJECT_ROOT, Settings
from .provider_secrets import SecretResolutionError, validate_provider_document

LOCAL_SETTINGS_PATH = "/local/settings/v1/providers/openai"
QWEN_LOCAL_SETTINGS_PATH = "/local/settings/v1/providers/qwen"
SETTINGS_TOKEN_HEADER = "X-ProjectTown-Settings-Token"
_MAX_BODY_BYTES = 16 * 1024
_OPENAI_URL = "https://api.openai.com/v1"
_OPENAI_MODEL = "gpt-5-mini-2025-08-07"
_QWEN_MODEL = "qwen-plus"
WINDOWS_ACL_TIMEOUT_SECONDS = 3
WINDOWS_ACL_TRACE_MARKERS = (
    "START",
    "ITEM_VALIDATED",
    "IDENTITY_READY",
    "DESCRIPTOR_READ",
    "OWNER_VALIDATED",
    "EXISTING_RULES_ENUMERATED",
    "DACL_PREPARED",
    "DACL_APPLIED",
    "VERIFIED_ITEM",
    "VERIFIED_DESCRIPTOR",
    "VERIFIED_RULES_ENUMERATED",
    "COMPLETE",
)
_SUPPORTED_PROVIDERS = frozenset({"openai", "qwen"})
_TOKEN_BYTES = 32
_TOKEN_PATTERN_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class LocalSettingsError(RuntimeError):
    """Stable, non-secret local settings failure."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def local_settings_route_enabled(settings: Settings) -> bool:
    base_enabled = (
        settings.enable_local_settings_control
        and settings.profile in {"development", "test"}
        and settings.secret_source == "local_file"
    )
    if not base_enabled:
        return False
    if not _is_container_environment():
        return True
    return (
        settings.allow_container_local_settings
        and _canonical_ipv4(settings.local_settings_trusted_peer)
        == settings.local_settings_trusted_peer
    )


def _is_container_environment() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("container", "").lower() in {
        "docker",
        "podman",
        "container",
    }


class LocalSettingsService:
    """Small filesystem-only service with optimistic revision control."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        allow_test_client: bool = False,
        container_mode: bool = False,
        trusted_peer: str = "",
    ) -> None:
        base = PROJECT_ROOT if root is None else Path(root).resolve()
        self._root = base
        self._secrets_dir = base / ".secrets"
        self._settings_path = base / ".secrets" / "model-providers.local.toml"
        self._token_path = base / ".secrets" / "projecttown-settings-session.token"
        self._allow_test_client = allow_test_client
        self._container_mode = container_mode
        self._trusted_peer = trusted_peer
        self._token: str | None = None
        self._token_file_content: bytes | None = None
        self._fingerprint: str | None = None
        self._revision: str | None = None
        self._lock = threading.RLock()
        self._secrets_identity: tuple[int, int] | None = None
        self._container_lock_fd: int | None = None
        self._container_lock_path = (
            base / ".secrets" / ".projecttown-settings-session.lock"
        )

    @property
    def token(self) -> str:
        if self._token is None:
            raise RuntimeError("local settings service is not started")
        return self._token

    def start(self) -> None:
        with self._lock:
            try:
                self._ensure_secure_directories(create=True)
                if self._container_mode:
                    self._acquire_container_lock()
                token = secrets.token_urlsafe(_TOKEN_BYTES)
                payload = token.encode("ascii")
                if self._container_mode and self._token_path.exists():
                    self._rotate_stale_token(payload)
                else:
                    self._write_new_restricted_file(self._token_path, payload)
                self._token = token
                self._token_file_content = payload
                self._refresh_revision_locked()
            except LocalSettingsError:
                self._release_container_lock()
                raise
            except (OSError, ValueError, subprocess.SubprocessError):
                self._release_container_lock()
                raise LocalSettingsError(
                    "LOCAL_SETTINGS_FILESYSTEM_ERROR", 503
                ) from None

    def close(self) -> None:
        with self._lock:
            if self._token_file_content is None:
                return
            try:
                self._ensure_secure_directories(create=False)
                info = os.lstat(self._token_path)
                if _regular_nonreparse(info):
                    current = self._token_path.read_bytes()
                    if hmac.compare_digest(current, self._token_file_content):
                        self._token_path.unlink()
            except (OSError, LocalSettingsError):
                pass
            finally:
                self._token = None
                self._token_file_content = None
                self._release_container_lock()

    def verify_token(self, supplied: str | None) -> bool:
        return (
            isinstance(supplied, str)
            and self._token is not None
            and hmac.compare_digest(supplied, self._token)
        )

    def get(self, provider: str = "openai") -> dict[str, Any]:
        _validate_supported_provider(provider)
        with self._lock:
            try:
                return self._get_locked(provider)
            except LocalSettingsError:
                raise
            except (OSError, ValueError, subprocess.SubprocessError):
                raise LocalSettingsError(
                    "LOCAL_SETTINGS_FILESYSTEM_ERROR", 503
                ) from None

    def _get_locked(self, provider: str) -> dict[str, Any]:
        document, fingerprint = self._read_document()
        self._set_revision_if_changed(fingerprint)
        # Validate the complete document through the stable provider boundary.
        # Qwen is permitted to remain an entirely empty, not-yet-configured
        # triplet while OpenAI is being edited.
        _validate_document(document, "openai")
        entry = _provider_entry(document, provider)
        if provider == "qwen" and all(
            entry[field] == "" for field in ("base_url", "api_key", "model")
        ):
            return {
                "provider": provider,
                "base_url": "",
                "model": "",
                "api_key_configured": False,
                "revision": self._revision,
                "base_url_options": [],
                "model_options": [_QWEN_MODEL],
                "base_url_configurable": True,
                "runtime_supported": True,
                "live_authorized": False,
            }
        connection = _validate_document(document, provider)
        return {
            "provider": provider,
            "base_url": connection.base_url,
            "model": connection.model,
            "api_key_configured": bool(connection.api_key),
            "revision": self._revision,
            "base_url_options": [_OPENAI_URL]
            if provider == "openai"
            else [connection.base_url],
            "model_options": [_OPENAI_MODEL] if provider == "openai" else [_QWEN_MODEL],
            "runtime_supported": True,
            **(
                {"base_url_configurable": True, "live_authorized": False}
                if provider == "qwen"
                else {}
            ),
        }

    def put(
        self, payload: Mapping[str, Any], provider: str = "openai"
    ) -> dict[str, Any]:
        _validate_supported_provider(provider)
        required = {
            "base_url",
            "model",
            "api_key_action",
            "api_key",
            "expected_revision",
        }
        if set(payload) != required:
            raise LocalSettingsError("LOCAL_SETTINGS_BODY_INVALID")
        action = payload["api_key_action"]
        if action not in {"keep", "replace", "clear"}:
            raise LocalSettingsError("LOCAL_SETTINGS_KEY_ACTION_INVALID")
        if not isinstance(payload["expected_revision"], str):
            raise LocalSettingsError("LOCAL_SETTINGS_REVISION_INVALID")
        if action == "replace" and (
            not isinstance(payload["api_key"], str)
            or not payload["api_key"]
            or payload["api_key"] != payload["api_key"].strip()
        ):
            raise LocalSettingsError("LOCAL_SETTINGS_BODY_INVALID")
        with self._lock:
            try:
                return self._put_locked(payload, action, provider)
            except LocalSettingsError:
                raise
            except (OSError, ValueError, subprocess.SubprocessError):
                raise LocalSettingsError(
                    "LOCAL_SETTINGS_FILESYSTEM_ERROR", 503
                ) from None

    def _put_locked(
        self, payload: Mapping[str, Any], action: object, provider: str
    ) -> dict[str, Any]:
        current_document, expected_fingerprint = self._read_document()
        self._set_revision_if_changed(expected_fingerprint)
        if not hmac.compare_digest(payload["expected_revision"], self._revision or ""):
            raise LocalSettingsError("LOCAL_SETTINGS_REVISION_CONFLICT", 409)
        if action == "keep":
            if payload["api_key"] is not None:
                raise LocalSettingsError("LOCAL_SETTINGS_BODY_INVALID")
            key = _document_key(current_document, provider)
        elif action == "clear":
            if payload["api_key"] is not None:
                raise LocalSettingsError("LOCAL_SETTINGS_BODY_INVALID")
            key = ""
        else:
            key = payload["api_key"]
        document = {
            "version": 3,
            "providers": {
                name: dict(_provider_entry(current_document, name))
                for name in _SUPPORTED_PROVIDERS
            },
        }
        document["providers"][provider] = {
            "base_url": payload["base_url"],
            "api_key": key,
            "model": payload["model"],
        }
        _validate_document(document, provider)
        encoded = _canonical_document(document)
        self._atomic_write_settings(encoded, expected_fingerprint)
        self._refresh_revision_locked()
        return self._get_locked(provider)

    def _read_document(self) -> tuple[dict[str, Any], str]:
        self._ensure_secure_directories(create=False)
        try:
            info = os.lstat(self._settings_path)
        except FileNotFoundError:
            document = _empty_document()
            return document, _fingerprint(_canonical_document(document))
        except OSError:
            raise LocalSettingsError("LOCAL_SETTINGS_FILE_UNREADABLE") from None
        if not _regular_nonreparse(info):
            raise LocalSettingsError("LOCAL_SETTINGS_FILE_INVALID")
        if not _restricted_permissions(self._settings_path, info):
            raise LocalSettingsError("LOCAL_SETTINGS_FILE_PERMISSIONS_INVALID")
        try:
            raw = self._settings_path.read_bytes()
            document = _toml_document(raw)
        except (OSError, UnicodeDecodeError, ValueError):
            raise LocalSettingsError("LOCAL_SETTINGS_FILE_INVALID") from None
        return document, _fingerprint(raw)

    def _set_revision_if_changed(self, fingerprint: str) -> None:
        if self._fingerprint != fingerprint:
            self._fingerprint = fingerprint
            self._revision = secrets.token_urlsafe(24)

    def _refresh_revision_locked(self) -> None:
        _, fingerprint = self._read_document()
        self._set_revision_if_changed(fingerprint)

    def _atomic_write_settings(self, payload: bytes, expected_fingerprint: str) -> None:
        self._ensure_secure_directories(create=True)
        parent = self._settings_path.parent
        temp = parent / f".{self._settings_path.name}.{secrets.token_hex(16)}.tmp"
        fd: int | None = None
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _set_restricted_permissions(temp)
            self._before_replace()
            if self._current_fingerprint() != expected_fingerprint:
                raise LocalSettingsError("LOCAL_SETTINGS_REVISION_CONFLICT", 409)
            self._ensure_secure_directories(create=False)
            os.replace(temp, self._settings_path)
            _set_restricted_permissions(self._settings_path)
            document, _ = self._read_document()
            _validate_document(document)
        except Exception:
            if fd is not None:
                os.close(fd)
            try:
                if temp.exists() and temp.is_file() and not temp.is_symlink():
                    temp.unlink()
            except OSError:
                pass
            raise

    def _before_replace(self) -> None:
        """Test seam; production leaves the checked window empty."""

    def _current_fingerprint(self) -> str:
        _, fingerprint = self._read_document()
        return fingerprint

    def _write_new_restricted_file(self, path: Path, payload: bytes) -> None:
        if path.exists():
            raise LocalSettingsError("LOCAL_SETTINGS_TOKEN_EXISTS", 503)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _set_restricted_permissions(path)
            info = os.lstat(path)
            if not _regular_nonreparse(info) or not _restricted_permissions(path, info):
                raise LocalSettingsError(
                    "LOCAL_SETTINGS_TOKEN_PERMISSIONS_INVALID", 503
                )
        except Exception:
            try:
                if path.exists() and path.is_file() and not path.is_symlink():
                    path.unlink()
            except OSError:
                pass
            raise

    def _acquire_container_lock(self) -> None:
        if self._container_lock_fd is not None:
            raise LocalSettingsError("LOCAL_SETTINGS_INSTANCE_LOCKED", 503)
        if os.name == "nt":
            raise LocalSettingsError("LOCAL_SETTINGS_CONTAINER_LOCK_UNSUPPORTED", 503)
        import fcntl

        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self._container_lock_path, flags, 0o600)
        try:
            before = os.fstat(fd)
            after = os.lstat(self._container_lock_path)
            if (
                not _regular_nonreparse(after)
                or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
            ):
                raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
            _set_restricted_permissions(self._container_lock_path)
            checked = os.lstat(self._container_lock_path)
            if not _restricted_permissions(self._container_lock_path, checked):
                raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LocalSettingsError(
                    "LOCAL_SETTINGS_INSTANCE_LOCKED", 503
                ) from None
            self._container_lock_fd = fd
        except Exception:
            os.close(fd)
            raise

    def _release_container_lock(self) -> None:
        fd = self._container_lock_fd
        self._container_lock_fd = None
        if fd is None:
            return
        try:
            if os.name != "nt":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _rotate_stale_token(self, payload: bytes) -> None:
        info = os.lstat(self._token_path)
        if not _regular_nonreparse(info) or not _restricted_permissions(
            self._token_path, info
        ):
            raise LocalSettingsError("LOCAL_SETTINGS_TOKEN_PERMISSIONS_INVALID", 503)
        stale = self._token_path.read_bytes()
        if not _valid_stale_token(stale):
            raise LocalSettingsError("LOCAL_SETTINGS_TOKEN_INVALID", 503)
        temp = (
            self._token_path.parent
            / f".{self._token_path.name}.{secrets.token_hex(16)}.tmp"
        )
        try:
            self._write_new_restricted_file(temp, payload)
            current = os.lstat(self._token_path)
            if current.st_dev != info.st_dev or current.st_ino != info.st_ino:
                raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
            os.replace(temp, self._token_path)
            _set_restricted_permissions(self._token_path)
            final = os.lstat(self._token_path)
            if not _regular_nonreparse(final) or not _restricted_permissions(
                self._token_path, final
            ):
                raise LocalSettingsError(
                    "LOCAL_SETTINGS_TOKEN_PERMISSIONS_INVALID", 503
                )
        except Exception:
            try:
                if temp.exists() and temp.is_file() and not temp.is_symlink():
                    temp.unlink()
            except OSError:
                pass
            raise

    def _ensure_secure_directories(self, *, create: bool) -> None:
        try:
            root_info = os.lstat(self._root)
        except OSError:
            raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503) from None
        if not _directory_nonreparse(root_info):
            raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
        if not self._secrets_dir.exists():
            if not create:
                return
            self._secrets_dir.mkdir(mode=0o700)
        try:
            info = os.lstat(self._secrets_dir)
        except OSError:
            raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503) from None
        if not _directory_nonreparse(info):
            raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
        if create or self._secrets_identity is None:
            try:
                _set_restricted_directory_permissions(self._secrets_dir)
            except (OSError, subprocess.SubprocessError):
                raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503) from None
            info = os.lstat(self._secrets_dir)
            if not _restricted_directory_permissions(self._secrets_dir, info):
                raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
        if self._secrets_dir.resolve().parent != self._root.resolve():
            raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
        identity = (info.st_dev, info.st_ino)
        if self._secrets_identity is not None and identity != self._secrets_identity:
            raise LocalSettingsError("LOCAL_SETTINGS_PATH_DENIED", 503)
        self._secrets_identity = identity


def _empty_document() -> dict[str, Any]:
    return {
        "version": 3,
        "providers": {
            "openai": {"base_url": _OPENAI_URL, "api_key": "", "model": _OPENAI_MODEL},
            "qwen": {"base_url": "", "api_key": "", "model": ""},
        },
    }


def _canonical_document(document: Mapping[str, Any]) -> bytes:
    openai = document["providers"]["openai"]
    qwen = document["providers"]["qwen"]
    return (
        "version = 3\n\n[providers.openai]\n"
        f"base_url = {json.dumps(openai['base_url'])}\napi_key = {json.dumps(openai['api_key'])}\nmodel = {json.dumps(openai['model'])}\n\n"
        "[providers.qwen]\n"
        f"base_url = {json.dumps(qwen['base_url'])}\napi_key = {json.dumps(qwen['api_key'])}\nmodel = {json.dumps(qwen['model'])}\n"
    ).encode()


def _toml_document(raw: bytes) -> dict[str, Any]:
    import tomllib

    value = tomllib.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("not a document")
    return value


def _fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _document_key(document: Mapping[str, Any], provider: str) -> str:
    try:
        key = document["providers"][provider]["api_key"]
    except (KeyError, TypeError):
        raise LocalSettingsError("LOCAL_SETTINGS_FILE_INVALID") from None
    if not isinstance(key, str):
        raise LocalSettingsError("LOCAL_SETTINGS_FILE_INVALID")
    return key


def _validate_document(document: object, provider: str = "openai"):
    try:
        if not isinstance(document, dict) or not isinstance(
            document.get("providers"), dict
        ):
            return validate_provider_document(
                document, provider, allow_unconfigured_api_key=True
            )
        # The local editor can retain an unconfigured key for the provider not
        # being edited.  Validate its destination/model using a disposable
        # in-memory placeholder; runtime resolution remains strict.
        editor_document = {
            "version": document.get("version"),
            "providers": {
                name: dict(entry) if isinstance(entry, Mapping) else entry
                for name, entry in document["providers"].items()
            },
        }
        for name, entry in editor_document["providers"].items():
            if name == provider or not isinstance(entry, dict):
                continue
            if (
                entry.get("api_key") == ""
                and entry.get("base_url")
                and entry.get("model")
            ):
                entry["api_key"] = "editor-validation-placeholder"
        return validate_provider_document(
            editor_document, provider, allow_unconfigured_api_key=True
        )
    except SecretResolutionError as error:
        raise LocalSettingsError(error.code) from None


def _validate_supported_provider(provider: str) -> None:
    if provider not in _SUPPORTED_PROVIDERS:
        raise LocalSettingsError("LOCAL_SETTINGS_PROVIDER_UNSUPPORTED", 404)


def _provider_entry(document: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    try:
        entry = document["providers"][provider]
    except (KeyError, TypeError):
        raise LocalSettingsError("LOCAL_SETTINGS_FILE_INVALID") from None
    if not isinstance(entry, Mapping) or set(entry) != {"base_url", "api_key", "model"}:
        raise LocalSettingsError("LOCAL_SETTINGS_FILE_INVALID")
    return entry


def _regular_nonreparse(info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not (getattr(info, "st_file_attributes", 0) & reparse)
    )


def _directory_nonreparse(info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not (getattr(info, "st_file_attributes", 0) & reparse)
    )


def _restricted_permissions(path: Path, info: os.stat_result) -> bool:
    if os.name != "nt":
        return (stat.S_IMODE(info.st_mode) & 0o077) == 0
    return _windows_acl_is_restricted(path)


def _restricted_directory_permissions(path: Path, info: os.stat_result) -> bool:
    if os.name != "nt":
        return (stat.S_IMODE(info.st_mode) & 0o077) == 0
    return _windows_acl_is_restricted(path)


def _set_restricted_directory_permissions(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o700)
        return
    _icacls_restrict(path, directory=True)


def _set_restricted_permissions(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    _icacls_restrict(path, directory=False)


def _icacls_restrict(path: Path, *, directory: bool) -> None:
    before = _acl_target_identity(path, directory=directory)
    executable = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    try:
        _run_windows_acl(executable, _windows_acl_restrict_script(directory), path)
        after = _acl_target_identity(path, directory=directory)
    except OSError:
        raise OSError("local settings ACL operation failed") from None
    if before != after or not _windows_acl_is_restricted(path):
        raise OSError("local settings ACL operation failed")


def _acl_target_identity(path: Path, *, directory: bool) -> tuple[int, int]:
    try:
        info = os.lstat(path)
    except OSError:
        raise OSError("local settings ACL operation failed") from None
    expected = _directory_nonreparse(info) if directory else _regular_nonreparse(info)
    if not expected:
        raise OSError("local settings ACL operation failed")
    return info.st_dev, info.st_ino


def _windows_acl_restrict_script(directory: bool, *, trace: bool = False) -> str:
    inheritance = (
        "[System.Security.AccessControl.InheritanceFlags]::ObjectInherit -bor "
        "[System.Security.AccessControl.InheritanceFlags]::ContainerInherit"
        if directory
        else "[System.Security.AccessControl.InheritanceFlags]::None"
    )
    expected_container = "$true" if directory else "$false"
    expected_type = "[IO.DirectoryInfo]" if directory else "[IO.FileInfo]"

    def marker(stage: str) -> str:
        return f"Write-Output 'PROJECTTOWN_ACL_TRACE:{stage}';" if trace else ""

    return (
        marker("START")
        +
        "$ErrorActionPreference='Stop';$p=$env:PROJECTTOWN_LOCAL_SETTINGS_PATH;"
        "if([string]::IsNullOrWhiteSpace($p)){throw 'missing path'};"
        "$item=Get-Item -LiteralPath $p -Force;if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'reparse path'};"
        f"if(-not ($item -is {expected_type}) -or $item.PSIsContainer -ne {expected_container}){{throw 'invalid path type'}};"
        + marker("ITEM_VALIDATED")
        +
        "$currentSid=[Security.Principal.WindowsIdentity]::GetCurrent().User;"
        + marker("IDENTITY_READY")
        +
        "$allowedOwners=@($currentSid.Value,'S-1-5-18','S-1-5-32-544');$sections=[Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner -bor [Security.AccessControl.AccessControlSections]::Group;"
        "$existing=$item.GetAccessControl($sections);$ownerBefore=$existing.GetOwner([Security.Principal.SecurityIdentifier]).Value;$groupBefore=$existing.GetGroup([Security.Principal.SecurityIdentifier]).Value;"
        + marker("DESCRIPTOR_READ")
        +
        "if($allowedOwners -notcontains $ownerBefore){throw 'owner denied'};"
        + marker("OWNER_VALIDATED")
        +
        "$existing.SetAccessRuleProtection($true,$false);"
        "$existingRules=@($existing.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]));foreach($rule in $existingRules){if(-not $rule.IsInherited -and ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -or $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny)){$null=$existing.RemoveAccessRuleSpecific($rule)}};"
        + marker("EXISTING_RULES_ENUMERATED")
        +
        "$acl=$existing;"
        f"$inheritance={inheritance};"
        "$rule=New-Object Security.AccessControl.FileSystemAccessRule($currentSid,[Security.AccessControl.FileSystemRights]::FullControl,$inheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow);"
        "$acl.AddAccessRule($rule);"
        + marker("DACL_PREPARED")
        +
        "$item.SetAccessControl($acl);"
        + marker("DACL_APPLIED")
        +
        f"$verifiedItem=Get-Item -LiteralPath $p -Force;if(($verifiedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or -not ($verifiedItem -is {expected_type}) -or $verifiedItem.PSIsContainer -ne $item.PSIsContainer){{throw 'ACL verification failed'}};"
        + marker("VERIFIED_ITEM")
        +
        "$verified=$verifiedItem.GetAccessControl($sections);$ownerAfter=$verified.GetOwner([Security.Principal.SecurityIdentifier]).Value;$groupAfter=$verified.GetGroup([Security.Principal.SecurityIdentifier]).Value;"
        + marker("VERIFIED_DESCRIPTOR")
        +
        "if(-not $verified.AreAccessRulesProtected -or $ownerAfter -ne $ownerBefore -or $groupAfter -ne $groupBefore -or $allowedOwners -notcontains $ownerAfter){throw 'ACL verification failed'};"
        "$verifiedRules=@($verified.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]));$currentAllowCount=0;foreach($rule in $verifiedRules){if($rule.IsInherited -or $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny){throw 'ACL verification failed'};if($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow){throw 'ACL verification failed'};$sid=$rule.IdentityReference.Value;if($sid -ne $currentSid.Value -or (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) -or $rule.InheritanceFlags -ne $inheritance){throw 'ACL verification failed'};$currentAllowCount++};"
        + marker("VERIFIED_RULES_ENUMERATED")
        +
        "if($currentAllowCount -ne 1){throw 'ACL verification failed'}"
        + marker("COMPLETE")
    )


def _windows_acl_is_restricted(path: Path) -> bool:
    executable = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    script = (
        "$ErrorActionPreference='Stop';$p=$env:PROJECTTOWN_LOCAL_SETTINGS_PATH;"
        "if([string]::IsNullOrWhiteSpace($p)){exit 1};$item=Get-Item -LiteralPath $p -Force;$sections=[Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner -bor [Security.AccessControl.AccessControlSections]::Group;$acl=$item.GetAccessControl($sections);"
        "if(-not $acl.AreAccessRulesProtected){exit 1};$current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$allowed=@($current,'S-1-5-18','S-1-5-32-544');$owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value;if($allowed -notcontains $owner){exit 1};$currentFull=$false;$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]));"
        "foreach($r in $rules){if($r.AccessControlType -eq 'Allow'){$sid=$r.IdentityReference.Value;if($allowed -notcontains $sid){exit 1};if($sid -eq $current -and (($r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl)){$currentFull=$true}}};if(-not $currentFull){exit 1};exit 0"
    )
    try:
        _run_windows_acl(executable, script, path)
    except OSError:
        return False
    return True


def _run_windows_acl(executable: Path, script: str, path: Path) -> None:
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "PROJECTTOWN_LOCAL_SETTINGS_PATH": str(path),
    }
    try:
        completed = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=WINDOWS_ACL_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        raise OSError("local settings ACL operation failed") from None
    if completed.returncode != 0:
        raise OSError("local settings ACL operation failed")


def install_local_settings_routes(
    application: FastAPI, service: LocalSettingsService
) -> None:
    async def guarded(request: Request) -> JSONResponse | None:
        if request.method not in {"GET", "PUT"}:
            return _error("LOCAL_SETTINGS_METHOD_DENIED", 405)
        host = request.headers.get("host", "")
        client = request.client.host if request.client else ""
        allowed = {"127.0.0.1", "::1", "localhost"}
        expected_client = service._trusted_peer if service._container_mode else None
        client_allowed = (
            client == expected_client
            if expected_client is not None
            else client in allowed
            or (service._allow_test_client and client == "testclient")
        )
        if not _valid_loopback_host(host) or not client_allowed:
            return _error("LOCAL_SETTINGS_LOOPBACK_REQUIRED", 403)
        if (
            request.query_params
            or request.headers.get("cookie")
            or request.headers.get("origin")
        ):
            return _error("LOCAL_SETTINGS_REQUEST_DENIED", 403)
        if not service.verify_token(request.headers.get(SETTINGS_TOKEN_HEADER)):
            return _error("LOCAL_SETTINGS_TOKEN_DENIED", 403)
        return None

    async def get_settings(request: Request, provider: str) -> JSONResponse:
        denied = await guarded(request)
        if denied:
            return denied
        try:
            return JSONResponse(service.get(provider))
        except LocalSettingsError as error:
            return _error(error.code, error.status_code)

    async def put_settings(request: Request, provider: str) -> JSONResponse:
        denied = await guarded(request)
        if denied:
            return denied
        if request.headers.get("content-type") != "application/json":
            return _error("LOCAL_SETTINGS_CONTENT_TYPE_INVALID", 415)
        length = request.headers.get("content-length")
        if length is not None and (
            not length.isdigit() or int(length) > _MAX_BODY_BYTES
        ):
            return _error("LOCAL_SETTINGS_BODY_INVALID", 413)
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            return _error("LOCAL_SETTINGS_BODY_INVALID", 413)
        try:
            payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return _error("LOCAL_SETTINGS_BODY_INVALID", 400)
        if not isinstance(payload, dict):
            return _error("LOCAL_SETTINGS_BODY_INVALID", 400)
        try:
            return JSONResponse(service.put(payload, provider))
        except LocalSettingsError as error:
            return _error(error.code, error.status_code)

    def get_route_for(provider: str):
        async def get_route(request: Request) -> JSONResponse:
            return await get_settings(request, provider)

        return get_route

    def put_route_for(provider: str):
        async def put_route(request: Request) -> JSONResponse:
            return await put_settings(request, provider)

        return put_route

    for provider, path in (
        ("openai", LOCAL_SETTINGS_PATH),
        ("qwen", QWEN_LOCAL_SETTINGS_PATH),
    ):
        application.add_api_route(
            path,
            get_route_for(provider),
            methods=["GET"],
            response_class=JSONResponse,
        )
        application.add_api_route(
            path,
            put_route_for(provider),
            methods=["PUT"],
            response_class=JSONResponse,
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code}})


def _valid_loopback_host(value: str) -> bool:
    if not value or any(char in value for char in "@,/?#"):
        return False
    host = value
    port: str | None = None
    if value.startswith("["):
        if not value.startswith("[::1]"):
            return False
        remainder = value[5:]
        if remainder:
            if not remainder.startswith(":"):
                return False
            port = remainder[1:]
        host = "::1"
    elif value.count(":") == 0:
        host = value
    elif value.count(":") == 1:
        host, port = value.split(":", 1)
    else:
        return False
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    if port is None:
        return True
    return port.isascii() and port.isdigit() and 1 <= int(port) <= 65_535


def _canonical_ipv4(value: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    return str(parsed) if parsed.version == 4 else None


def _valid_stale_token(value: bytes) -> bool:
    return len(value) == 43 and all(byte in _TOKEN_PATTERN_BYTES for byte in value)
