"""Static Phase 4E boundary checks; these tests do not start an application."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Literal

from backend.app import material_workflow
from backend.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
_AUTHORING_MODULES = (
    ROOT / "backend" / "app" / "local_workspace_task.py",
    ROOT / "backend" / "app" / "local_workspace_task_api.py",
    ROOT / "backend" / "app" / "local_workspace_task_authoring.py",
    ROOT / "backend" / "app" / "local_workspace_task_authoring_api.py",
)
_FORBIDDEN_MODULE_PREFIXES = (
    "backend.app.provider_secrets",
    "backend.app.v1.mcp_adapter",
    "backend.app.v1.openai_adapter",
    "backend.app.v1.qwen_adapter",
    "backend.app.v1.provider_summary",
    "backend.app.v1.model_runtime",
    "backend.app.v1.orchestration",
    "backend.app.v1.service",
    "backend.app.runtime",
    "httpx",
    "subprocess",
    "apscheduler",
    "celery",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                imports.add(f"backend.app.{node.module}")
            else:
                imports.add(node.module)
    return imports


def _has_forbidden_import(imports: set[str]) -> bool:
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in _FORBIDDEN_MODULE_PREFIXES
    )


def test_phase4_authoring_and_local_mcp_are_default_off() -> None:
    settings = Settings()

    assert settings.enable_local_workspace_task is False
    assert settings.enable_local_workspace_task_create is False
    assert settings.enable_local_mcp is False


def test_phase4_authoring_has_no_cross_boundary_imports() -> None:
    imports = set().union(*(_imported_modules(path) for path in _AUTHORING_MODULES))

    assert not _has_forbidden_import(imports)
    assert "backend.app.material_workflow" in imports


def test_material_workflow_uses_retained_deterministic_rag_and_zero_call_contract() -> (
    None
):
    imports = _imported_modules(ROOT / "backend" / "app" / "material_workflow.py")
    fields = material_workflow.FutureParameters.model_fields

    # The retained local ``v1.rag`` implementation is explicitly permitted.
    assert "backend.app.v1.rag" in imports
    assert material_workflow._RETRIEVAL_VERSION == "segmented-deterministic-rag-v2"
    assert fields["provider_calls"].annotation == Literal[0]
    assert fields["embedding_calls"].annotation == Literal[0]
    assert fields["mcp_calls"].annotation == Literal[0]


def test_phase4c_documents_follow_participant_only_v4_gate() -> None:
    phase4 = (ROOT / "docs" / "v3-phase-4.md").read_text(encoding="utf-8")
    product_direction = (ROOT / "docs" / "v3-product-direction.md").read_text(
        encoding="utf-8"
    )
    acceptance = (ROOT / "docs" / "v3-phase-0-4-acceptance-2026-09-01.md").read_text(
        encoding="utf-8"
    )

    assert "projecttown-phase3e-rc-v4" in phase4
    assert "participant_instance_plus_engineering_acceptance_plus_user_v1" in phase4
    assert "EngineeringAcceptanceV4" in phase4
    assert "同一 Participant identity label 完成 R1/R2" in phase4
    assert "Independent Study Reviewer" not in phase4
    assert "Reviewer control rating" not in phase4
    assert "Reviewer PASS" not in phase4
    assert "不创建第二套 4C Study、adapter" in phase4
    assert "或任何新记录" in phase4
    assert "RETAIN` 可以保留候选，但不能代替 4C 所需的 RC acceptance" in phase4
    assert "4C 通过也不授权 4D" in phase4
    assert "3E v4 additive Study/Round/Summary/User RC records" in product_direction
    assert "每轮 EngineeringAcceptanceV4" in product_direction
    assert "4C 对 Phase 3E v4 的只读 checkpoint" in product_direction
    assert "4C 仅只读核验 Phase 3E v4 canonical check/status" in acceptance
    assert "v4 Participant 两轮、每轮 EngineeringAcceptanceV4 与 User RC" in acceptance
