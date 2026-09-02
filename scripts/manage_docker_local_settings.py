"""Manage the opt-in Docker local Settings mode without exporting provider secrets.

Only the session token is mirrored to the host `.secrets` directory. Provider
configuration remains in the Docker named volume and is never read by this tool.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "docker-compose.yml"
OVERRIDE_COMPOSE = ROOT / "docker-compose.local-settings.yml"
PROJECT = "projecttown"
SERVICE = "projecttown"
NETWORK = "projecttown_local_settings"
SUBNET = ipaddress.ip_network("172.30.250.0/29")
GATEWAY = "172.30.250.1"
TOKEN_NAME = "projecttown-settings-session.token"
TOKEN_LENGTH = 43
BASE_NETWORK = "projecttown_default"
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ManagementError(RuntimeError):
    pass


def _run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=30,
        check=False,
    )


def _json(args: list[str]) -> Any:
    completed = _run(args)
    if completed.returncode != 0:
        raise ManagementError("LOCAL_SETTINGS_DOCKER_INSPECT_FAILED")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ManagementError("LOCAL_SETTINGS_DOCKER_INSPECT_FAILED") from error


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "docker",
            "compose",
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(OVERRIDE_COMPOSE),
            *args,
        ]
    )


def preflight_network() -> None:
    identifiers = _run(["docker", "network", "ls", "-q"])
    if identifiers.returncode != 0:
        raise ManagementError("LOCAL_SETTINGS_DOCKER_NETWORK_CHECK_FAILED")
    ids = [line for line in identifiers.stdout.splitlines() if line]
    networks: list[dict[str, Any]] = (
        [] if not ids else _json(["docker", "network", "inspect", *ids])
    )
    for network in networks:
        name = network.get("Name")
        configs = network.get("IPAM", {}).get("Config") or []
        subnets = [
            item.get("Subnet")
            for item in configs
            if isinstance(item, dict) and item.get("Subnet")
        ]
        if name == NETWORK:
            if subnets != [str(SUBNET)] or configs[0].get("Gateway") != GATEWAY:
                raise ManagementError("LOCAL_SETTINGS_DOCKER_EXPECTED_NETWORK_INVALID")
            for identifier, endpoint in (network.get("Containers") or {}).items():
                if not isinstance(identifier, str) or not isinstance(endpoint, dict):
                    raise ManagementError("LOCAL_SETTINGS_DOCKER_EXPECTED_NETWORK_BUSY")
                endpoint_container = _inspect_container(identifier)
                endpoint_labels = endpoint_container.get("Config", {}).get("Labels", {})
                if (
                    endpoint_labels.get("com.docker.compose.project") != PROJECT
                    or endpoint_labels.get("com.docker.compose.service") != SERVICE
                    or not endpoint_container.get("State", {}).get("Running")
                ):
                    raise ManagementError("LOCAL_SETTINGS_DOCKER_EXPECTED_NETWORK_BUSY")
            continue
        for value in subnets:
            try:
                if SUBNET.overlaps(ipaddress.ip_network(value, strict=False)):
                    raise ManagementError("LOCAL_SETTINGS_DOCKER_SUBNET_CONFLICT")
            except ValueError as error:
                raise ManagementError(
                    "LOCAL_SETTINGS_DOCKER_NETWORK_CHECK_FAILED"
                ) from error


def _container_id() -> str:
    completed = _compose("ps", "-q", SERVICE)
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(values) != 1:
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_ID_INVALID")
    return values[0]


def _inspect_container(identifier: str) -> dict[str, Any]:
    inspected = _json(["docker", "inspect", identifier])
    if (
        not isinstance(inspected, list)
        or len(inspected) != 1
        or not isinstance(inspected[0], dict)
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_ID_INVALID")
    return inspected[0]


def _assert_container(container: dict[str, Any], expected_image: str) -> None:
    state = container.get("State", {})
    labels = container.get("Config", {}).get("Labels", {})
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    mounts = container.get("Mounts", [])
    networks = container.get("NetworkSettings", {}).get("Networks", {})
    if not state.get("Running") or state.get("Health", {}).get("Status") != "healthy":
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_NOT_HEALTHY")
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != SERVICE
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_IDENTITY_DENIED")
    if (
        not _canonical_image_id(container.get("Image"))
        or container.get("Image") != expected_image
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_IMAGE_DENIED")
    if config.get("User") != "10001:10001" or not host.get("ReadonlyRootfs"):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_HARDENING_DENIED")
    bindings = host.get("PortBindings", {})
    ports = bindings.get("8000/tcp")
    if not isinstance(ports, list) or any(
        item.get("HostIp") != "127.0.0.1" for item in ports
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_PORT_DENIED")
    secret_mounts = [
        item for item in mounts if item.get("Destination") == "/app/.secrets"
    ]
    if (
        len(secret_mounts) != 1
        or secret_mounts[0].get("Type") != "volume"
        or not secret_mounts[0].get("RW")
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_MOUNT_DENIED")
    if secret_mounts[0].get("Name") != "projecttown_local_settings":
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_MOUNT_DENIED")
    if any(
        item.get("Type") == "bind" and ".secrets" in str(item.get("Source", ""))
        for item in mounts
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_MOUNT_DENIED")
    if set(networks) != {NETWORK}:
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_NETWORK_DENIED")
    blocked = ("OPENAI", "QWEN", "DEEPSEEK", "BASE_URL", "API_KEY", "MODEL")
    if any(
        any(fragment in item.partition("=")[0] for fragment in blocked)
        for item in config.get("Env", [])
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_ENV_DENIED")


def _target_image_id() -> str:
    completed = _compose("images", "-q", SERVICE)
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(values) != 1:
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_IMAGE_DENIED")
    inspected = _json(["docker", "image", "inspect", values[0]])
    if (
        not isinstance(inspected, list)
        or len(inspected) != 1
        or not isinstance(inspected[0], dict)
    ):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_IMAGE_DENIED")
    image_id = inspected[0].get("Id")
    if not _canonical_image_id(image_id):
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_IMAGE_DENIED")
    return image_id


def _canonical_image_id(value: object) -> bool:
    return isinstance(value, str) and _IMAGE_ID.fullmatch(value) is not None


def _mirror_path() -> Path:
    directory = ROOT / ".secrets"
    try:
        root_info = os.lstat(ROOT)
        info = os.lstat(directory)
    except OSError as error:
        raise ManagementError("LOCAL_SETTINGS_MIRROR_DIRECTORY_DENIED") from error
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or directory.is_symlink()
    ):
        raise ManagementError("LOCAL_SETTINGS_MIRROR_DIRECTORY_DENIED")
    if directory.resolve().parent != ROOT.resolve() or not _path_is_restricted(
        directory, info, directory=True
    ):
        raise ManagementError("LOCAL_SETTINGS_MIRROR_DIRECTORY_DENIED")
    target = directory / TOKEN_NAME
    if target.exists():
        _validate_mirror_file(target)
    return target


def _path_is_restricted(path: Path, info: os.stat_result, *, directory: bool) -> bool:
    if os.name != "nt":
        return stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
    return _windows_acl_is_restricted(path)


def _windows_acl_is_restricted(path: Path) -> bool:
    executable = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    script = (
        "$ErrorActionPreference='Stop';$p=$env:PROJECTTOWN_LOCAL_SETTINGS_PATH;"
        "if([string]::IsNullOrWhiteSpace($p)){exit 1};$acl=Get-Acl -LiteralPath $p;"
        "if(-not $acl.AreAccessRulesProtected){exit 1};"
        "$current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "$allowed=@($current,'S-1-5-18','S-1-5-32-544');$currentFull=$false;"
        "foreach($r in $acl.Access){if($r.AccessControlType -eq 'Allow'){"
        "$sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value;"
        "if($allowed -notcontains $sid){exit 1};if($sid -eq $current -and "
        "(($r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq "
        "[Security.AccessControl.FileSystemRights]::FullControl)){$currentFull=$true}}};"
        "if(-not $currentFull){exit 1};exit 0"
    )
    environment = {
        "SystemRoot": r"C:\Windows",
        "WINDIR": r"C:\Windows",
        "PROJECTTOWN_LOCAL_SETTINGS_PATH": str(path),
    }
    try:
        completed = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _restrict(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    principal = os.getenv("USERNAME")
    domain = os.getenv("USERDOMAIN")
    if not principal or not domain:
        raise ManagementError("LOCAL_SETTINGS_MIRROR_ACL_DENIED")
    executable = Path(r"C:\Windows\System32\icacls.exe")
    grant = f"{domain}\\{principal}:F"
    completed = subprocess.run(
        [
            str(executable),
            str(path),
            "/inheritance:r",
            "/grant:r",
            grant,
            "/remove:g",
            "OWNER RIGHTS",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"SystemRoot": r"C:\Windows", "WINDIR": r"C:\Windows"},
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise ManagementError("LOCAL_SETTINGS_MIRROR_ACL_DENIED")
    info = os.lstat(path)
    if not _path_is_restricted(path, info, directory=False):
        raise ManagementError("LOCAL_SETTINGS_MIRROR_ACL_DENIED")


def _validate_mirror_file(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as error:
        raise ManagementError("LOCAL_SETTINGS_MIRROR_FILE_DENIED") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or not _path_is_restricted(path, info, directory=False)
    ):
        raise ManagementError("LOCAL_SETTINGS_MIRROR_FILE_DENIED")


def _valid_token(path: Path) -> bytes:
    _validate_mirror_file(path)
    info = os.lstat(path)
    if info.st_size != TOKEN_LENGTH:
        raise ManagementError("LOCAL_SETTINGS_TOKEN_INVALID")
    raw = path.read_bytes()
    alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if len(raw) != TOKEN_LENGTH or any(value not in alphabet for value in raw):
        raise ManagementError("LOCAL_SETTINGS_TOKEN_INVALID")
    return raw


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _local_get(expected_status: int, token: bytes | None = None) -> None:
    headers = (
        {} if token is None else {"X-ProjectTown-Settings-Token": token.decode("ascii")}
    )
    request = urllib.request.Request(
        "http://127.0.0.1:8000/local/settings/v1/providers/openai", headers=headers
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _RejectRedirect()
    )
    try:
        with opener.open(request, timeout=3) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, TimeoutError) as error:
        raise ManagementError("LOCAL_SETTINGS_TOKEN_HANDSHAKE_FAILED") from error
    if status != expected_status:
        raise ManagementError("LOCAL_SETTINGS_TOKEN_HANDSHAKE_FAILED")


def sync_token() -> None:
    target = _mirror_path()
    identifier = _container_id()
    container = _inspect_container(identifier)
    expected_image = _target_image_id()
    _assert_container(container, expected_image)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(16)}.tmp")
    try:
        copied = _run(
            ["docker", "cp", f"{identifier}:/app/.secrets/{TOKEN_NAME}", str(temporary)]
        )
        if copied.returncode != 0:
            raise ManagementError("LOCAL_SETTINGS_TOKEN_COPY_FAILED")
        _restrict(temporary)
        token = _valid_token(temporary)
        _local_get(200, token)
        if _container_id() != identifier:
            raise ManagementError("LOCAL_SETTINGS_CONTAINER_CHANGED")
        _assert_container(_inspect_container(identifier), expected_image)
        os.replace(temporary, target)
        _restrict(target)
    except Exception:
        try:
            if (
                temporary.exists()
                and temporary.is_file()
                and not temporary.is_symlink()
            ):
                temporary.unlink()
        except OSError:
            pass
        raise


def start() -> None:
    preflight_network()
    completed = _compose(
        "up", "--build", "--detach", "--force-recreate", "--no-deps", SERVICE
    )
    if completed.returncode != 0:
        raise ManagementError("LOCAL_SETTINGS_START_FAILED")
    deadline = time.monotonic() + 45
    while True:
        try:
            container = _inspect_container(_container_id())
            if (
                container.get("State", {}).get("Running")
                and container.get("State", {}).get("Health", {}).get("Status")
                == "healthy"
            ):
                break
        except ManagementError:
            pass
        if time.monotonic() >= deadline:
            raise ManagementError("LOCAL_SETTINGS_START_HEALTH_TIMEOUT")
        time.sleep(1)
    sync_token()


def rollback() -> None:
    completed = _run(
        [
            "docker",
            "compose",
            "-f",
            str(BASE_COMPOSE),
            "up",
            "--detach",
            "--force-recreate",
            "--no-deps",
            SERVICE,
        ]
    )
    if completed.returncode != 0:
        raise ManagementError("LOCAL_SETTINGS_ROLLBACK_FAILED")
    target = _mirror_path()
    identifier = _base_container_id()
    expected_image = _base_target_image_id()
    deadline = time.monotonic() + 45
    while True:
        container = _inspect_container(identifier)
        if (
            container.get("State", {}).get("Running")
            and container.get("State", {}).get("Health", {}).get("Status") == "healthy"
        ):
            break
        if time.monotonic() >= deadline:
            raise ManagementError("LOCAL_SETTINGS_ROLLBACK_HEALTH_FAILED")
        time.sleep(1)
    _assert_base_container(container, expected_image)
    _local_get(404)
    if target.exists():
        _validate_mirror_file(target)
        target.unlink()


def _base_container_id() -> str:
    completed = _run(
        ["docker", "compose", "-f", str(BASE_COMPOSE), "ps", "-q", SERVICE]
    )
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(values) != 1:
        raise ManagementError("LOCAL_SETTINGS_CONTAINER_ID_INVALID")
    return values[0]


def _base_target_image_id() -> str:
    completed = _run(
        ["docker", "compose", "-f", str(BASE_COMPOSE), "images", "-q", SERVICE]
    )
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(values) != 1:
        raise ManagementError("LOCAL_SETTINGS_ROLLBACK_CONTAINER_DENIED")
    inspected = _json(["docker", "image", "inspect", values[0]])
    if (
        not isinstance(inspected, list)
        or len(inspected) != 1
        or not isinstance(inspected[0], dict)
    ):
        raise ManagementError("LOCAL_SETTINGS_ROLLBACK_CONTAINER_DENIED")
    image_id = inspected[0].get("Id")
    if not _canonical_image_id(image_id):
        raise ManagementError("LOCAL_SETTINGS_ROLLBACK_CONTAINER_DENIED")
    return image_id


def _assert_base_container(container: dict[str, Any], expected_image: str) -> None:
    labels = container.get("Config", {}).get("Labels", {})
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    ports = host.get("PortBindings", {}).get("8000/tcp")
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != SERVICE
        or not _canonical_image_id(container.get("Image"))
        or container.get("Image") != expected_image
        or config.get("User") != "10001:10001"
        or not host.get("ReadonlyRootfs")
        or not isinstance(ports, list)
        or any(item.get("HostIp") != "127.0.0.1" for item in ports)
        or set(container.get("NetworkSettings", {}).get("Networks", {}))
        != {BASE_NETWORK}
        or any(
            item.get("Destination") == "/app/.secrets"
            for item in container.get("Mounts", [])
        )
    ):
        raise ManagementError("LOCAL_SETTINGS_ROLLBACK_CONTAINER_DENIED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "sync-token", "rollback"))
    command = parser.parse_args(argv).command
    try:
        {"start": start, "sync-token": sync_token, "rollback": rollback}[command]()
    except (ManagementError, OSError, subprocess.SubprocessError):
        print("LOCAL_SETTINGS_MANAGEMENT_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
