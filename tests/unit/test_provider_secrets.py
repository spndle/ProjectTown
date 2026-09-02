from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import provider_secrets
from backend.app.provider_secrets import (
    SecretResolutionError,
    resolve_provider_connection,
    validate_provider_document,
)

_URL = "https://api.openai.com/v1"
_MODEL = "gpt-5-mini-2025-08-07"
_QWEN_URL = "https://workspace-1.cn-beijing.maas.aliyuncs.com/api/v1"
_QWEN_MODEL = "qwen-plus"


def _local_env(**extra: str) -> dict[str, str]:
    return {
        "PROJECTTOWN_SECRET_SOURCE": "local_file",
        "PROJECTTOWN_PROFILE": "test",
        **extra,
    }


def _write(
    path: Path, url: str = _URL, key: str = "CANARY_KEY", model: str = _MODEL
) -> None:
    path.write_text(
        f'version = 3\n[providers.openai]\nbase_url = "{url}"\napi_key = "{key}"\nmodel = "{model}"\n',
        encoding="utf-8",
    )


def _code(callback: object) -> str:
    with pytest.raises(SecretResolutionError) as caught:
        callback()  # type: ignore[operator]
    return caught.value.code


def test_environment_requires_atomic_connection_and_repr_hides_values() -> None:
    connection = resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "CANARY",
            "OPENAI_MODEL": _MODEL,
        },
    )
    assert (
        connection.base_url == _URL
        and connection.model == _MODEL
        and connection.source == "environment"
    )
    assert "CANARY" not in repr(connection) and _URL not in repr(connection)
    assert (
        _code(lambda: resolve_provider_connection("openai", environ={}))
        == "SECRET_CONNECTION_REQUIRED"
    )
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai", environ={"OPENAI_API_KEY": "x"}
            )
        )
        == "SECRET_CONNECTION_PARTIAL"
    )
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai", environ={"OPENAI_BASE_URL": _URL}
            )
        )
        == "SECRET_CONNECTION_PARTIAL"
    )


@pytest.mark.parametrize("provider", ["", "OpenAI", "openai!", "x" * 33, 42])
def test_invalid_provider_is_rejected_before_source_resolution(
    provider: object,
) -> None:
    assert (
        _code(lambda: resolve_provider_connection(provider, environ={}))
        == "SECRET_PROVIDER_INVALID"
    )  # type: ignore[arg-type]


def test_unknown_secret_source_is_rejected() -> None:
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai", environ={"PROJECTTOWN_SECRET_SOURCE": "vault"}
            )
        )
        == "SECRET_SOURCE_INVALID"
    )


def test_environment_ignores_unrelated_variables() -> None:
    connection = resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "CANARY",
            "OPENAI_MODEL": _MODEL,
            "PATH": "unrelated",
            "OTHER_API_KEY": "ignored",
        },
    )
    assert connection.provider == "openai" and connection.source == "environment"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1",
        "https://evil.example/v1",
        "https://api.openai.com/v1/responses",
        "https://api.openai.com/v1//",
        "https://api.openai.com/v1/../v1",
        "https://api.openai.com./v1",
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "https://api.openai.com:444/v1",
        "https://user@api.openai.com/v1",
        "https://api.openai.com/v1?q=x",
        "https://api.openai.com/v1#x",
        "https://api.openai.com%2f.evil/v1",
        "https://api.openai.com\\v1",
    ],
)
def test_openai_destination_allowlist_rejects_host_and_path_evasion(url: str) -> None:
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai",
                environ={
                    "OPENAI_BASE_URL": url,
                    "OPENAI_API_KEY": "x",
                    "OPENAI_MODEL": _MODEL,
                },
            )
        )
        == "SECRET_BASE_URL_DENIED"
    )


def test_hash_depends_on_destination_not_key_and_normalizes_slash() -> None:
    first = resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "one",
            "OPENAI_MODEL": _MODEL,
        },
    )
    second = resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL + "/",
            "OPENAI_API_KEY": "two",
            "OPENAI_MODEL": _MODEL,
        },
    )
    assert first.destination_config_hash == second.destination_config_hash
    assert second.base_url == _URL


def test_connection_hash_binds_model_without_key_and_rejects_unknown_model() -> None:
    first = resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "one",
            "OPENAI_MODEL": _MODEL,
        },
    )
    second = resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": _URL,
            "OPENAI_API_KEY": "two",
            "OPENAI_MODEL": _MODEL,
        },
    )
    assert first.connection_config_hash == second.connection_config_hash
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai",
                environ={
                    "OPENAI_BASE_URL": _URL,
                    "OPENAI_API_KEY": "x",
                    "OPENAI_MODEL": "qwen-plus",
                },
            )
        )
        == "SECRET_MODEL_UNSUPPORTED"
    )


def test_pure_document_boundary_returns_only_normalized_connection() -> None:
    connection = validate_provider_document(
        {
            "version": 3,
            "providers": {
                "openai": {"base_url": _URL + "/", "api_key": "CANARY", "model": _MODEL}
            },
        },
        "openai",
    )
    assert connection.base_url == _URL and connection.model == _MODEL
    assert "CANARY" not in repr(connection) and _URL not in repr(connection)


def test_settings_document_mode_allows_only_requested_empty_key() -> None:
    document = {
        "version": 3,
        "providers": {"openai": {"base_url": _URL, "api_key": "", "model": _MODEL}},
    }
    assert (
        _code(lambda: validate_provider_document(document, "openai"))
        == "SECRET_API_KEY_INVALID"
    )
    connection = validate_provider_document(
        document, "openai", allow_unconfigured_api_key=True
    )
    assert connection.api_key == "" and connection.model == _MODEL
    for field in ("base_url", "model"):
        broken = {
            "version": 3,
            "providers": {"openai": {"base_url": _URL, "api_key": "", "model": _MODEL}},
        }
        broken["providers"]["openai"][field] = ""  # type: ignore[index]
        assert _code(
            lambda document=broken: validate_provider_document(
                document, "openai", allow_unconfigured_api_key=True
            )
        ) in {"SECRET_BASE_URL_INVALID", "SECRET_MODEL_INVALID"}


def test_local_v3_requires_triplet_and_refuses_environment_mixing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "model-providers.local.toml"
    _write(local)
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert (
        resolve_provider_connection("openai", environ=_local_env()).api_key
        == "CANARY_KEY"
    )
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai", environ=_local_env(OPENAI_BASE_URL=_URL)
            )
        )
        == "SECRET_SOURCE_MIXING_DENIED"
    )
    _write(local, key="")
    assert (
        _code(lambda: resolve_provider_connection("openai", environ=_local_env()))
        == "SECRET_API_KEY_INVALID"
    )


@pytest.mark.parametrize(
    "content",
    [
        'version = 1\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\n',
        'version = 2\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "x"\n',
        'version = 3\n[providers.openai]\nbase_url = ""\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\n',
    ],
)
def test_local_schema_is_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    local = tmp_path / "model-providers.local.toml"
    local.write_text(content, encoding="utf-8")
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert _code(
        lambda: resolve_provider_connection("openai", environ=_local_env())
    ) in {
        "MODEL_CONFIG_MIGRATION_REQUIRED",
        "SECRET_LOCAL_FILE_SCHEMA_INVALID",
        "SECRET_BASE_URL_INVALID",
    }


@pytest.mark.parametrize(
    "content",
    [
        'version = 3\nunknown = "x"\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\n',
        'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\nextra = "x"\n',
        'version = 3\n[providers.openai]\nbase_url = "replace-with-local-development-value"\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\n',
        'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "replace-with-local-development-value"\nmodel = "gpt-5-mini-2025-08-07"\n',
        'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "'
        + ("x" * (provider_secrets._MAX_API_KEY_CHARS + 1))
        + '"\nmodel = "gpt-5-mini-2025-08-07"\n',
        'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/'
        + ("v" * provider_secrets._MAX_BASE_URL_CHARS)
        + '"\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\n',
    ],
)
def test_local_schema_rejects_unknown_fields_placeholders_and_oversize_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    local = tmp_path / "model-providers.local.toml"
    local.write_text(content, encoding="utf-8")
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert _code(
        lambda: resolve_provider_connection("openai", environ=_local_env())
    ) in {
        "SECRET_LOCAL_FILE_SCHEMA_INVALID",
        "SECRET_BASE_URL_INVALID",
        "SECRET_API_KEY_INVALID",
        "SECRET_MODEL_INVALID",
    }


def test_errors_do_not_echo_url_or_key() -> None:
    url, key = "https://evil.example/v1", "CANARY_DO_NOT_LEAK"
    with pytest.raises(SecretResolutionError) as caught:
        resolve_provider_connection(
            "openai",
            environ={
                "OPENAI_BASE_URL": url,
                "OPENAI_API_KEY": key,
                "OPENAI_MODEL": _MODEL,
            },
        )
    assert url not in str(caught.value) + repr(caught.value) and key not in str(
        caught.value
    ) + repr(caught.value)


def test_dynamic_cross_provider_mixing_and_profile_are_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "model-providers.local.toml"
    _write(local)
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai", environ=_local_env(DASHSCOPE_API_KEY="x")
            )
        )
        == "SECRET_SOURCE_MIXING_DENIED"
    )
    assert (
        _code(
            lambda: resolve_provider_connection(
                "openai",
                environ={
                    "PROJECTTOWN_SECRET_SOURCE": "local_file",
                    "PROJECTTOWN_PROFILE": "production",
                },
            )
        )
        == "SECRET_LOCAL_FILE_PROFILE_DENIED"
    )


def test_local_file_missing_oversize_malformed_and_symlink_are_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "missing.toml"
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    assert (
        _code(lambda: resolve_provider_connection("openai", environ=_local_env()))
        == "SECRET_LOCAL_FILE_MISSING"
    )
    local.write_bytes(b"x" * (provider_secrets._MAX_LOCAL_FILE_BYTES + 1))
    assert (
        _code(lambda: resolve_provider_connection("openai", environ=_local_env()))
        == "SECRET_LOCAL_FILE_INVALID"
    )
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    local.write_text("not toml", encoding="utf-8")
    assert (
        _code(lambda: resolve_provider_connection("openai", environ=_local_env()))
        == "SECRET_LOCAL_FILE_MALFORMED"
    )
    target = tmp_path / "target.toml"
    _write(target)
    linked = tmp_path / "linked.toml"
    try:
        os.symlink(target, linked)
    except OSError:
        pytest.skip("symlink creation unavailable")
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", linked)
    assert (
        _code(lambda: resolve_provider_connection("openai", environ=_local_env()))
        == "SECRET_LOCAL_FILE_LINK_DENIED"
    )


def test_permission_helpers_fail_closed_and_windows_probe_is_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert provider_secrets._validate_local_file_permissions(
        Path("x"), SimpleNamespace(st_mode=stat.S_IFREG | 0o600), platform="posix"
    )
    assert not provider_secrets._validate_local_file_permissions(
        Path("x"), SimpleNamespace(st_mode=stat.S_IFREG | 0o644), platform="posix"
    )
    real_helper = provider_secrets._windows_acl_allows_only_local_principals
    monkeypatch.setattr(
        provider_secrets,
        "_windows_acl_allows_only_local_principals",
        lambda path: False,
    )
    assert not provider_secrets._validate_local_file_permissions(
        Path("x"), SimpleNamespace(st_mode=stat.S_IFREG | 0o600), platform="nt"
    )
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(provider_secrets.subprocess, "run", fake_run)
    monkeypatch.setattr(
        provider_secrets, "_windows_acl_allows_only_local_principals", real_helper
    )
    monkeypatch.setattr(provider_secrets.subprocess, "run", fake_run)
    assert provider_secrets._windows_acl_allows_only_local_principals(
        Path(r"C:\fixed\file.toml")
    )
    assert (
        captured["stdout"] is subprocess.DEVNULL
        and captured["stderr"] is subprocess.DEVNULL
    )
    assert "OPENAI_API_KEY" not in captured["env"]  # type: ignore[operator]


@pytest.mark.parametrize("outcome", ["nonzero", "oserror", "timeout"])
def test_windows_acl_probe_fails_closed_for_bad_helper_outcomes(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if outcome == "oserror":
            raise OSError("blocked")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("powershell", 3)
        return subprocess.CompletedProcess(args[0], 1)

    monkeypatch.setattr(provider_secrets.subprocess, "run", fake_run)
    assert not provider_secrets._windows_acl_allows_only_local_principals(
        Path(r"C:\fixed\file.toml")
    )


def test_windows_acl_probe_uses_fixed_executable_minimal_environment_and_literal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(provider_secrets.subprocess, "run", fake_run)
    target = Path(r"C:\fixed folder\provider.toml")
    assert provider_secrets._windows_acl_allows_only_local_principals(target)
    command = captured["command"]  # type: ignore[assignment]
    environment = captured["env"]  # type: ignore[assignment]
    assert command[0] == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"  # type: ignore[index]
    assert command[1:3] == ["-NoProfile", "-NonInteractive"]  # type: ignore[index]
    assert set(environment) == {"SystemRoot", "WINDIR", "PROJECTTOWN_ACL_PROBE_PATH"}  # type: ignore[arg-type]
    assert environment["PROJECTTOWN_ACL_PROBE_PATH"] == str(target)  # type: ignore[index]
    assert "Get-Acl -LiteralPath" in command[-1]  # type: ignore[index]
    assert captured["timeout"] == 3 and captured["check"] is False


def test_unrequested_empty_slot_allowed_but_nonempty_unsupported_is_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "providers.toml"
    local.write_text(
        f'version = 3\n[providers.openai]\nbase_url = "{_URL}"\napi_key = "x"\nmodel = "{_MODEL}"\n[providers.qwen]\nbase_url = ""\napi_key = ""\nmodel = ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert (
        resolve_provider_connection("openai", environ=_local_env()).provider == "openai"
    )
    _write(local)
    local.write_text(
        local.read_text(encoding="utf-8")
        + '[providers.qwen]\nbase_url = "https://evil.example/v1"\napi_key = "x"\nmodel = "qwen-plus"\n',
        encoding="utf-8",
    )
    assert (
        _code(lambda: resolve_provider_connection("openai", environ=_local_env()))
        == "SECRET_BASE_URL_DENIED"
    )


def test_requested_empty_reserved_provider_slot_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "providers.toml"
    local.write_text(
        'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "x"\nmodel = "gpt-5-mini-2025-08-07"\n'
        '[providers.qwen]\nbase_url = ""\napi_key = ""\nmodel = ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert (
        _code(lambda: resolve_provider_connection("qwen", environ=_local_env()))
        == "SECRET_BASE_URL_INVALID"
    )


def test_qwen_environment_triplet_and_provider_specific_hash() -> None:
    connection = resolve_provider_connection(
        "qwen",
        environ={
            "DASHSCOPE_BASE_URL": _QWEN_URL,
            "DASHSCOPE_API_KEY": "CANARY_QWEN",
            "DASHSCOPE_MODEL": _QWEN_MODEL,
        },
    )
    assert (
        connection.provider == "qwen"
        and connection.base_url == _QWEN_URL
        and connection.model == _QWEN_MODEL
    )
    assert "CANARY_QWEN" not in repr(connection)
    assert (
        _code(
            lambda: resolve_provider_connection(
                "qwen", environ={"DASHSCOPE_API_KEY": "x"}
            )
        )
        == "SECRET_CONNECTION_PARTIAL"
    )
    assert (
        connection.connection_config_hash
        != resolve_provider_connection(
            "openai",
            environ={
                "OPENAI_BASE_URL": _URL,
                "OPENAI_API_KEY": "x",
                "OPENAI_MODEL": _MODEL,
            },
        ).connection_config_hash
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/",
        "https://workspace.other.cn-beijing.maas.aliyuncs.com/api/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com:444/api/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1?q=x",
        "https://user@workspace.cn-beijing.maas.aliyuncs.com/api/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com%2f.evil/api/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com\\api/v1",
        "https://Workspace.cn-beijing.maas.aliyuncs.com/api/v1",
    ],
)
def test_qwen_destination_allowlist_rejects_evasion(url: str) -> None:
    assert (
        _code(
            lambda: resolve_provider_connection(
                "qwen",
                environ={
                    "DASHSCOPE_BASE_URL": url,
                    "DASHSCOPE_API_KEY": "x",
                    "DASHSCOPE_MODEL": _QWEN_MODEL,
                },
            )
        )
        == "SECRET_BASE_URL_DENIED"
    )


def test_qwen_document_is_accepted_and_mixed_local_source_still_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "providers.toml"
    local.write_text(
        f'version = 3\n[providers.openai]\nbase_url = ""\napi_key = ""\nmodel = ""\n'
        f'[providers.qwen]\nbase_url = "{_QWEN_URL}"\napi_key = "CANARY_QWEN"\nmodel = "{_QWEN_MODEL}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_secrets, "_LOCAL_FILE", local)
    monkeypatch.setattr(
        provider_secrets, "_validate_local_file_permissions", lambda path, info: True
    )
    assert (
        resolve_provider_connection("qwen", environ=_local_env()).base_url == _QWEN_URL
    )
    assert (
        _code(
            lambda: resolve_provider_connection(
                "qwen", environ=_local_env(DASHSCOPE_MODEL=_QWEN_MODEL)
            )
        )
        == "SECRET_SOURCE_MIXING_DENIED"
    )
