from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_runtime_validator_requires_explicit_offline_contract() -> None:
    script = (ROOT / "scripts" / "validate_docker_runtime.ps1").read_text(
        encoding="utf-8"
    )

    assert "DefaultsFile" in script
    assert '"EvidenceRoot must be fresh (create-only)."' in script
    assert '"DefaultsFile must be a non-dot validation defaults file."' in script
    assert "--env-file $defaults" in script
    assert "--no-build --pull never" in script
    assert "docker image inspect $Image" in script
    assert "compose-config" in script
    assert "compose-restart" in script
    assert "Wait-ContainerHealthy" in script
    assert "TimeoutSeconds = 60" in script
    assert "sqlite-user-version" in script
    assert "logs --no-color --tail 200" in script
    assert "down --remove-orphans" in script
    assert "down -v" not in script


def test_validation_overlay_only_selects_existing_image_and_unique_port() -> None:
    overlay = (ROOT / "docker-compose.validation.yml").read_text(encoding="utf-8")
    defaults = (ROOT / "config" / "compose-validation.defaults").read_text(
        encoding="utf-8"
    )

    assert "PROJECTTOWN_VALIDATION_IMAGE" in overlay
    assert "build: !reset null" in overlay
    assert "ports: !override" in overlay
    assert "PROJECTTOWN_VALIDATION_PORT" in overlay
    assert not (ROOT / ".env").exists() or ".env" not in defaults
    assert "PROJECTTOWN_TOOL_ALLOWLIST=" in defaults
