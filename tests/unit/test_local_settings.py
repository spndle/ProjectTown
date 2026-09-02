from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from backend.app import local_settings
from backend.app.local_settings import (
    LocalSettingsError,
    LocalSettingsService,
    _set_restricted_permissions,
)


def _service(tmp_path: Path) -> LocalSettingsService:
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    service.start()
    return service


@pytest.mark.parametrize(
    ("directory", "inheritance"),
    [
        (False, "[System.Security.AccessControl.InheritanceFlags]::None"),
        (
            True,
            (
                "[System.Security.AccessControl.InheritanceFlags]::ObjectInherit -bor "
                "[System.Security.AccessControl.InheritanceFlags]::ContainerInherit"
            ),
        ),
    ],
)
def test_windows_acl_script_uses_current_sid_and_expected_inheritance(
    directory: bool, inheritance: str
) -> None:
    script = local_settings._windows_acl_restrict_script(directory)
    assert "WindowsIdentity]::GetCurrent().User" in script
    assert "USERNAME" not in script and "USERDOMAIN" not in script
    assert "SetAccessRuleProtection($true,$false)" in script
    assert "RemoveAccessRuleSpecific" in script
    assert inheritance in script
    assert "$item.SetAccessControl($acl)" in script
    assert "$verifiedItem=[IO.FileInfo]::new($p)" in script or "$verifiedItem=[IO.DirectoryInfo]::new($p)" in script
    assert "$item=[IO.DirectoryInfo]::new($p)" in local_settings._windows_acl_restrict_script(True)
    assert "$item=[IO.FileInfo]::new($p)" in local_settings._windows_acl_restrict_script(False)
    assert "$groupBefore" in script and "$groupAfter" in script
    assert "$allowedOwners -notcontains $ownerBefore" in script
    assert "$allowedOwners -notcontains $ownerAfter" in script
    assert "$groupAfter -ne $groupBefore" in script
    assert "$allowedOwners -notcontains $groupBefore" not in script
    assert "$allowedOwners -notcontains $groupAfter" not in script
    assert "Set-Acl" not in script and "SetOwner" not in script and "SetAuditRule" not in script
    assert "SetGroup" not in script
    assert "owner denied" in script and "ACL verification failed" in script
    assert "$rule.IsInherited" in script and "AccessControlType]::Deny" in script
    assert "$currentAllowCount -ne 1" in script


def test_windows_acl_scripts_use_sid_native_dotnet_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    restrict = local_settings._windows_acl_restrict_script(True)
    captured: list[str] = []
    monkeypatch.setattr(
        local_settings,
        "_run_windows_acl",
        lambda _executable, script, _path: captured.append(script),
    )
    assert local_settings._windows_acl_is_restricted(tmp_path, directory=True)
    verify = captured[0]
    for script in (restrict, verify):
        for forbidden in ("Get-Item", "Write-Output", "New-Object", "Get-Acl", "Translate("):
            assert forbidden not in script
        assert "$existing.Access" not in script
        assert "$verified.Access" not in script
        assert "$acl.Access" not in script
        assert "AccessControlSections]::Access" in script
        assert "AccessControlSections]::Owner" in script
        assert "AccessControlSections]::Group" in script
        assert "GetAccessControl($sections)" in script
        assert "GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])" in script
        assert "Audit" not in script and "SACL" not in script
        assert "PSModulePath" not in script
        assert "LOCALAPPDATA" not in script and "USERPROFILE" not in script
        assert "[IO.DirectoryInfo]::new($p)" in script
        assert "$item.Refresh()" in script and "$item.Exists" in script
    assert "[Security.AccessControl.FileSystemAccessRule]::new(" in restrict
    assert "$group=$acl.GetGroup([Security.Principal.SecurityIdentifier]).Value" in verify


def test_windows_acl_restrict_uses_fixed_powershell_and_rejects_identity_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("safe", encoding="utf-8")
    calls: list[tuple[Path, str, Path]] = []
    monkeypatch.setattr(
        local_settings,
        "_run_windows_acl",
        lambda executable, script, target: calls.append((executable, script, target)),
    )
    monkeypatch.setattr(
        local_settings, "_windows_acl_is_restricted", lambda target, *, directory: True
    )
    local_settings._icacls_restrict(path, directory=False)
    assert calls[0][0] == Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    assert calls[0][2] == path

    identities = iter(((1, 1), (1, 2)))
    monkeypatch.setattr(
        local_settings, "_acl_target_identity", lambda *args, **kwargs: next(identities)
    )
    with pytest.raises(OSError, match="local settings ACL operation failed"):
        local_settings._icacls_restrict(path, directory=False)


def test_windows_acl_restrict_converts_runner_failure_to_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("safe", encoding="utf-8")
    monkeypatch.setattr(
        local_settings,
        "_run_windows_acl",
        lambda *args: (_ for _ in ()).throw(OSError("CANARY_ACL_FAILURE")),
    )
    with pytest.raises(OSError, match="local settings ACL operation failed"):
        local_settings._icacls_restrict(path, directory=False)


def test_windows_acl_runner_uses_minimal_environment_and_literal_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "space ' & [\u4e2d\u6587].toml"
    calls: list[tuple[list[str], dict[str, str], dict[str, object]]] = []

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"], kwargs))
        return Completed()

    monkeypatch.setattr(local_settings.subprocess, "run", fake_run)
    local_settings._run_windows_acl(
        Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        "Get-Acl -LiteralPath $env:PROJECTTOWN_LOCAL_SETTINGS_PATH",
        path,
    )
    command, environment, kwargs = calls[0]
    assert command[:3] == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-NonInteractive",
    ]
    assert set(environment) == {"SystemRoot", "WINDIR", "PROJECTTOWN_LOCAL_SETTINGS_PATH"}
    assert environment["PROJECTTOWN_LOCAL_SETTINGS_PATH"] == str(path)
    assert str(path) not in command[-1]
    assert kwargs["timeout"] == local_settings.WINDOWS_ACL_TIMEOUT_SECONDS


def test_windows_acl_timeout_is_fixed_and_bounded() -> None:
    assert local_settings.WINDOWS_ACL_TIMEOUT_SECONDS == 3


@pytest.mark.parametrize(
    "failure",
    [
        type("Completed", (), {"returncode": 1})(),
        local_settings.subprocess.TimeoutExpired(
            ["powershell"], local_settings.WINDOWS_ACL_TIMEOUT_SECONDS
        ),
        OSError("runner unavailable"),
    ],
)
def test_windows_acl_runner_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: object
) -> None:
    path = tmp_path / "settings.toml"
    if isinstance(failure, Exception):
        monkeypatch.setattr(
            local_settings.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(failure),
        )
    else:
        monkeypatch.setattr(local_settings.subprocess, "run", lambda *args, **kwargs: failure)
    with pytest.raises(OSError, match="local settings ACL operation failed"):
        local_settings._run_windows_acl(Path("powershell.exe"), "exit 0", path)


def test_get_is_redacted_and_put_uses_cas_and_atomic_file(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        first = service.get()
        assert first["api_key_configured"] is False and first["base_url"].endswith(
            "/v1"
        )
        saved = service.put(
            {
                "base_url": first["base_url"],
                "model": first["model"],
                "api_key_action": "replace",
                "api_key": "CANARY_LOCAL_SETTINGS",
                "expected_revision": first["revision"],
            }
        )
        assert (
            saved["api_key_configured"] is True
            and saved["revision"] != first["revision"]
        )
        assert "CANARY_LOCAL_SETTINGS" not in str(saved)
        with pytest.raises(LocalSettingsError) as raised:
            service.put(
                {
                    "base_url": first["base_url"],
                    "model": first["model"],
                    "api_key_action": "clear",
                    "api_key": None,
                    "expected_revision": first["revision"],
                }
            )
        assert raised.value.code == "LOCAL_SETTINGS_REVISION_CONFLICT"
        cleared = service.put(
            {
                "base_url": saved["base_url"],
                "model": saved["model"],
                "api_key_action": "clear",
                "api_key": None,
                "expected_revision": saved["revision"],
            }
        )
        assert cleared["api_key_configured"] is False
        assert "CANARY_LOCAL_SETTINGS" not in (
            tmp_path / ".secrets" / "model-providers.local.toml"
        ).read_text(encoding="utf-8")
    finally:
        service.close()


def test_relaxed_editor_rejects_empty_url_or_model_and_external_change_rotates_revision(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        current = service.get()
        for field in ("base_url", "model"):
            payload = {
                "base_url": current["base_url"],
                "model": current["model"],
                "api_key_action": "clear",
                "api_key": None,
                "expected_revision": current["revision"],
            }
            payload[field] = ""
            with pytest.raises(LocalSettingsError):
                service.put(payload)
        path = tmp_path / ".secrets" / "model-providers.local.toml"
        path.write_text(
            'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = ""\nmodel = "gpt-5-mini-2025-08-07"\n\n[providers.qwen]\nbase_url = ""\napi_key = ""\nmodel = ""\n',
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
        _set_restricted_permissions(path)
        assert service.get()["revision"] != current["revision"]
    finally:
        service.close()


def test_token_cleanup_refuses_replaced_content(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token_path = tmp_path / ".secrets" / "projecttown-settings-session.token"
    token_path.write_text("replacement", encoding="ascii")
    service.close()
    assert token_path.exists()


def test_container_mode_rotates_a_valid_stale_token_and_releases_lock(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("container lock behavior is verified in a Linux Docker runtime")
    first = LocalSettingsService(
        root=tmp_path,
        allow_test_client=True,
        container_mode=True,
        trusted_peer="172.30.250.1",
    )
    first.start()
    stale_token = first.token
    token_path = tmp_path / ".secrets" / "projecttown-settings-session.token"
    first._token = None  # Simulate a terminated process which left its token behind.
    first._token_file_content = None
    first._release_container_lock()

    second = LocalSettingsService(
        root=tmp_path,
        allow_test_client=True,
        container_mode=True,
        trusted_peer="172.30.250.1",
    )
    try:
        second.start()
        assert second.token != stale_token
        assert token_path.read_text(encoding="ascii") == second.token
    finally:
        second.close()
    assert not token_path.exists()


def test_container_mode_allows_only_one_live_instance(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("container lock behavior is verified in a Linux Docker runtime")
    first = LocalSettingsService(
        root=tmp_path,
        allow_test_client=True,
        container_mode=True,
        trusted_peer="172.30.250.1",
    )
    second = LocalSettingsService(
        root=tmp_path,
        allow_test_client=True,
        container_mode=True,
        trusted_peer="172.30.250.1",
    )
    first.start()
    try:
        with pytest.raises(LocalSettingsError) as raised:
            second.start()
        assert raised.value.code == "LOCAL_SETTINGS_INSTANCE_LOCKED"
    finally:
        first.close()


@pytest.mark.parametrize("stale", [b"", b"not-a-generated-token", b"a" * 42])
def test_container_mode_rejects_invalid_stale_token(
    tmp_path: Path, stale: bytes
) -> None:
    if os.name == "nt":
        pytest.skip("container lock behavior is verified in a Linux Docker runtime")
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir(mode=0o700)
    token_path = secrets_dir / "projecttown-settings-session.token"
    token_path.write_bytes(stale)
    os.chmod(token_path, 0o600)
    service = LocalSettingsService(
        root=tmp_path,
        allow_test_client=True,
        container_mode=True,
        trusted_peer="172.30.250.1",
    )
    with pytest.raises(LocalSettingsError) as raised:
        service.start()
    assert raised.value.code == "LOCAL_SETTINGS_TOKEN_INVALID"
    assert token_path.read_bytes() == stale


@pytest.mark.parametrize("key", ["", "   ", " key", "key "])
def test_replace_cannot_clear_or_normalize_key(tmp_path: Path, key: str) -> None:
    service = _service(tmp_path)
    try:
        current = service.get()
        with pytest.raises(LocalSettingsError) as raised:
            service.put(
                {
                    "base_url": current["base_url"],
                    "model": current["model"],
                    "api_key_action": "replace",
                    "api_key": key,
                    "expected_revision": current["revision"],
                }
            )
        assert raised.value.code == "LOCAL_SETTINGS_BODY_INVALID"
    finally:
        service.close()


def test_put_serializes_same_revision_and_detects_external_change_before_replace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        current = service.get()
        payload = {
            "base_url": current["base_url"],
            "model": current["model"],
            "api_key_action": "replace",
            "api_key": "CANARY_THREAD",
            "expected_revision": current["revision"],
        }
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def writer() -> None:
            barrier.wait()
            try:
                service.put(payload)
                outcomes.append("success")
            except LocalSettingsError as error:
                outcomes.append(error.code)

        workers = [threading.Thread(target=writer) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert sorted(outcomes) == ["LOCAL_SETTINGS_REVISION_CONFLICT", "success"]

        latest = service.get()
        path = tmp_path / ".secrets" / "model-providers.local.toml"

        def inject_external_change() -> None:
            path.write_text(
                'version = 3\n[providers.openai]\nbase_url = "https://api.openai.com/v1"\napi_key = "EXTERNAL_MARKER"\nmodel = "gpt-5-mini-2025-08-07"\n\n[providers.qwen]\nbase_url = ""\napi_key = ""\nmodel = ""\n',
                encoding="utf-8",
            )
            _set_restricted_permissions(path)

        service._before_replace = inject_external_change  # type: ignore[method-assign]
        with pytest.raises(LocalSettingsError) as raised:
            service.put(
                {
                    "base_url": latest["base_url"],
                    "model": latest["model"],
                    "api_key_action": "clear",
                    "api_key": None,
                    "expected_revision": latest["revision"],
                }
            )
        assert raised.value.code == "LOCAL_SETTINGS_REVISION_CONFLICT"
        assert "EXTERNAL_MARKER" in path.read_text(encoding="utf-8")
    finally:
        service.close()


def test_secrets_directory_symlink_is_denied(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    secrets_dir = tmp_path / ".secrets"
    try:
        os.symlink(target, secrets_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    with pytest.raises(LocalSettingsError) as raised:
        service.start()
    assert raised.value.code == "LOCAL_SETTINGS_PATH_DENIED"


def test_directory_hardening_failure_happens_before_token_or_settings_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    monkeypatch.setattr(
        local_settings,
        "_set_restricted_directory_permissions",
        lambda path: (_ for _ in ()).throw(OSError("CANARY_DIRECTORY_ACL")),
    )
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    with pytest.raises(LocalSettingsError) as raised:
        service.start()
    assert raised.value.code == "LOCAL_SETTINGS_PATH_DENIED"
    assert not (secrets_dir / "projecttown-settings-session.token").exists()
    assert not (secrets_dir / "model-providers.local.toml").exists()


def test_qwen_write_preserves_openai_and_redacts_both_provider_canaries(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        openai = service.get()
        openai_saved = service.put(
            {
                "base_url": openai["base_url"],
                "model": openai["model"],
                "api_key_action": "replace",
                "api_key": "OPENAI_CANARY",
                "expected_revision": openai["revision"],
            }
        )
        qwen = service.get("qwen")
        assert qwen["base_url"] == "" and qwen["model_options"] == ["qwen-plus"]
        qwen_saved = service.put(
            {
                "base_url": "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1",
                "model": "qwen-plus",
                "api_key_action": "replace",
                "api_key": "QWEN_CANARY",
                "expected_revision": qwen["revision"],
            },
            "qwen",
        )
        assert qwen_saved["provider"] == "qwen"
        assert qwen_saved["api_key_configured"] is True
        assert qwen_saved["base_url_options"] == [qwen_saved["base_url"]]
        assert qwen_saved["live_authorized"] is False
        assert "QWEN_CANARY" not in repr(qwen_saved)
        preserved = service.get("openai")
        assert preserved["base_url"] == openai_saved["base_url"]
        assert preserved["model"] == openai_saved["model"]
        assert preserved["api_key_configured"] is True
        assert "OPENAI_CANARY" not in repr(preserved)
        serialized = (tmp_path / ".secrets" / "model-providers.local.toml").read_text(
            encoding="utf-8"
        )
        assert "OPENAI_CANARY" in serialized and "QWEN_CANARY" in serialized
    finally:
        service.close()


@pytest.mark.parametrize(
    "base_url,model",
    [
        ("https://example.invalid/api/v1", "qwen-plus"),
        ("https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1", "qwen3-8b"),
    ],
)
def test_qwen_put_requires_strict_workspace_url_and_model(
    tmp_path: Path, base_url: str, model: str
) -> None:
    service = _service(tmp_path)
    try:
        current = service.get("qwen")
        with pytest.raises(LocalSettingsError) as raised:
            service.put(
                {
                    "base_url": base_url,
                    "model": model,
                    "api_key_action": "replace",
                    "api_key": "QWEN_CANARY",
                    "expected_revision": current["revision"],
                },
                "qwen",
            )
        assert raised.value.code in {
            "SECRET_BASE_URL_DENIED",
            "SECRET_MODEL_UNSUPPORTED",
        }
        assert "QWEN_CANARY" not in str(raised.value)
    finally:
        service.close()


def test_cross_provider_same_revision_has_one_winner(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        revision = service.get()["revision"]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        payloads = [
            (
                "openai",
                {
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-5-mini-2025-08-07",
                    "api_key_action": "replace",
                    "api_key": "OPENAI_THREAD",
                    "expected_revision": revision,
                },
            ),
            (
                "qwen",
                {
                    "base_url": "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1",
                    "model": "qwen-plus",
                    "api_key_action": "replace",
                    "api_key": "QWEN_THREAD",
                    "expected_revision": revision,
                },
            ),
        ]

        def writer(provider: str, payload: dict[str, object]) -> None:
            barrier.wait()
            try:
                service.put(payload, provider)
                outcomes.append("success")
            except LocalSettingsError as error:
                outcomes.append(error.code)

        workers = [threading.Thread(target=writer, args=item) for item in payloads]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert sorted(outcomes) == ["LOCAL_SETTINGS_REVISION_CONFLICT", "success"]
    finally:
        service.close()
