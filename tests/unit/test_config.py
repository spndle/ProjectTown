from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.config import Settings, _env_bool
from backend.app.main import create_app

BOOLEAN_ENV_FIELDS = (
    ("enable_v1_runtime", "PROJECTTOWN_ENABLE_V1_RUNTIME", True),
    ("enable_local_mcp", "PROJECTTOWN_ENABLE_LOCAL_MCP", False),
    ("telemetry_enabled", "PROJECTTOWN_TELEMETRY_ENABLED", False),
    (
        "enable_local_settings_control",
        "PROJECTTOWN_ENABLE_LOCAL_SETTINGS_CONTROL",
        False,
    ),
    (
        "allow_container_local_settings",
        "PROJECTTOWN_ALLOW_CONTAINER_LOCAL_SETTINGS",
        False,
    ),
    ("debug", "PROJECTTOWN_DEBUG", False),
)
TRUE_TOKENS = ("1", "true", "yes", "on")
FALSE_TOKENS = ("0", "false", "no", "off")


@pytest.mark.parametrize(("field", "env_name", "default"), BOOLEAN_ENV_FIELDS)
def test_boolean_environment_defaults(
    monkeypatch: pytest.MonkeyPatch, field: str, env_name: str, default: bool
) -> None:
    monkeypatch.delenv(env_name, raising=False)
    assert getattr(Settings.from_env(), field) is default


@pytest.mark.parametrize(("field", "env_name", "default"), BOOLEAN_ENV_FIELDS)
@pytest.mark.parametrize("token", TRUE_TOKENS)
def test_boolean_environment_true_tokens(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    env_name: str,
    default: bool,
    token: str,
) -> None:
    del default
    monkeypatch.setenv(env_name, f"  {token.upper()}  ")
    if field == "allow_container_local_settings":
        monkeypatch.setenv("PROJECTTOWN_LOCAL_SETTINGS_TRUSTED_PEER", "172.30.250.1")
    assert getattr(Settings.from_env(), field) is True


@pytest.mark.parametrize(("field", "env_name", "default"), BOOLEAN_ENV_FIELDS)
@pytest.mark.parametrize("token", FALSE_TOKENS)
def test_boolean_environment_false_tokens(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    env_name: str,
    default: bool,
    token: str,
) -> None:
    del default
    monkeypatch.setenv(env_name, f"  {token.upper()}  ")
    assert getattr(Settings.from_env(), field) is False


@pytest.mark.parametrize(("field", "env_name", "default"), BOOLEAN_ENV_FIELDS)
@pytest.mark.parametrize(
    ("raw", "assert_not_echoed"),
    (("", False), (" ", False), ("2", True), ("CANARY_INVALID", True)),
)
def test_boolean_environment_rejects_invalid_values_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    env_name: str,
    default: bool,
    raw: str,
    assert_not_echoed: bool,
) -> None:
    del field, default
    monkeypatch.setenv(env_name, raw)
    with pytest.raises(ValueError) as raised:
        _env_bool(env_name, False)
    message = str(raised.value)
    assert message == f"{env_name} must be one of: 1, true, yes, on, 0, false, no, off"
    if assert_not_echoed:
        assert raw not in message


@pytest.mark.parametrize(("field", "env_name", "default"), BOOLEAN_ENV_FIELDS)
@pytest.mark.parametrize("invalid", ("true", 1, 0, None, object()))
def test_boolean_settings_reject_non_bool_from_mapping_direct_and_overrides(
    tmp_path: Path,
    field: str,
    env_name: str,
    default: bool,
    invalid: object,
) -> None:
    del env_name, default
    base = {
        "database_path": tmp_path / "db.sqlite",
        "sandbox_root": tmp_path / "sandbox",
    }
    with pytest.raises(ValueError, match=rf"^{field} must be a bool$"):
        Settings.from_mapping({**base, field: invalid})
    with pytest.raises(ValueError, match=rf"^{field} must be a bool$"):
        Settings(**{field: invalid})
    with pytest.raises(ValueError, match=rf"^{field} must be a bool$"):
        Settings().with_overrides(**{field: invalid})


@pytest.mark.parametrize("profile", ("production", "development", "test"))
def test_profile_accepts_known_values(profile: str) -> None:
    assert Settings(profile=profile).profile == profile


@pytest.mark.parametrize("secret_source", ("environment", "local_file"))
def test_secret_source_accepts_known_values(secret_source: str) -> None:
    assert Settings(secret_source=secret_source).secret_source == secret_source


@pytest.mark.parametrize(
    "field, invalid",
    (
        ("profile", "Production"),
        ("profile", "development "),
        ("profile", None),
        ("secret_source", "ENVIRONMENT"),
        ("secret_source", "local_file "),
        ("secret_source", None),
    ),
)
def test_enums_fail_closed_without_normalization(field: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=rf"^{field} must be one of:") as raised:
        Settings(**{field: invalid})
    assert str(invalid) not in str(raised.value)


def test_create_app_rejects_truthy_mapping_before_database_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_database(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("database construction must not run")

    monkeypatch.setattr("backend.app.main.Database", unexpected_database)
    with pytest.raises(ValueError, match=r"^enable_v1_runtime must be a bool$"):
        create_app(
            {
                "database_path": tmp_path / "db.sqlite",
                "sandbox_root": tmp_path / "sandbox",
                "enable_v1_runtime": "true",
            }
        )


def test_compose_boolean_literals_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECTTOWN_ENABLE_V1_RUNTIME", "true")
    assert _env_bool("PROJECTTOWN_ENABLE_V1_RUNTIME", False) is True
    assert Settings(
        enable_local_settings_control=True,
        allow_container_local_settings=True,
        local_settings_trusted_peer="172.30.250.1",
        profile="development",
        secret_source="local_file",
    )


def test_workspace_authoring_create_is_default_off_and_requires_safe_roots(
    tmp_path: Path,
) -> None:
    assert Settings().enable_local_workspace_task_create is False
    work, material = tmp_path / "work", tmp_path / "material"
    work.mkdir()
    material.mkdir()
    with pytest.raises(ValueError, match="requires enable_local_workspace_task"):
        Settings(
            enable_local_workspace_task_create=True,
            local_workspace_task_root=work,
            local_workspace_task_material_root=material,
        )
