from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.run_v3_material_workflow as cli
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
)

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_v3_material_workflow.py"
FIXTURES = Path(__file__).parents[2] / "examples" / "v3-phase-1"
SCENARIOS = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))[
    "scenarios"
]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _status(
    completed: subprocess.CompletedProcess[str], code: int = 0
) -> dict[str, object]:
    assert completed.returncode == code
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    status = json.loads(completed.stdout)
    assert status["schema_version"] == "v3-material-cli-status-v1"
    assert status["offline_calls"] == {"embedding": 0, "mcp": 0, "provider": 0}
    assert "confirmation_provenance" in status
    return status


@pytest.mark.parametrize(
    ("generator_version", "pdf_export_version"),
    [
        pytest.param(
            "deterministic-grounded-plan-v3",
            "v3-material-pdf-export-v3",
            id="v3",
        ),
        pytest.param(
            "deterministic-grounded-plan-v4",
            "v3-material-pdf-export-v4",
            id="v4",
        ),
    ],
)
def test_cli_exposes_explicit_generator_and_pdf_choices(
    generator_version: str, pdf_export_version: str
) -> None:
    draft = cli._build_parser().parse_args(
        [
            "draft",
            "--root",
            "D:\\root",
            "--file",
            "notes.md",
            "--task",
            "x",
            "--artifact-kind",
            "plan",
            "--draft-out",
            "D:\\draft.json",
            "--generator-version",
            generator_version,
        ]
    )
    pdf = cli._build_parser().parse_args(
        [
            "pdf-export",
            "--root",
            "D:\\root",
            "--result",
            "D:\\result.json",
            "--pdf-out",
            "D:\\plan.pdf",
            "--pdf-export-version",
            pdf_export_version,
        ]
    )
    assert draft.generator_version == generator_version
    assert pdf.pdf_export_version == pdf_export_version


def test_cli_runs_v5_runbook_and_rejects_mixed_or_duplicate_pdf(tmp_path: Path) -> None:
    root, outside = tmp_path / "materials", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "notes.md").write_text(
        "# Evidence\nLocal verification evidence.\n", encoding="utf-8"
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "runbook.pdf",
    )
    draft = _status(
        _run(
            "draft",
            "--root",
            str(root),
            "--file",
            "notes.md",
            "--task",
            task,
            "--artifact-kind",
            "plan",
            "--generator-version",
            "deterministic-grounded-plan-v5",
            "--draft-out",
            str(draft_path),
        )
    )
    result = _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    assert result["state"] == "generated"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    assert (
        record["draft"]["future_parameters"]["generator_version"]
        == "deterministic-grounded-plan-v5"
    )
    exported = _status(
        _run(
            "pdf-export",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--pdf-out",
            str(pdf_path),
            "--pdf-export-version",
            "v3-material-pdf-export-v5",
        )
    )
    assert exported["state"] == "generated" and pdf_path.is_file()
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(outside / "mixed.pdf"),
                "--pdf-export-version",
                "v3-material-pdf-export-v4",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v5",
            ),
            2,
        )["outcome"]
        == "rejected"
    )


def test_cli_runs_v6_runbook_and_rejects_mixed_or_duplicate_pdf(tmp_path: Path) -> None:
    root, outside = tmp_path / "materials", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "notes.md").write_text(
        "# Evidence\nLocal verification evidence.\n", encoding="utf-8"
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "runbook.pdf",
    )
    draft = _status(
        _run(
            "draft",
            "--root",
            str(root),
            "--file",
            "notes.md",
            "--task",
            task,
            "--artifact-kind",
            "plan",
            "--generator-version",
            "deterministic-grounded-plan-v6",
            "--draft-out",
            str(draft_path),
        )
    )
    result = _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    assert result["state"] == "generated"
    assert (
        json.loads(result_path.read_text(encoding="utf-8"))["draft"][
            "future_parameters"
        ]["generator_version"]
        == "deterministic-grounded-plan-v6"
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v6",
            )
        )["state"]
        == "generated"
    )
    assert pdf_path.is_file()
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(outside / "mixed.pdf"),
                "--pdf-export-version",
                "v3-material-pdf-export-v5",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v6",
            ),
            2,
        )["outcome"]
        == "rejected"
    )


def test_cli_runs_v8_runbook_and_rejects_mixed_or_duplicate_pdf(tmp_path: Path) -> None:
    from pypdf import PdfReader

    root, outside = tmp_path / "materials", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "notes.md").write_text(
        "# Evidence\nLocal verification evidence.\n", encoding="utf-8"
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "runbook-v8.pdf",
    )
    bindings = {
        "run_binding_binding_id": "cli-absolute-root-001",
        "run_binding_working_directory": str(root),
        "run_binding_candidate_path": str(outside / "candidate.pdf"),
        "run_binding_preview_path": str(outside / "preview.json"),
        "run_binding_manifest_path": str(outside / "manifest.json"),
        "run_binding_prior_study_evidence_path": str(outside / "prior-T002.json"),
        "run_binding_historical_evidence_root": str(outside / "history"),
        "run_binding_fresh_root": str(outside / "fresh"),
        "run_binding_fresh_evidence_root": str(outside / "fresh" / "evidence"),
        "run_binding_test_command": f"cd {root} && python -m pytest -q",
        "run_binding_fresh_result_output_path": str(outside / "fresh-result.json"),
        "run_binding_fresh_result_evidence_label": "cli-absolute-root-evidence",
        "run_binding_expected_page_count": "4",
        "run_binding_approved_hash_provenance_tuple_source": str(
            outside / "approved-tuple.json"
        ),
        "run_binding_planned_study_evidence_output": str(
            outside / "planned-study-T002.json"
        ),
        "run_binding_preflight_result": "passed",
    }
    binding_args = [
        item
        for key, value in bindings.items()
        for item in ("--constraint", f"{key}={value}")
    ]
    draft = _status(
        _run(
            "draft",
            "--root",
            str(root),
            "--file",
            "notes.md",
            "--task",
            task,
            "--artifact-kind",
            "plan",
            "--generator-version",
            "deterministic-grounded-plan-v7",
            *binding_args,
            "--draft-out",
            str(draft_path),
        )
    )
    generated = _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    assert generated["state"] == "generated"
    result_record = json.loads(result_path.read_text(encoding="utf-8"))
    assert (
        result_record["draft"]["future_parameters"]["generator_version"]
        == "deterministic-grounded-plan-v7"
    )
    assert dict(result_record["draft"]["constraints"]) == bindings
    preview = result_record["preview_markdown"]
    assert "PATH_REF[" in preview
    assert "COMMAND_REF[" in preview
    assert "[WORKING_DIRECTORY]" in preview
    assert all(
        value not in preview
        for key, value in bindings.items()
        if key
        not in {
            "run_binding_binding_id",
            "run_binding_fresh_result_evidence_label",
            "run_binding_expected_page_count",
            "run_binding_preflight_result",
        }
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v7",
            )
        )["state"]
        == "generated"
    )
    with pdf_path.open("rb") as stream:
        pages = PdfReader(stream).pages
        extracted = "\n".join(page.extract_text() or "" for page in pages)
    page_text = [page.extract_text() or "" for page in pages]
    assert len(pages) == 4
    assert "M00 Run Binding preflight" in page_text[0]
    assert "Initial State: BLOCK" in page_text[1]
    assert all(token in page_text[2] for token in ("Verification Matrix", "M00", "M08"))
    assert all(
        token in page_text[3]
        for token in ("状态合同", "角色与 User Gate", "引用", "离线边界")
    )
    assert all(token not in extracted for token in ("<b>", "<br/>", "&lt;", "&gt;"))
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(outside / "mixed.pdf"),
                "--pdf-export-version",
                "v3-material-pdf-export-v6",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v7",
            ),
            2,
        )["outcome"]
        == "rejected"
    )


def test_cli_runs_v9_runbook_and_rejects_mixed_or_duplicate_pdf(tmp_path: Path) -> None:
    from pypdf import PdfReader

    root, outside = tmp_path / "materials", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "notes.md").write_text(
        "# Evidence\nLocal verification evidence.\n", encoding="utf-8"
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "runbook-v9.pdf",
    )
    bindings = {
        "run_binding_runbook_version": "projecttown-human-pdf-v9",
        "run_binding_verification_target_version": "projecttown-human-pdf-v8",
        "run_binding_verification_target_id": "v8-T002-candidate",
        "run_binding_verification_run_id": "cli-v9-run-001",
        "run_binding_working_directory": str(root),
        "run_binding_candidate_path": str(outside / "candidate.pdf"),
        "run_binding_preview_record_path": str(outside / "preview.json"),
        "run_binding_candidate_hash_source": "SHA256:declared-v8-tuple",
        "run_binding_candidate_expected_page_count": "<TO BIND BEFORE RUN>",
        "run_binding_manifest_path": str(outside / "manifest.json"),
        "run_binding_prior_study_evidence_path": str(outside / "prior-T002.json"),
        "run_binding_historical_evidence_root": str(outside / "history"),
        "run_binding_fresh_root": str(outside / "fresh"),
        "run_binding_fresh_evidence_root": str(outside / "fresh" / "evidence"),
        "run_binding_material_source_root": str(root),
        "run_binding_fresh_draft_path": str(outside / "fresh-draft.json"),
        "run_binding_fresh_result_command": "python scripts/run_v3_material_workflow.py generate",
        "run_binding_fresh_result_output_path": str(outside / "fresh-result.json"),
        "run_binding_fresh_result_schema": "v3-material-result-session-v1",
        "run_binding_fresh_result_evidence_label": "v9-cli-evidence",
        "run_binding_planned_study_evidence_output": str(
            outside / "planned-study-T002.json"
        ),
    }
    binding_args = [
        item
        for key, value in bindings.items()
        for item in ("--constraint", f"{key}={value}")
    ]
    draft = _status(
        _run(
            "draft",
            "--root",
            str(root),
            "--file",
            "notes.md",
            "--task",
            task,
            "--artifact-kind",
            "plan",
            "--generator-version",
            "deterministic-grounded-plan-v8",
            *binding_args,
            "--draft-out",
            str(draft_path),
        )
    )
    generated = _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    assert generated["state"] == "generated"
    result_record = json.loads(result_path.read_text(encoding="utf-8"))
    assert (
        result_record["draft"]["future_parameters"]["generator_version"]
        == "deterministic-grounded-plan-v8"
    )
    constraints = dict(result_record["draft"]["constraints"])
    assert constraints == bindings
    assert "run_binding_fresh_confirmation_hash" not in constraints
    legacy_v8_keys = {
        "run_binding_binding_id",
        "run_binding_expected_page_count",
        "run_binding_approved_hash_provenance_tuple_source",
        "run_binding_preflight_result",
    }
    assert not legacy_v8_keys.intersection(constraints)
    preview = result_record["preview_markdown"]
    assert all(
        token in preview
        for token in (
            "BINDING BLOCKED",
            "M00-01",
            "Verification Matrix",
            "Citation Usage Audit",
        )
    )
    assert "- status: PREFLIGHT PASS" not in preview
    assert "fresh_confirmation_hash | UNBOUND" in preview
    assert str(outside) not in preview
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v8",
            )
        )["state"]
        == "generated"
    )
    with pdf_path.open("rb") as stream:
        pages = PdfReader(stream).pages
        extracted = "\n".join(page.extract_text() or "" for page in pages)
    assert pages
    assert all(
        token in extracted
        for token in ("M00 Run Binding preflight", "Verification Matrix", "M08")
    )
    original_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(outside / "mixed.pdf"),
                "--pdf-export-version",
                "v3-material-pdf-export-v7",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v8",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest() == original_hash


def test_cli_runs_v10_runbook_and_rejects_mixed_or_duplicate_pdf(
    tmp_path: Path,
) -> None:
    from pypdf import PdfReader

    root, outside = tmp_path / "materials", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "notes.md").write_text(
        "# Evidence\nLocal verification evidence.\n", encoding="utf-8"
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "runbook-v10.pdf",
    )
    history_study, history_work, fresh = (
        outside / "history-study",
        outside / "history-work",
        outside / "fresh-v10",
    )
    history_study.mkdir()
    history_work.mkdir()
    fresh.mkdir()
    candidate = history_work / "T002-runbook-v8.pdf"
    candidate.write_bytes(b"%PDF-1.4\n")
    preview = history_work / "T002-result-generator-v7-recovery-01.json"
    preview.write_text("{}", encoding="utf-8")
    prior = history_study / "T002.json"
    prior.write_text("{}", encoding="utf-8")
    manifest = root / "projecttown-trial-manifest-v10.json"
    manifest.write_text("{}", encoding="utf-8")
    bindings = {
        "execution": "offline",
        "preserve_v1_v2_contracts": "true",
        "run_binding_runbook_version": "projecttown-human-pdf-v10",
        "run_binding_verification_target_version": "projecttown-human-pdf-v8",
        "run_binding_verification_target_id": "projecttown-v3-phase2-human-pdf-v8-20260829-001:T002",
        "run_binding_verification_run_id": "cli-v10-run-001",
        "run_binding_candidate_profile": "projecttown-human-pdf-v8",
        "run_binding_candidate_sha256": "1686e8e33ba39e0d25a554c8750e03781a68cea8a2205f911777b53eb3ecca68",
        "run_binding_candidate_pdf_export_version": "v3-material-pdf-export-v7",
        "run_binding_candidate_pdf_renderer_version": "projecttown-reportlab-pdf-v7",
        "run_binding_candidate_expected_page_count": "4",
        "run_binding_candidate_path": str(candidate),
        "run_binding_preview_record_path": str(preview),
        "run_binding_manifest_path": str(manifest),
        "run_binding_historical_study_root": str(history_study),
        "run_binding_historical_work_root": str(history_work),
        "run_binding_historical_result_json_path": str(preview),
        "run_binding_approved_provenance_tuple_source": str(prior),
        "run_binding_prior_study_evidence_path": str(prior),
        "run_binding_final_snapshot_path": str(candidate),
        "run_binding_working_directory": str(root),
        "run_binding_material_source_root": str(root),
        "run_binding_fresh_root": str(fresh),
        "run_binding_fresh_draft_path": str(fresh / "draft.json"),
        "run_binding_fresh_result_output_path": str(fresh / "result.json"),
        "run_binding_fresh_evidence_root": str(fresh / "evidence"),
        "run_binding_fresh_result_schema": "v3-material-result-session-v1",
        "run_binding_planned_study_evidence_output": "<TO BIND BEFORE RUN>",
        "run_binding_user_disposition_record_path": "<TO BIND BEFORE RUN>",
        "run_binding_release_authorization_record_path": "<TO BIND BEFORE RUN>",
    }
    binding_args = [
        item
        for key, value in bindings.items()
        for item in ("--constraint", f"{key}={value}")
    ]
    draft = _status(
        _run(
            "draft",
            "--root",
            str(root),
            "--file",
            "notes.md",
            "--task",
            task,
            "--artifact-kind",
            "plan",
            "--generator-version",
            "deterministic-grounded-plan-v9",
            *binding_args,
            "--draft-out",
            str(draft_path),
        )
    )
    generated = _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    assert generated["state"] == "generated"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    assert (
        record["draft"]["future_parameters"]["generator_version"]
        == "deterministic-grounded-plan-v9"
    )
    assert "M00-07" in record["preview_markdown"]
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v9",
            )
        )["state"]
        == "generated"
    )
    with pdf_path.open("rb") as stream:
        pages = PdfReader(stream).pages
        extracted = "\n".join(page.extract_text() or "" for page in pages)
    assert len(pages) == 3
    assert all(
        token in extracted for token in ("M00-01", "M07", "M08", "Citation Usage Audit")
    )
    original_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(outside / "mixed.pdf"),
                "--pdf-export-version",
                "v3-material-pdf-export-v8",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
                "--pdf-export-version",
                "v3-material-pdf-export-v9",
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest() == original_hash


def _copy_scenario(tmp_path: Path, scenario: dict[str, object]) -> Path:
    root = tmp_path / "materials"
    shutil.copytree(FIXTURES / str(scenario["root"]), root)
    return root.resolve()


def _draft(
    root: Path, out: Path, scenario: dict[str, object], *, reverse: bool = False
) -> dict[str, object]:
    items = list(dict(scenario["constraints"]).items())
    files = list(scenario["files"])
    if reverse:
        items.reverse()
        files.reverse()
    command = [
        "draft",
        "--root",
        str(root),
        "--task",
        str(scenario["task"]),
        "--artifact-kind",
        str(scenario["artifact_kind"]),
        "--draft-out",
        str(out),
    ]
    for name in files:
        command += ["--file", str(name)]
    if scenario["readme_target"] is not None:
        command += ["--readme-target", str(scenario["readme_target"])]
    for key, value in items:
        command += ["--constraint", f"{key}={value}"]
    return _status(_run(*command))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: str(item["name"]))
def test_committed_workflow_scenarios(
    tmp_path: Path, scenario: dict[str, object]
) -> None:
    root, outside = _copy_scenario(tmp_path, scenario), tmp_path / "outside"
    outside.mkdir()
    source_hashes = {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    draft_path, result_path, export_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "artifact.md",
    )
    draft = _draft(root, draft_path, scenario)
    assert draft["state"] == "waiting_confirmation" and draft_path.exists()
    assert draft["confirmation_provenance"] == "not_confirmed"
    missing = _run(
        "generate",
        "--root",
        str(root),
        "--draft",
        str(draft_path),
        "--result-out",
        str(result_path),
    )
    _status(missing, 2)
    assert not result_path.exists()
    wrong = _run(
        "generate",
        "--root",
        str(root),
        "--draft",
        str(draft_path),
        "--confirmation-hash",
        "0" * 64,
        "--result-out",
        str(result_path),
    )
    _status(wrong, 2)
    result = _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    assert result["state"] == scenario["expected_state"]
    assert result["integrity"] == "self_consistent"
    assert result["confirmation_provenance"] == "explicit_current_invocation"
    checked = _status(_run("check", "--root", str(root), "--session", str(result_path)))
    assert checked["integrity"] == "self_consistent"
    assert checked["freshness"] == "fresh"
    assert checked["confirmation_provenance"] == "unanchored_external_session"
    preview = _status(
        _run("preview", "--root", str(root), "--result", str(result_path))
    )
    assert preview["freshness"] == "fresh" and "preview_markdown" in preview
    assert preview["confirmation_provenance"] == "unanchored_external_session"
    exported = _run(
        "export",
        "--root",
        str(root),
        "--result",
        str(result_path),
        "--export-out",
        str(export_path),
    )
    if scenario["expected_state"] == "generated":
        _status(exported)
        assert export_path.exists()
    else:
        _status(exported, 2)
        assert not export_path.exists()
    for content in [
        result_path.read_text(encoding="utf-8"),
        *([export_path.read_text(encoding="utf-8")] if export_path.exists() else []),
    ]:
        assert str(root).casefold() not in content.casefold()
    assert source_hashes == {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_deterministic_order_and_stale_recovery(tmp_path: Path) -> None:
    scenario = next(item for item in SCENARIOS if item["name"] == "research-plan-cn")
    roots = [_copy_scenario(tmp_path / name, scenario) for name in ("one", "two")]
    outputs: list[tuple[Path, Path, Path]] = []
    for index, root in enumerate(roots):
        outside = tmp_path / f"outside-{index}"
        outside.mkdir()
        draft_path, result_path, export_path = (
            outside / "draft.json",
            outside / "result.json",
            outside / "artifact.md",
        )
        draft = _draft(root, draft_path, scenario, reverse=bool(index))
        _status(
            _run(
                "generate",
                "--root",
                str(root),
                "--draft",
                str(draft_path),
                "--confirmation-hash",
                str(draft["contract_hash"]),
                "--result-out",
                str(result_path),
            )
        )
        _status(
            _run(
                "export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--export-out",
                str(export_path),
            )
        )
        outputs.append((draft_path, result_path, export_path))
    assert [item.read_bytes() for item in outputs[0]] == [
        item.read_bytes() for item in outputs[1]
    ]
    root, (_, result_path, export_path) = roots[0], outputs[0]
    frozen = _status(_run("preview", "--result", str(result_path)))["preview_markdown"]
    (root / str(scenario["files"][0])).write_text("changed", encoding="utf-8")
    assert (
        _status(_run("check", "--root", str(root), "--session", str(result_path)))[
            "freshness"
        ]
        == "stale_or_unavailable"
    )
    assert (
        _status(_run("preview", "--root", str(root), "--result", str(result_path)), 2)[
            "code"
        ]
        == "STALE_OR_UNAVAILABLE"
    )
    assert (
        _status(
            _run(
                "export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--export-out",
                str(tmp_path / "new.md"),
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(_run("preview", "--result", str(result_path)))["preview_markdown"]
        == frozen
    )
    assert export_path.exists()


def test_pdf_export_is_create_only_and_user_directed(tmp_path: Path) -> None:
    scenario = next(item for item in SCENARIOS if item["artifact_kind"] == "plan")
    root, outside = _copy_scenario(tmp_path, scenario), tmp_path / "outside"
    outside.mkdir()
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "plan.pdf",
    )
    draft = _draft(root, draft_path, scenario)
    _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    exported = _status(
        _run(
            "pdf-export",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--pdf-out",
            str(pdf_path),
        )
    )
    assert exported["outputs"]["pdf"] is True and exported["pdf_path"] == str(pdf_path)
    assert "JSON" in str(
        exported["user_guidance"]
    ) and pdf_path.read_bytes().startswith(b"%PDF-")
    assert (
        _status(
            _run(
                "pdf-export",
                "--root",
                str(root),
                "--result",
                str(result_path),
                "--pdf-out",
                str(pdf_path),
            ),
            2,
        )["code"]
        == "INVALID_OUTPUT_PATH"
    )


def test_pdf_export_v2_requires_explicit_version_and_reports_it(tmp_path: Path) -> None:
    scenario = next(item for item in SCENARIOS if item["artifact_kind"] == "plan")
    root, outside = _copy_scenario(tmp_path, scenario), tmp_path / "outside"
    outside.mkdir()
    draft_path, result_path, pdf_path = (
        outside / "draft.json",
        outside / "result.json",
        outside / "plan-v2.pdf",
    )
    draft = _draft(root, draft_path, scenario)
    _status(
        _run(
            "generate",
            "--root",
            str(root),
            "--draft",
            str(draft_path),
            "--confirmation-hash",
            str(draft["contract_hash"]),
            "--result-out",
            str(result_path),
        )
    )
    status = _status(
        _run(
            "pdf-export",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--pdf-out",
            str(pdf_path),
            "--pdf-export-version",
            "v3-material-pdf-export-v2",
        )
    )
    assert status["pdf_export_version"] == "v3-material-pdf-export-v2"
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    rejected = _status(
        _run(
            "pdf-export",
            "--root",
            str(root),
            "--result",
            str(result_path),
            "--pdf-out",
            str(outside / "unknown.pdf"),
            "--pdf-export-version",
            "unknown",
        ),
        2,
    )
    assert rejected["code"] == "INVALID_ARGUMENTS"


def test_rejections_are_terminal_safe_and_create_only(tmp_path: Path) -> None:
    scenario = SCENARIOS[0]
    root, outside = _copy_scenario(tmp_path, scenario), tmp_path / "outside"
    outside.mkdir()
    task = f"x\x1b[2J\u202e {root}"
    draft_path = outside / "draft.json"
    status = _status(
        _run(
            "draft",
            "--root",
            str(root),
            "--file",
            str(scenario["files"][0]),
            "--task",
            task,
            "--artifact-kind",
            "plan",
            "--draft-out",
            str(draft_path),
        )
    )
    assert str(root).casefold() not in json.dumps(status).casefold()
    assert (
        _status(
            _run(
                "draft",
                "--root",
                str(root),
                "--file",
                str(scenario["files"][0]),
                "--task",
                "again",
                "--artifact-kind",
                "plan",
                "--draft-out",
                str(draft_path),
            ),
            2,
        )["outcome"]
        == "rejected"
    )
    assert (
        _status(
            _run(
                "draft",
                "--root",
                str(root) + "\\.",
                "--file",
                str(scenario["files"][0]),
                "--task",
                "x",
                "--artifact-kind",
                "plan",
                "--draft-out",
                str(outside / "bad.json"),
            ),
            2,
        )["code"]
        == "INVALID_ROOT"
    )
    assert (
        _status(
            _run(
                "draft",
                "--root",
                str(root),
                "--file",
                str(scenario["files"][0]),
                "--task",
                "x",
                "--artifact-kind",
                "plan",
                "--constraint",
                "a=1",
                "--constraint",
                "A=2",
                "--draft-out",
                str(outside / "bad.json"),
            ),
            2,
        )["code"]
        == "INVALID_CONSTRAINTS"
    )


@pytest.mark.parametrize(
    ("error", "exit_code", "outcome", "state"),
    [
        (PublicationRollbackError(), 2, "rejected", "rolled_back"),
        (
            PublicationAttentionError(),
            3,
            "attention_required",
            "committed_needs_attention",
        ),
    ],
)
def test_cli_publication_states_are_not_general_rejections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    exit_code: int,
    outcome: str,
    state: str,
) -> None:
    root = _copy_scenario(tmp_path, SCENARIOS[0])
    output = (tmp_path / "outside" / "draft.json").resolve()
    output.parent.mkdir()
    monkeypatch.setattr(
        cli, "publish_new_file", lambda *_args: (_ for _ in ()).throw(error)
    )
    assert (
        cli.main(
            [
                "draft",
                "--root",
                str(root),
                "--file",
                str(SCENARIOS[0]["files"][0]),
                "--task",
                "x",
                "--artifact-kind",
                "plan",
                "--draft-out",
                str(output),
            ]
        )
        == exit_code
    )
    status = json.loads(capsys.readouterr().out)
    assert status["outcome"] == outcome
    assert status["publication_state"] == state
