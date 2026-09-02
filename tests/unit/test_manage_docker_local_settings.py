from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import manage_docker_local_settings as manager

IMAGE_ID = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def restricted_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        manager, "_path_is_restricted", lambda path, info, directory: True
    )


def _container() -> dict[str, object]:
    return {
        "Image": IMAGE_ID,
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Config": {
            "User": "10001:10001",
            "Labels": {
                "com.docker.compose.project": "projecttown",
                "com.docker.compose.service": "projecttown",
            },
            "Env": [],
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1"}]},
        },
        "Mounts": [
            {
                "Destination": "/app/.secrets",
                "Type": "volume",
                "RW": True,
                "Name": "projecttown_local_settings",
            }
        ],
        "NetworkSettings": {"Networks": {"projecttown_local_settings": {}}},
    }


def _base_container(*, healthy: bool = True) -> dict[str, object]:
    return {
        **_container(),
        "State": {
            "Running": healthy,
            "Health": {"Status": "healthy" if healthy else "starting"},
        },
        "NetworkSettings": {"Networks": {manager.BASE_NETWORK: {}}},
        "Mounts": [],
    }


@pytest.mark.parametrize(
    "section, value",
    [
        ("identity", "other"),
        ("HostConfig.PortBindings.8000/tcp", [{"HostIp": "0.0.0.0"}]),
        ("Mounts", []),
        ("NetworkSettings.Networks", {"bridge": {}}),
        ("Config.Env", ["PROJECTTOWN_QWEN_API_KEY=x"]),
    ],
)
def test_container_attestation_rejects_identity_mount_network_port_and_env(
    section: str,
    value: object,
) -> None:
    candidate = _container()
    if section == "identity":
        candidate["Config"]["Labels"]["com.docker.compose.service"] = value  # type: ignore[index]
    else:
        current: object = candidate
        keys = section.split(".")
        for key in keys[:-1]:
            current = current[key]  # type: ignore[index]
        current[keys[-1]] = value  # type: ignore[index]
    with pytest.raises(manager.ManagementError):
        manager._assert_container(candidate, IMAGE_ID)


@pytest.mark.parametrize("image_id", ["a" * 64, "sha256:abc", "sha256:" + "G" * 64])
def test_target_image_rejects_noncanonical_inspected_id(
    monkeypatch: pytest.MonkeyPatch,
    image_id: str,
) -> None:
    monkeypatch.setattr(
        manager,
        "_compose",
        lambda *args: subprocess.CompletedProcess(args, 0, "short-id\n", ""),
    )
    monkeypatch.setattr(manager, "_json", lambda args: [{"Id": image_id}])
    with pytest.raises(manager.ManagementError, match="IMAGE_DENIED"):
        manager._target_image_id()


@pytest.mark.parametrize("inspected", [[], [{"Id": IMAGE_ID}, {"Id": IMAGE_ID}]])
def test_target_image_rejects_zero_or_multiple_inspect_objects(
    monkeypatch: pytest.MonkeyPatch,
    inspected: object,
) -> None:
    monkeypatch.setattr(
        manager,
        "_compose",
        lambda *args: subprocess.CompletedProcess(args, 0, "short-id\n", ""),
    )
    monkeypatch.setattr(manager, "_json", lambda args: inspected)
    with pytest.raises(manager.ManagementError, match="IMAGE_DENIED"):
        manager._target_image_id()


def test_target_image_rejects_inspect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        manager,
        "_compose",
        lambda *args: subprocess.CompletedProcess(args, 0, "short-id\n", ""),
    )
    monkeypatch.setattr(
        manager,
        "_json",
        lambda args: (_ for _ in ()).throw(
            manager.ManagementError("LOCAL_SETTINGS_DOCKER_INSPECT_FAILED")
        ),
    )
    with pytest.raises(manager.ManagementError, match="INSPECT_FAILED"):
        manager._target_image_id()


def test_network_preflight_accepts_null_ipam_and_expected_empty_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_json(args: list[str]) -> object:
        return [
            {
                "Name": manager.NETWORK,
                "IPAM": {
                    "Config": [
                        {"Subnet": str(manager.SUBNET), "Gateway": manager.GATEWAY}
                    ]
                },
                "Containers": {},
            }
        ]

    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 0, "id\n", ""),
    )
    monkeypatch.setattr(manager, "_json", fake_json)
    manager.preflight_network()


def test_network_preflight_rejects_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 0, "id\n", ""),
    )
    monkeypatch.setattr(
        manager,
        "_json",
        lambda args: [
            {"Name": "other", "IPAM": {"Config": [None, {"Subnet": "172.30.250.0/30"}]}}
        ],
    )
    with pytest.raises(manager.ManagementError, match="SUBNET_CONFLICT"):
        manager.preflight_network()


def test_network_preflight_allows_only_controlled_existing_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 0, "id\n", ""),
    )
    monkeypatch.setattr(
        manager,
        "_json",
        lambda args: [
            {
                "Name": manager.NETWORK,
                "IPAM": {
                    "Config": [
                        {"Subnet": str(manager.SUBNET), "Gateway": manager.GATEWAY}
                    ]
                },
                "Containers": {"id": {"Name": "container-a"}},
            }
        ],
    )
    monkeypatch.setattr(
        manager,
        "_inspect_container",
        lambda identifier: {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": manager.PROJECT,
                    "com.docker.compose.service": manager.SERVICE,
                }
            },
            "State": {"Running": True},
        },
    )
    manager.preflight_network()


def test_network_preflight_rejects_unknown_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 0, "id\n", ""),
    )
    monkeypatch.setattr(
        manager,
        "_json",
        lambda args: [
            {
                "Name": manager.NETWORK,
                "IPAM": {
                    "Config": [
                        {"Subnet": str(manager.SUBNET), "Gateway": manager.GATEWAY}
                    ]
                },
                "Containers": {"id": {"Name": "unknown"}},
            }
        ],
    )
    monkeypatch.setattr(
        manager,
        "_inspect_container",
        lambda identifier: {"Config": {"Labels": {}}, "State": {"Running": True}},
    )
    with pytest.raises(manager.ManagementError, match="EXPECTED_NETWORK_BUSY"):
        manager.preflight_network()


def test_mirror_rejects_symlink_and_wide_permission_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".secrets").mkdir()
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    target = tmp_path / ".secrets" / manager.TOKEN_NAME
    try:
        target.symlink_to(tmp_path / "other")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(manager.ManagementError):
        manager._mirror_path()
    target.unlink()
    target.write_bytes(b"A" * manager.TOKEN_LENGTH)
    monkeypatch.setattr(
        manager, "_path_is_restricted", lambda path, info, directory: False
    )
    with pytest.raises(manager.ManagementError):
        manager._mirror_path()


def test_local_handshake_disables_proxy_and_rejects_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: list[object] = []

    class Response:
        status = 302

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class Opener:
        def open(self, request: object, timeout: int) -> Response:
            return Response()

    monkeypatch.setattr(
        manager.urllib.request,
        "build_opener",
        lambda *items: handlers.extend(items) or Opener(),
    )
    with pytest.raises(manager.ManagementError, match="HANDSHAKE_FAILED"):
        manager._local_get(200, b"A" * manager.TOKEN_LENGTH)
    assert isinstance(handlers[0], manager.urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}


def test_sync_failure_does_not_publish_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    target = secrets_dir / manager.TOKEN_NAME
    target.write_text("old", encoding="ascii")
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    monkeypatch.setattr(manager, "_container_id", lambda: "container-a")
    monkeypatch.setattr(manager, "_target_image_id", lambda: IMAGE_ID)
    monkeypatch.setattr(manager, "_inspect_container", lambda identifier: _container())
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 1, "", ""),
    )
    with pytest.raises(manager.ManagementError, match="TOKEN_COPY_FAILED"):
        manager.sync_token()
    assert target.read_text(encoding="ascii") == "old"


def test_sync_handshake_or_container_change_does_not_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    target = secrets_dir / manager.TOKEN_NAME
    target.write_text("old", encoding="ascii")
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    calls = iter(["container-a", "container-b"])
    monkeypatch.setattr(manager, "_container_id", lambda: next(calls))
    monkeypatch.setattr(manager, "_target_image_id", lambda: IMAGE_ID)
    monkeypatch.setattr(manager, "_inspect_container", lambda identifier: _container())

    def fake_run(
        args: list[str], capture: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["docker", "cp"]:
            Path(args[-1]).write_bytes(b"A" * manager.TOKEN_LENGTH)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(manager, "_run", fake_run)
    monkeypatch.setattr(manager, "_local_get", lambda expected_status, token=None: None)
    with pytest.raises(manager.ManagementError, match="CONTAINER_CHANGED"):
        manager.sync_token()
    assert target.read_text(encoding="ascii") == "old"


def test_rollback_command_never_deletes_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    (tmp_path / ".secrets").mkdir()
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: (
            commands.append(args) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )
    monkeypatch.setattr(manager, "_base_container_id", lambda: "base")
    monkeypatch.setattr(manager, "_base_target_image_id", lambda: IMAGE_ID)
    monkeypatch.setattr(
        manager, "_inspect_container", lambda identifier: _base_container()
    )
    monkeypatch.setattr(manager, "_local_get", lambda expected_status, token=None: None)
    manager.rollback()
    assert all("-v" not in command and "volume" not in command for command in commands)
    assert any("--no-deps" in command for command in commands)


def test_rollback_keeps_mirror_when_health_or_route_check_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    target = secrets_dir / manager.TOKEN_NAME
    target.write_bytes(b"A" * manager.TOKEN_LENGTH)
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(manager, "_base_container_id", lambda: "base")
    monkeypatch.setattr(manager, "_base_target_image_id", lambda: IMAGE_ID)
    monkeypatch.setattr(
        manager, "_inspect_container", lambda identifier: _base_container(healthy=False)
    )
    ticks = iter([0.0, 46.0])
    monkeypatch.setattr(manager.time, "monotonic", lambda: next(ticks))
    with pytest.raises(manager.ManagementError):
        manager.rollback()
    assert target.exists()


def test_rollback_waits_for_healthy_before_deleting_mirror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    target = secrets_dir / manager.TOKEN_NAME
    target.write_bytes(b"A" * manager.TOKEN_LENGTH)
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, capture=True: subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(manager, "_base_container_id", lambda: "base")
    monkeypatch.setattr(manager, "_base_target_image_id", lambda: IMAGE_ID)
    states = iter([_base_container(healthy=False), _base_container()])
    monkeypatch.setattr(manager, "_inspect_container", lambda identifier: next(states))
    monkeypatch.setattr(manager, "_local_get", lambda expected_status, token=None: None)
    monkeypatch.setattr(manager.time, "sleep", lambda seconds: None)
    manager.rollback()
    assert not target.exists()


@pytest.mark.parametrize(
    "mutation", ["identity", "image", "user", "readonly", "port", "network", "mount"]
)
def test_base_container_attestation_rejects_identity_and_hardening(
    mutation: str,
) -> None:
    candidate = _base_container()
    if mutation == "identity":
        candidate["Config"]["Labels"]["com.docker.compose.service"] = "other"  # type: ignore[index]
    elif mutation == "image":
        candidate["Image"] = "other"
    elif mutation == "user":
        candidate["Config"]["User"] = "0"  # type: ignore[index]
    elif mutation == "readonly":
        candidate["HostConfig"]["ReadonlyRootfs"] = False  # type: ignore[index]
    elif mutation == "port":
        candidate["HostConfig"]["PortBindings"] = {"8000/tcp": [{"HostIp": "0.0.0.0"}]}  # type: ignore[index]
    elif mutation == "network":
        candidate["NetworkSettings"] = {"Networks": {"bridge": {}}}
    else:
        candidate["Mounts"] = [{"Destination": "/app/.secrets"}]
    with pytest.raises(manager.ManagementError, match="ROLLBACK_CONTAINER_DENIED"):
        manager._assert_base_container(candidate, IMAGE_ID)
