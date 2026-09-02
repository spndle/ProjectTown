import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_release_version_and_local_launcher_are_v1() -> None:
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"

    launcher = (ROOT / "scripts" / "run_v1.ps1").read_text(encoding="utf-8")
    backend_launcher = (ROOT / "scripts" / "run_backend.ps1").read_text(
        encoding="utf-8"
    )
    assert '"127.0.0.1"' in launcher
    assert "-NoReload" in launcher
    assert '"backend.main:app"' in backend_launcher

    godot_validator = (ROOT / "scripts" / "validate_godot_v1.ps1").read_text(
        encoding="utf-8"
    )
    assert '"--headless", "--editor"' in godot_validator
    assert "res://tests/api_smoke.gd" in godot_validator
    assert "LIVE_GODOT_BACKEND_OK" in godot_validator
    assert "-WindowStyle Hidden" in godot_validator


def test_compose_has_safe_defaults_and_does_not_require_dotenv() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["projecttown"]

    assert "env_file" not in service
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert "/tmp" in service["tmpfs"]
    assert service["environment"]["PROJECTTOWN_RUNTIME_API_PREFIX"] == (
        "${PROJECTTOWN_RUNTIME_API_PREFIX:-/api/v2}"
    )
    assert service["volumes"] == [
        "projecttown-data:/app/data",
        "projecttown-sandbox:/app/sandbox",
    ]
    assert compose["volumes"] == {
        "projecttown-data": None,
        "projecttown-sandbox": None,
    }


def test_opt_in_docker_settings_override_is_explicit_and_preserves_base_safety() -> (
    None
):
    base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    override = yaml.safe_load(
        (ROOT / "docker-compose.local-settings.yml").read_text(encoding="utf-8")
    )
    service = override["services"]["projecttown"]
    environment = service["environment"]
    assert ".secrets" not in base
    assert ".secrets/" in dockerignore
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "mkdir -p /app/data /app/sandbox /app/.secrets" in dockerfile
    assert "chmod 0700 /app/.secrets" in dockerfile
    assert '"--no-proxy-headers"' in dockerfile
    assert environment == {
        "PROJECTTOWN_ENABLE_LOCAL_SETTINGS_CONTROL": "true",
        "PROJECTTOWN_PROFILE": "development",
        "PROJECTTOWN_SECRET_SOURCE": "local_file",
        "PROJECTTOWN_ALLOW_CONTAINER_LOCAL_SETTINGS": "true",
        "PROJECTTOWN_LOCAL_SETTINGS_TRUSTED_PEER": "172.30.250.1",
    }
    assert service["volumes"] == [
        {
            "type": "volume",
            "source": "projecttown-local-settings",
            "target": "/app/.secrets",
            "read_only": False,
        }
    ]
    assert service["networks"] == ["local-settings"]
    assert (
        override["networks"]["local-settings"]["name"] == "projecttown_local_settings"
    )
    assert override["networks"]["local-settings"]["ipam"]["config"] == [
        {
            "subnet": "172.30.250.0/29",
            "gateway": "172.30.250.1",
        }
    ]
    assert (
        override["volumes"]["projecttown-local-settings"]["name"]
        == "projecttown_local_settings"
    )
    manager = (ROOT / "scripts" / "manage_docker_local_settings.py").read_text(
        encoding="utf-8"
    )
    assert "LOCAL_SETTINGS_DOCKER_SUBNET_CONFLICT" in manager
    assert '"docker", "cp"' in manager
    assert "model-providers.local.toml" not in manager


def test_release_ci_keeps_quality_and_coverage_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "checks:\n    runs-on: windows-latest" in workflow
    assert "linux-platform:\n    runs-on: ubuntu-latest" in workflow
    assert "python -m ruff check backend tests scripts" in workflow
    assert "python -m compileall -q backend scripts" in workflow
    assert "Prepare protected pytest base temp" in workflow
    assert workflow.count("Prepare protected pytest base temp") == 1
    assert "$ErrorActionPreference = \"Stop\"" in workflow
    assert "[Security.Principal.WindowsIdentity]::GetCurrent()" in workflow
    assert "$identity.User.Value" in workflow
    assert "& \"$env:SystemRoot\\System32\\icacls.exe\"" in workflow
    assert '"*${currentSid}:(OI)(CI)F"' in workflow
    assert "AreAccessRulesProtected" in workflow
    assert "unexpectedAllowSids" in workflow
    assert "$runnerTemp = (Resolve-Path -LiteralPath $env:RUNNER_TEMP).Path" in workflow
    assert '$systemTemp = Join-Path $runnerTemp "projecttown-ci-system-temp"' in workflow
    assert "icacls failed while protecting system temp" in workflow
    assert "Resolve-Path -LiteralPath $systemTemp" in workflow
    assert "system temp must remain outside the repository" in workflow
    for variable in ("TEMP", "TMP", "TMPDIR"):
        assert f'"{variable}=$canonicalSystemTemp" >> $env:GITHUB_ENV' in workflow
    control = "Diagnose Windows PowerShell control"
    acl_probe = "Diagnose Local Settings ACL primitive"
    assert workflow.count(control) == 1
    assert "timeout-minutes: 1" in workflow
    assert "python scripts/diagnose_windows_acl.py --control" in workflow
    assert workflow.count(acl_probe) == 1
    assert '[Guid]::NewGuid().ToString("N")' in workflow
    assert "New-Item -ItemType Directory -Path $probe | Out-Null" in workflow
    assert "New-Item -ItemType Directory -Path $probe -Force" not in workflow
    assert "ACL probe must remain outside the repository" in workflow
    assert "python scripts/diagnose_windows_acl.py --path $env:PROJECTTOWN_ACL_PROBE_PATH" in workflow
    assert "timeout-minutes: 2" in workflow
    assert "timeout=3" not in workflow
    diagnostic = (ROOT / "scripts" / "diagnose_windows_acl.py").read_text(
        encoding="utf-8"
    )
    assert "local_settings._windows_acl_restrict_script(True, trace=True)" in diagnostic
    assert "DIAGNOSTIC_TIMEOUT_SECONDS = 15" in diagnostic
    assert "CONTROL_COMMAND" in diagnostic
    assert "Get-Acl" not in diagnostic and ".secrets" not in diagnostic
    assert r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" in diagnostic
    assert 'environment["PROJECTTOWN_LOCAL_SETTINGS_PATH"] = str(args.path.resolve())' in diagnostic
    assert ".secrets" not in workflow[workflow.index("Diagnose Local Settings ACL primitive"):]
    assert "Remove-Item -LiteralPath $probe -Recurse -Force" in workflow
    pytest_command = "python -m pytest -q --basetemp=sandbox/tmp/ci-pytest-parent/run"
    assert pytest_command in workflow
    assert workflow.index("TMPDIR=$canonicalSystemTemp") < workflow.index(control) < workflow.index(
        acl_probe
    ) < workflow.index(pytest_command)
    assert "continue-on-error" not in workflow
    assert " -ra " in workflow
    assert "--cov-fail-under=80" in workflow
    assert workflow.count(
        "Path('sandbox/tmp').mkdir(parents=True, exist_ok=True)"
    ) == 1
    assert "Run Linux security and platform allowlist" in workflow
    assert "--junitxml=sandbox/tmp/linux-platform.xml" in workflow
    assert "Require Linux allowlist to have zero skips or errors" in workflow
    assert "linux platform junit gate failed" in workflow


def test_readme_direct_launch_and_quickstart_docker_path() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --env-file .env"
        in readme
    )
    quickstart = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    assert "docker compose up --build -d" in quickstart
    assert "http://127.0.0.1:8000/api/v2/health" in quickstart
    assert "`projecttown-data`" in quickstart
    assert "`projecttown-sandbox`" in quickstart
    assert "python -m uvicorn backend.main:app" not in quickstart


def test_public_candidate_keeps_sol_terra_config_and_preserves_font_notices() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".codex/backups/" in gitignore
    assert ".codex/agents/luna-*.toml" in gitignore
    assert "\n.codex/\n" not in gitignore

    config = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'default_subagent_model = "gpt-5.6-terra"' in config
    assert "[agents.terra_explorer]" in config
    assert "[agents.sol_escalation]" in config

    font_root = ROOT / "godot" / "assets" / "fonts"
    archive_note = (font_root / "README-Fusion-Pixel-Font.md").read_text(
        encoding="utf-8"
    )
    assert "https://github.com/TakWolf/fusion-pixel-font" in archive_note
    assert "docs/preview" not in archive_note
    assert (font_root / "OFL-Fusion-Pixel-Font.txt").is_file()
    assert (font_root / "THIRD_PARTY_NOTICES.md").is_file()


def test_default_pytest_scope_excludes_workspace_backups() -> None:
    pytest_config = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in pytest_config
    assert "sandbox" in pytest_config


def test_phase3e_v4_manifest_binds_participant_only_human_gate_lineage() -> None:
    path = ROOT / "examples/v3-phase-3/projecttown-phase3e-manifest-v4.json"
    data = path.read_bytes()
    manifest = json.loads(data.decode("utf-8"))
    assert hashlib.sha256(data).hexdigest() == (
        "24ce4fef9e069a92026790ca9fa859fca9480b3beb696d90caefb36a66521aa4"
    )
    assert manifest["candidate_profile"] == "projecttown-phase3e-rc-v4"
    assert manifest["procedure_version"] == "phase3e-release-candidate-v4"
    assert manifest["gate_model"] == (
        "participant_instance_plus_engineering_acceptance_plus_user_v1"
    )
    assert manifest["participant_count"] == 1
    assert manifest["participant_instance_rounds"] == [
        "R1-CONTROLLED-APPLY",
        "R2-REPORT-EXPORT",
    ]
    assert manifest["engineering_acceptance_schema"] == (
        "v3-phase3e-engineering-acceptance-v4"
    )
    assert manifest["round1_evidence"].endswith("never v4 execution authorization")
