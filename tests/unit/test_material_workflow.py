from __future__ import annotations

import json
import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

import backend.app.material_workflow as workflow
from backend.app.material_workflow import (
    MaterialWorkflowError,
    create_draft,
    generate_result,
    load_external_session,
    load_session,
    parse_session_bytes,
    publish_new_direct_child,
    publish_new_file,
    render_export,
    render_pdf_export,
    render_preview,
    serialize_session,
)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "materials"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\nA fact\n", encoding="utf-8")
    (root / "data.txt").write_text("value\n", encoding="utf-8")
    return root


def test_generator_v9_runbook_binds_v8_inputs_and_keeps_future_outputs_blocked(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    from backend.app.pdf_export import runbook_v10_flow_geometry

    root = _root(tmp_path)
    history_study, history_work, fresh = (
        tmp_path / "history-study",
        tmp_path / "history-work",
        tmp_path / "fresh-v10",
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
        "run_binding_verification_run_id": "engineering-v10-001",
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
    draft = create_draft(
        root,
        ["notes.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=bindings,
        generator_version="deterministic-grounded-plan-v9",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert "PATH_REF[" in result.preview_markdown
    assert str(candidate) not in result.preview_markdown
    assert "UNRESOLVED / BINDING BLOCKED" in result.preview_markdown
    assert "draft→generate" in result.preview_markdown
    assert "WAITING USER" in result.preview_markdown
    assert "M06 | Mandatory | History" in result.preview_markdown
    assert "| S006 | out of scope" in result.preview_markdown
    assert "--file README.md --file docs/limitations.md" in result.preview_markdown
    assert (
        "--file docs/v2-closeout.md --file docs/validation-v1.0.md"
        in result.preview_markdown
    )
    assert (
        "--root <material_source_root> --session <preview_record_path>"
        in result.preview_markdown
    )
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v9")
    (tmp_path / "v10-layout-fixture.pdf").write_bytes(pdf)
    assert pdf == render_pdf_export(
        root, result, export_version="v3-material-pdf-export-v9"
    )
    pages = PdfReader(BytesIO(pdf)).pages
    assert len(pages) == 3
    page_text = [page.extract_text() or "" for page in pages]
    assert all(f"M00-0{index}" in page_text[0] for index in range(1, 8))
    assert "canonical binding unique/type" in page_text[0]
    assert "LOCAL-ONLY" in page_text[0]
    assert "Canonical declared value" in page_text[0]
    assert "M00-01" not in page_text[1]
    assert "Inventory" in page_text[1] and "Independent Study" in page_text[1]
    assert "ACCEPT / RETAIN" in page_text[1]
    assert (
        "Verification Matrix" in page_text[2] and "Citation Usage Audit" in page_text[2]
    )
    extracted = "\n".join(page.extract_text() or "" for page in pages)
    assert all(
        token in extracted
        for token in ("M00-01", "M00-07", "M08", "Citation Usage Audit")
    )
    assert "NOT AUTHORIZED is a legal no-op" in extracted
    normalized_pdf_text = re.sub(r"[\s\u200b\x00]+", "", extracted)
    assert re.sub(r"\s+", "", str(candidate)) in normalized_pdf_text
    assert re.sub(r"\s+", "", str(preview)) in normalized_pdf_text
    assert re.sub(r"\s+", "", str(root)) in normalized_pdf_text
    assert "Resolvedpath:" in normalized_pdf_text
    (fresh / "result.json").write_text("{}", encoding="utf-8")
    assert workflow.verify_result(root, result)
    (fresh / "result.json").unlink()
    (fresh / "result.json").mkdir()
    assert not workflow.verify_result(root, result)
    nodes, connectors = runbook_v10_flow_geometry()
    assert len(nodes) == 10 and len(connectors) == 9
    for connector in connectors[:6]:
        source, target = connector
        assert source[1] > target[1]
        assert source[1] - target[1] >= 2
    for connector in connectors[6:]:
        assert len(connector) == 4
        assert connector[-1][1] > nodes[7][1] + nodes[7][3]
    with pytest.raises(ValueError, match="INVALID_FLOW_GEOMETRY"):
        runbook_v10_flow_geometry(gap_mm=5)
    with pytest.raises(MaterialWorkflowError, match="material workflow rejected"):
        render_pdf_export(root, result, export_version="v3-material-pdf-export-v8")
    for replacement in (
        {**bindings, "run_binding_unknown": "forbidden"},
        {**bindings, "unknown_general": "forbidden"},
        {**bindings, "run_binding_candidate_path": "relative.pdf"},
        {**bindings, "run_binding_user_disposition_record_path": str(candidate)},
        {**bindings, "run_binding_fresh_root": str(root / "inside-source")},
    ):
        with pytest.raises(MaterialWorkflowError, match="material workflow rejected"):
            create_draft(
                root,
                ["notes.md"],
                task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
                artifact_kind="plan",
                constraints=replacement,
                generator_version="deterministic-grounded-plan-v9",
            )


def test_generator_v8_runbook_keeps_m00_blocked_without_a_reference_resolver(
    tmp_path: Path,
) -> None:
    """v9 never turns opaque display references into a preflight pass."""
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    values = {
        "run_binding_runbook_version": "v9",
        "run_binding_verification_target_version": "v8",
        "run_binding_verification_target_id": "T002",
        "run_binding_verification_run_id": "engineering-v9",
        "run_binding_candidate_path": "D:/evidence/candidate.pdf",
        "run_binding_preview_record_path": "D:/evidence/preview.json",
        "run_binding_candidate_hash_source": "approved tuple",
        "run_binding_candidate_expected_page_count": "0",
        "run_binding_manifest_path": "D:/history/manifest.json",
        "run_binding_prior_study_evidence_path": "D:/history/T002.json",
        "run_binding_historical_evidence_root": "D:/history",
        "run_binding_working_directory": "D:/fresh",
        "run_binding_fresh_root": "D:/fresh",
        "run_binding_fresh_evidence_root": "D:/fresh/evidence",
        "run_binding_material_source_root": "D:/materials",
        "run_binding_fresh_draft_path": "D:/fresh/draft.json",
        "run_binding_fresh_confirmation_hash": "a" * 64,
        "run_binding_fresh_result_command": "run_v3_material_workflow.py generate",
        "run_binding_fresh_result_output_path": "D:/fresh/result.json",
        "run_binding_fresh_result_schema": "v3-material-result-session-v1",
        "run_binding_fresh_result_evidence_label": "fresh-v9",
        "run_binding_planned_study_evidence_output": "D:/new/T002.json",
    }
    draft = create_draft(
        root,
        ["notes.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=values,
        generator_version="deterministic-grounded-plan-v8",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert "status: BINDING BLOCKED" in result.preview_markdown
    assert "PATH_REF[" in result.preview_markdown
    assert "status: PREFLIGHT PASS" not in result.preview_markdown
    assert "D:/materials" not in result.preview_markdown
    assert "D:/fresh" not in result.preview_markdown
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v8")
    assert pdf == render_pdf_export(
        root, result, export_version="v3-material-pdf-export-v8"
    )
    (tmp_path / "runbook-v9.pdf").write_bytes(pdf)
    pages = PdfReader(BytesIO(pdf)).pages
    assert len(pages) >= 2
    extracted = "\n".join(page.extract_text() or "" for page in pages)
    assert "M00-01" in extracted
    assert "BINDING BLOCKED" in extracted
    assert "does not equal PREFLIGHT PASS" in extracted
    assert "D:/materials" not in extracted and "D:/fresh" not in extracted
    assert all(token in extracted for token in ("Matrix A", "Matrix B", "S001", "S008"))

    # Text-position bboxes provide a deterministic density guard without
    # treating a raster screenshot as the canonical PDF representation. Footer
    # and empty visitor events are excluded so an orphan citation page cannot
    # pass merely because it has a page number.
    for page in pages:
        coordinates: list[float] = []
        body_text: list[str] = []

        def visitor(_text, current_matrix, text_matrix, _font, _size):
            y = float(current_matrix[5]) + float(text_matrix[5])
            text = _text.strip()
            if text and y > 45:
                coordinates.append(y)  # noqa: B023 - visitor is consumed immediately below.
                body_text.append(text)  # noqa: B023 - visitor is consumed immediately below.

        page.extract_text(visitor_text=visitor)
        assert coordinates
        assert len("".join(body_text)) >= 120
        assert max(coordinates) - min(coordinates) >= 240
        assert min(coordinates) <= 160
    nodes, connectors = __import__(
        "backend.app.pdf_export", fromlist=["runbook_v9_flow_geometry"]
    ).runbook_v9_flow_geometry(node_count=3, gap_mm=6)
    assert len(nodes) == 3 and len(connectors) == 2
    assert connectors[0][1] < nodes[0][1]


@pytest.mark.parametrize(
    "task",
    ("制定 v3 本地资料工作流后续迭代计划", "制定离线资料工作流维护检查清单"),
)
def test_v9_generic_plan_fixtures_delegate_to_existing_renderer(
    tmp_path: Path, task: str
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v8",
    )
    result = generate_result(root, draft, draft.contract_hash)
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v8")
    (tmp_path / "generic-plan-v9.pdf").write_bytes(pdf)
    pages = PdfReader(BytesIO(pdf)).pages
    assert pages and any((page.extract_text() or "").strip() for page in pages)


def test_v9_m00_status_is_extractable_for_eight_source_fixture(tmp_path: Path) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    (root / "third.md").write_text("# Third\nHistory\n", encoding="utf-8")
    (root / "fourth.md").write_text("# Fourth\nEvidence\n", encoding="utf-8")
    for index in range(5, 9):
        (root / f"source-{index}.md").write_text(
            f"# Source {index}\nVerification evidence {index}\n", encoding="utf-8"
        )
    draft = create_draft(
        root,
        [
            "notes.md",
            "data.txt",
            "third.md",
            "fourth.md",
            "source-5.md",
            "source-6.md",
            "source-7.md",
            "source-8.md",
        ],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=(
            ("run_binding_candidate_path", "D:/candidate.pdf"),
            ("run_binding_working_directory", "D:/fresh"),
        ),
        generator_version="deterministic-grounded-plan-v8",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert len(result.citations) == 8
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v8")
    assert pdf == render_pdf_export(
        root, result, export_version="v3-material-pdf-export-v8"
    )
    (tmp_path / "runbook-v9-four-source.pdf").write_bytes(pdf)
    pages = PdfReader(BytesIO(pdf)).pages
    extracted = "\n".join(page.extract_text() or "" for page in pages)
    assert "BINDING BLOCKED" in extracted
    assert "does not equal PREFLIGHT PASS" in extracted
    assert len(pages) == 3
    assert "Repository-relative source span" in extracted
    assert "Citation Usage Audit" in extracted
    assert "provider/embedding/MCP/network/paid calls=0" in extracted
    assert all(f"S{index:03d}" in extracted for index in range(1, 9))
    # Eight source spans must be present in the rendered citation index itself,
    # not merely as static identifiers in Citation Usage Audit.
    assert {
        "data.txt:1-1",
        "fourth.md:1-2",
        "notes.md:1-2",
        "source-5.md:1-2",
        "source-6.md:1-2",
        "source-7.md:1-2",
        "source-8.md:1-2",
        "third.md:1-2",
    }.issubset(extracted.split())
    for page in pages:
        body: list[str] = []
        positions: list[float] = []

        def visitor(text, current_matrix, text_matrix, _font, _size):
            y = float(current_matrix[5]) + float(text_matrix[5])
            if text.strip() and y > 45:
                body.append(text.strip())  # noqa: B023 - consumed immediately below.
                positions.append(y)  # noqa: B023 - consumed immediately below.

        page.extract_text(visitor_text=visitor)
        assert len("".join(body)) >= 120
        assert max(positions) - min(positions) >= 240
        assert min(positions) <= 160


@pytest.mark.parametrize(
    ("node_count", "scale"),
    ((2, 0.75), (2, 1.0), (2, 1.25), (8, 0.75), (8, 1.0), (8, 1.1)),
)
def test_v9_flow_geometry_keeps_connectors_outside_adjacent_nodes(
    scale: float, node_count: int
) -> None:
    from backend.app.pdf_export import runbook_v9_flow_geometry

    nodes, connectors = runbook_v9_flow_geometry(
        node_count=node_count, gap_mm=6, scale=scale
    )
    assert len(nodes) == node_count
    assert len(connectors) == node_count - 1
    for x, y, width, height in nodes:
        assert 0 <= x and 0 <= y
        assert x + width <= 172
        assert y + height <= 148
    for source, target, connector in zip(
        nodes[:-1], nodes[1:], connectors, strict=True
    ):
        source_bottom = source[1]
        target_top = target[1] + target[3]
        assert source_bottom - target_top >= 6 * scale - 1e-9
        assert connector[1] <= source_bottom - 1 * scale
        assert connector[3] >= target_top + 1 * scale
        assert connector[0] == connector[2] == source[0] + source[2] / 2
        assert target[0] <= connector[0] <= target[0] + target[2]


@pytest.mark.parametrize("font_size", (8.0, 9.4, 12.0))
@pytest.mark.parametrize(
    "label",
    (
        "M01-M06 verification / 验证与历史证据核验",
        "READY FOR USER GATE / 用户处置前准备就绪",
        "Independent Study / 独立真人评鉴与证据记录",
    ),
)
def test_v9_flow_labels_fit_node_width_for_font_sizes(
    label: str, font_size: float
) -> None:
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    from backend.app.pdf_export import _font_path

    font_name = "ProjectTownV9GeometryTest"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(_font_path())))
    # 132mm node minus 8mm combined label padding; the longest selected label
    # must remain readable at the largest supported test font.
    assert pdfmetrics.stringWidth(label, font_name, font_size) <= 124 * mm


@pytest.mark.parametrize(
    "kwargs",
    (
        {"node_count": 2, "gap_mm": 5.9},
        {"node_count": 2, "node_width_mm": 161},
        {"node_count": 8, "scale": 1.25},
        {"node_count": 0},
    ),
)
def test_v9_flow_geometry_rejects_invalid_gap_or_bounds(
    kwargs: dict[str, float],
) -> None:
    from backend.app.pdf_export import runbook_v9_flow_geometry

    with pytest.raises(ValueError, match="INVALID_FLOW_GEOMETRY"):
        runbook_v9_flow_geometry(**kwargs)  # type: ignore[arg-type]


def test_generator_v7_runbook_has_m00_states_vector_flow_and_four_pages(
    tmp_path: Path,
) -> None:
    """The v8 renderer is strict, readable, and does not mutate v6 routing."""
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    bindings = {
        "run_binding_binding_id": "run-001",
        "run_binding_working_directory": "D:/work",
        "run_binding_candidate_path": "D:/evidence/candidate.pdf",
        "run_binding_preview_path": "D:/evidence/preview.json",
        "run_binding_manifest_path": "D:/history/manifest.json",
        "run_binding_prior_study_evidence_path": "D:/history/T002.json",
        "run_binding_historical_evidence_root": "D:/history",
        "run_binding_fresh_root": "D:/fresh",
        "run_binding_fresh_evidence_root": "D:/fresh/evidence",
        "run_binding_test_command": "python -m pytest -q",
        "run_binding_fresh_result_output_path": "D:/fresh/T002-result.json",
        "run_binding_fresh_result_evidence_label": "fresh-result-v8",
        "run_binding_expected_page_count": "4",
        "run_binding_approved_hash_provenance_tuple_source": "D:/history/tuple.json",
        "run_binding_planned_study_evidence_output": "D:/new-study/T002.json",
        "run_binding_preflight_result": "passed",
    }
    draft = create_draft(
        root,
        ["notes.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=bindings,
        generator_version="deterministic-grounded-plan-v7",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert "status: PREFLIGHT PASS" in result.preview_markdown
    assert "| M02B |" in result.preview_markdown
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v7")
    reader = PdfReader(BytesIO(pdf))
    pages = [page.extract_text() or "" for page in reader.pages]
    assert len(pages) == 4
    assert "M00 Run Binding preflight" in pages[0]
    assert "复验对象与证据盘点" in pages[0]
    assert all(
        token in pages[0]
        for token in (
            "UNKNOWN",
            "Rerun",
            "History",
            "User",
            "Inventory UNKNOWN is blocking; detailed category and failure semantics are defined by M00-M08 Matrix.",
        )
    )
    assert all(
        token not in pages[0]
        for token in (
            "UNKNOWN/BLO\nCK",
            "可重跑验\n证",
            "不可重跑历史\n证据",
            "用户持有的发布\n事项",
        )
    )
    assert all(
        token in pages[1]
        for token in (
            "Initial State: BLOCK",
            "Independent Study",
            "可重跑验证",
            "不可重跑历史证据",
            "用户持有的发布事项",
        )
    )
    assert all(token in pages[2] for token in ("Verification Matrix", "M00", "M08"))
    assert all(
        token in pages[3]
        for token in (
            "状态合同",
            "PASS/FAIL 标准",
            "角色与 User Gate",
            "引用",
            "离线边界",
        )
    )
    assert all(len(page) >= 350 for page in pages)
    extracted = "\n".join(pages)
    assert "approved_hash_tuple_source" in pages[0]
    assert "planned_study_output" in pages[0]
    assert "approved_hash_provenance_tuple_source" not in pages[0]
    assert "planned_study_evidence_output" not in pages[0]
    assert "run_binding_approved_hash_provenance_tuple_source" in dict(
        result.draft.constraints
    )
    assert "run_binding_planned_study_evidence_output" in dict(result.draft.constraints)
    assert all(token not in extracted for token in ("<b>", "<br/>", "&lt;", "&gt;"))
    # The Matrix renderer must never split a readable English token mid-word.
    # These were the concrete page-three regressions from the v8 visual sample.
    assert all(
        token not in pages[2]
        for token in (
            "Categor\ny",
            "comm\nand",
            "out\nput",
            "labe\nl",
            "ren\nder",
            "Publi\nsh",
            "Relea\nse",
        )
    )
    matrix_text = re.sub(r"\s+", " ", pages[2])
    assert all(
        token in matrix_text
        for token in ("Category", "command + output", "render", "Release action")
    )
    matrix = result.preview_markdown.split("## Verification Matrix\n", 1)[1].split(
        "## 状态合同\n", 1
    )[0]
    assert [
        matrix.count(f"| {ident} |")
        for ident in (
            "M00",
            "M01",
            "M02",
            "M02B",
            "M03",
            "M04",
            "M05",
            "M06",
            "M07",
            "M08",
        )
    ] == [1] * 10


@pytest.mark.parametrize(
    ("constraints", "expected"),
    [
        ({}, "UNBOUND"),
        (
            {"run_binding_binding_id": "run-001"},
            "BINDING BLOCKED",
        ),
        (
            {"run_binding_preflight_result": "unknown"},
            "BINDING BLOCKED",
        ),
    ],
    ids=["unbound", "partially-bound", "unknown-preflight"],
)
def test_generator_v7_runbook_preflight_blocks_incomplete_or_unknown_bindings(
    tmp_path: Path, constraints: dict[str, str], expected: str
) -> None:
    if constraints.get("run_binding_preflight_result") == "unknown":
        constraints = {
            **{
                f"run_binding_{field}": field
                for field in (
                    "binding_id",
                    "working_directory",
                    "candidate_path",
                    "preview_path",
                    "manifest_path",
                    "prior_study_evidence_path",
                    "historical_evidence_root",
                    "fresh_root",
                    "fresh_evidence_root",
                    "test_command",
                    "fresh_result_output_path",
                    "fresh_result_evidence_label",
                    "expected_page_count",
                    "approved_hash_provenance_tuple_source",
                    "planned_study_evidence_output",
                )
            },
            **constraints,
        }
    root = _root(tmp_path)
    draft = create_draft(
        root,
        ["notes.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=constraints,
        generator_version="deterministic-grounded-plan-v7",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert f"status: {expected}" in result.preview_markdown


def test_generator_v7_runbook_bound_unvalidated_keeps_long_values_readable(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    forward_root = "D:/工程证据/" + "非常长的中英混排路径-long-segment/" * 3
    backslash_root = "C:\\工程证据\\" + "另一段-Long-CJK/" * 2
    fields = {
        "binding_id": "run-cjk-001",
        "working_directory": backslash_root + "working-directory",
        "candidate_path": forward_root + "candidate.pdf",
        "preview_path": forward_root + "preview.json",
        "manifest_path": forward_root + "manifest.json",
        "prior_study_evidence_path": forward_root + "prior-study.json",
        "historical_evidence_root": forward_root + "history",
        "fresh_root": forward_root + "fresh",
        "fresh_evidence_root": forward_root + "fresh/evidence",
        "test_command": (
            "cd " + backslash_root + "working-directory && python -m pytest -q"
        ),
        "fresh_result_output_path": forward_root + "fresh-result.json",
        "fresh_result_evidence_label": "fresh-cjk-evidence",
        "expected_page_count": "4",
        "approved_hash_provenance_tuple_source": forward_root + "tuple.json",
        "planned_study_evidence_output": forward_root + "new-study-output.json",
    }
    constraints = {f"run_binding_{key}": value for key, value in fields.items()}
    draft = create_draft(
        root,
        ["notes.md"],
        task="制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项",
        artifact_kind="plan",
        constraints=constraints,
        generator_version="deterministic-grounded-plan-v7",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert "status: BOUND-UNVALIDATED" in result.preview_markdown
    assert dict(result.draft.constraints) == constraints
    # Canonical Windows separators must survive the runbook replacement path.
    assert dict(result.draft.constraints)["run_binding_working_directory"].startswith(
        "C:\\工程证据\\"
    )
    assert "PATH_REF[" in result.preview_markdown
    assert "COMMAND_REF[" in result.preview_markdown
    assert "candidate.pdf" in result.preview_markdown
    assert "[WORKING_DIRECTORY]" in result.preview_markdown
    assert "Full canonical Input/Output/Historical values" in result.preview_markdown
    assert "command + output + label" in result.preview_markdown
    sensitive_values = tuple(
        value
        for key, value in fields.items()
        if key
        not in {"binding_id", "fresh_result_evidence_label", "expected_page_count"}
    )
    assert all(value not in result.preview_markdown for value in sensitive_values)
    assert "<TO BIND BEFORE RUN>" not in result.preview_markdown
    from io import BytesIO

    from pypdf import PdfReader

    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v7")
    pages = [page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages]
    assert len(pages) == 4
    assert "candidate.pdf" in pages[0]
    assert "PATH_REF[" in pages[0]
    assert all(value not in "\n".join(pages) for value in sensitive_values)
    flow = pages[1]
    assert all(
        boundary in flow
        for boundary in ("可重跑验证", "不可重跑历史证据", "用户持有的发布事项")
    )
    ordered = (
        "Initial State: BLOCK",
        "盘点与分类",
        "M00 PREFLIGHT PASS",
        "M01-M06 verification",
        "VERIFIED",
        "Independent Study",
        "READY FOR USER GATE",
        "User disposition: ACCEPT / REVISE / STOP",
    )
    assert [flow.index(token) for token in ordered] == sorted(
        flow.index(token) for token in ordered
    )


@contextmanager
def _error(code: str) -> Generator[None, None, None]:
    with pytest.raises(
        MaterialWorkflowError, match="material workflow rejected"
    ) as exc:
        yield
    assert exc.value.code == code


def test_draft_is_order_independent_and_contains_no_source_or_absolute_path(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    first = create_draft(
        root,
        ["notes.md", "data.txt"],
        task=" task\r\n",
        artifact_kind="plan",
        constraints={"z": "two", "A": "one"},
    )
    second = create_draft(
        root,
        ["data.txt", "notes.md"],
        task="task",
        artifact_kind="plan",
        constraints={"A": "one", "z": "two"},
    )
    assert serialize_session(first) == serialize_session(second)
    assert parse_session_bytes(serialize_session(first)) == first
    assert first.state == "waiting_confirmation"
    assert (
        first.future_parameters.provider_calls
        == first.future_parameters.embedding_calls
        == first.future_parameters.mcp_calls
        == 0
    )
    assert first.future_parameters.generator_version == "deterministic-grounded-plan-v2"
    assert first.future_parameters.retrieval_version == "segmented-deterministic-rag-v2"
    assert first.future_parameters.segmentation_version == "markdown-block-v2"
    rendered = serialize_session(first).decode("utf-8")
    assert str(root) not in rendered and "A fact" not in rendered


def test_readme_contract_and_constraints_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for kind, target in (
        ("readme", None),
        ("plan", "notes.md"),
        ("readme", "data.txt"),
    ):
        with _error("INVALID_README_TARGET"):
            create_draft(
                root,
                ["notes.md", "data.txt"],
                task="x",
                artifact_kind=kind,
                readme_target=target,
            )
    draft = create_draft(
        root,
        ["notes.md", "data.txt"],
        task="x",
        artifact_kind="readme",
        readme_target="notes.md",
    )
    assert draft.readme_target == "notes.md"
    for constraints in ({"A": "x", "a": "y"}, {"": "x"}, {"x": " "}):
        with _error("INVALID_CONSTRAINTS"):
            create_draft(
                root,
                ["notes.md"],
                task="x",
                artifact_kind="plan",
                constraints=constraints,
            )


def test_hard_link_and_unstable_source_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    linked = root / "linked.txt"
    try:
        os.link(root / "data.txt", linked)
    except OSError as error:
        pytest.skip(f"hard links unavailable: {error}")
    with _error("UNSTABLE_SOURCE"):
        create_draft(root, ["data.txt"], task="x", artifact_kind="plan")
    monkeypatch.setattr(
        "backend.app.material_workflow.read_stable_regular_file", lambda *_a, **_k: None
    )
    with _error("UNSTABLE_SOURCE"):
        create_draft(root, ["notes.md"], task="x", artifact_kind="plan")


def test_cross_device_source_is_rejected_with_platform_safe_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)

    def cross_device(*_args: object) -> os.stat_result:
        raise MaterialWorkflowError("UNTRUSTED_SOURCE")

    monkeypatch.setattr(workflow, "_check_same_device_ancestors", cross_device)
    with _error("UNTRUSTED_SOURCE"):
        create_draft(root, ["notes.md"], task="x", artifact_kind="plan")


def test_nested_ancestor_identity_change_after_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    nested = root / "nested"
    nested.mkdir()
    source = nested / "notes.md"
    source.write_text("same content\n", encoding="utf-8")
    stable_read = workflow.read_stable_regular_file

    def change_ancestor(*args: object, **kwargs: object):
        result = stable_read(*args, **kwargs)
        metadata = nested.lstat()
        os.utime(nested, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(workflow, "read_stable_regular_file", change_ancestor)
    with _error("MATERIAL_SET_CHANGED"):
        create_draft(root, ["nested/notes.md"], task="x", artifact_kind="plan")
    assert source.read_text(encoding="utf-8") == "same content\n"


def test_source_nlink_change_after_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    source = root / "notes.md"
    linked = root / "late-link.md"
    stable_read = workflow.read_stable_regular_file

    def add_link(*args: object, **kwargs: object):
        result = stable_read(*args, **kwargs)
        try:
            os.link(source, linked)
        except OSError as error:
            pytest.skip(f"hard links unavailable: {error}")
        return result

    monkeypatch.setattr(workflow, "read_stable_regular_file", add_link)
    with _error("UNSTABLE_SOURCE"):
        create_draft(root, ["notes.md"], task="x", artifact_kind="plan")


def test_parse_is_canonical_and_detects_wrong_hash_and_unknown_field(
    tmp_path: Path,
) -> None:
    session = create_draft(
        _root(tmp_path), ["notes.md"], task="x", artifact_kind="plan"
    )
    data = serialize_session(session)
    assert parse_session_bytes(data) == session
    for tampered, code in (
        (data.replace(b'"task":"x"', b'"task":"y"'), "INVALID_CONTRACT_HASH"),
        (data[:-1] + b"\n\n", "NONCANONICAL_SESSION"),
        (data.replace(b"{", b'{"unknown":1,', 1), "INVALID_SESSION"),
        (
            data.replace(b'"task"', b'"task"', 1).replace(
                b'"schema_version"', b'"schema_version"', 1
            )
            + b" ",
            "NONCANONICAL_SESSION",
        ),
    ):
        with _error(code):
            parse_session_bytes(tampered)
    duplicate = data.replace(b"{", b'{"schema_version":"junk",', 1)
    with _error("INVALID_SESSION"):
        parse_session_bytes(duplicate)


def test_load_external_single_link_session(tmp_path: Path) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    session_path = output / "draft.json"
    session_path.write_bytes(
        serialize_session(
            create_draft(root, ["notes.md"], task="x", artifact_kind="plan")
        )
    )
    assert load_session(root, session_path).task == "x"
    assert load_external_session(session_path).task == "x"
    with _error("INVALID_SESSION_PATH"):
        load_session(root, root / "notes.md")
    linked = output / "linked.json"
    try:
        os.link(session_path, linked)
    except OSError as error:
        pytest.skip(f"hard links unavailable: {error}")
    with _error("UNSTABLE_SESSION"):
        load_session(root, linked)


def test_publish_is_new_external_and_exact_under_link_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    publish_new_file(root, target, b"complete")
    assert target.read_bytes() == b"complete"
    with _error("INVALID_OUTPUT_PATH"):
        publish_new_file(root, target, b"other")
    with _error("INVALID_OUTPUT_PATH"):
        publish_new_file(root, root / "inside.json", b"no")
    failed = output / "failed.json"
    monkeypatch.setattr(
        "backend.app.material_workflow.os.link",
        lambda *_a: (_ for _ in ()).throw(OSError("no link")),
    )
    with _error("OUTPUT_PUBLISH_FAILED"):
        publish_new_file(root, failed, b"all-or-nothing")
    assert not failed.exists()


def test_publish_new_direct_child_is_exact_and_single_link(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "study.json"

    publish_new_direct_child(root, target, b'{"complete":true}\n')

    assert target.read_bytes() == b'{"complete":true}\n'
    assert target.lstat().st_nlink == 1


def test_publish_new_direct_child_rejects_nested_existing_and_noncanonical_paths(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    existing = root / "existing.json"
    existing.write_bytes(b"keep")
    for target in (
        root / "nested" / "study.json",
        existing,
        root / ".." / root.name / "noncanonical.json",
    ):
        with _error("INVALID_OUTPUT_PATH"):
            publish_new_direct_child(root, target, b"new")
    assert existing.read_bytes() == b"keep"
    assert not (root / "nested" / "study.json").exists()


def test_publish_new_direct_child_rejects_unsafe_root_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(workflow, "is_safe_directory", lambda _metadata: False)

    with _error("INVALID_ROOT"):
        publish_new_direct_child(root, root / "study.json", b"new")
    assert not (root / "study.json").exists()


def test_publish_new_direct_child_rejects_root_replacement_before_final_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    target = root / "study.json"
    original_open = workflow.os.open
    replaced = False

    def replace_root_before_staging(*args: object, **kwargs: object) -> int:
        nonlocal replaced
        if not replaced and isinstance(args[0], Path) and args[0].parent == root:
            replaced = True
            moved = tmp_path / "former-materials"
            root.rename(moved)
            root.mkdir()
        return original_open(*args, **kwargs)

    monkeypatch.setattr(workflow.os, "open", replace_root_before_staging)
    with _error("OUTPUT_PARENT_CHANGED"):
        publish_new_direct_child(root, target, b"new")
    assert replaced
    assert not target.exists()


def test_publish_new_direct_child_shares_rollback_state_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    target = root / "study.json"
    original_unlink = Path.unlink

    def fail_temporary_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith(".projecttown-"):
            raise OSError("temp unlink blocked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temporary_unlink)
    with pytest.raises(workflow.PublicationRollbackError) as error:
        publish_new_direct_child(root, target, b"new")
    assert error.value.code == "PUBLICATION_ROLLED_BACK"
    assert not target.exists()
    assert all(
        item.stat().st_size == 0
        for item in root.iterdir()
        if item.name.startswith(".projecttown-")
    )


def test_publish_write_failure_does_not_create_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    failed = output / "write-failed.json"
    monkeypatch.setattr(workflow.os, "write", lambda *_args: 0)
    with _error("OUTPUT_PUBLISH_FAILED"):
        publish_new_file(root, failed, b"all-or-nothing")
    assert not failed.exists()


def test_parse_rejects_nonfinite_and_oversize() -> None:
    with _error("INVALID_SESSION"):
        parse_session_bytes(b'{"x":NaN}\n')
    with _error("INVALID_SESSION"):
        parse_session_bytes(b"{" + b" " * (2 * 1_048_576) + b"}")


@pytest.mark.parametrize("kind", ["plan", "report", "readme"])
def test_generation_is_grounded_verified_and_read_only(
    tmp_path: Path, kind: str
) -> None:
    root = _root(tmp_path)
    draft = create_draft(
        root,
        ["notes.md", "data.txt"],
        task="summarize",
        artifact_kind=kind,
        readme_target="notes.md" if kind == "readme" else None,
    )
    before = (root / "notes.md").read_bytes()
    result = generate_result(root, draft, draft.contract_hash)
    assert result.state == "generated"
    assert parse_session_bytes(serialize_session(result)) == result
    assert render_preview(result) == result.preview_markdown
    assert render_export(root, result).endswith(b"\n")
    assert (root / "notes.md").read_bytes() == before


def test_plan_v2_is_user_facing_and_pdf_is_deterministic(tmp_path: Path) -> None:
    from pypdf import PdfReader

    root = _root(tmp_path)
    draft = create_draft(
        root, ["notes.md", "data.txt"], task="制定资料迭代计划", artifact_kind="plan"
    )
    result = generate_result(root, draft, draft.contract_hash)
    preview = render_preview(result)
    for heading in (
        "计划总结",
        "主要执行流程",
        "阶段与优先级",
        "交付物",
        "验收",
        "引用",
        "离线边界",
    ):
        assert heading in preview
    assert "Examine `" not in preview
    first, second = render_pdf_export(root, result), render_pdf_export(root, result)
    assert first.startswith(b"%PDF-") and first == second
    pdf_path = tmp_path / "plan.pdf"
    pdf_path.write_bytes(first)
    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert 1 <= len(reader.pages) <= 2 and "计划总结" in text and "主要执行顺序" in text
    assert all(len((page.extract_text() or "").strip()) >= 40 for page in reader.pages)
    assert "**" not in text and "`" not in text and "Purpose:" not in text
    assert "ˋ" not in text and "⏎" not in text and "\x00" not in text


def test_plan_v2_citation_is_minimal_and_old_v1_stays_verifiable(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    result = generate_result(
        root,
        create_draft(root, ["notes.md"], task="different plan", artifact_kind="plan"),
        create_draft(
            root, ["notes.md"], task="different plan", artifact_kind="plan"
        ).contract_hash,
    )
    assert all(
        item.line_start == item.line_end
        for item in result.citations
        if item.method == "lexical"
    )
    old = result.model_copy(
        update={
            "draft": result.draft.model_copy(
                update={
                    "future_parameters": result.draft.future_parameters.model_copy(
                        update={
                            "generator_version": "deterministic-grounded-template-v1"
                        }
                    )
                }
            )
        }
    )
    # Re-hashing cannot make a v2 body masquerade as an old frozen generator.
    assert not workflow.verify_result_integrity(_rehash(old))


@pytest.mark.parametrize("kind", ["plan", "report", "readme"])
def test_pdf_supports_all_artifact_kinds(tmp_path: Path, kind: str) -> None:
    root = _root(tmp_path)
    draft = create_draft(
        root,
        ["notes.md"],
        task="summarize evidence",
        artifact_kind=kind,
        readme_target="notes.md" if kind == "readme" else None,
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert render_pdf_export(root, result).startswith(b"%PDF-")
    assert render_pdf_export(
        root, result, export_version="v3-material-pdf-export-v2"
    ).startswith(b"%PDF-")


def test_pdf_presentation_v2_is_visual_deterministic_and_versioned(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "# 路线图\n\n"
        "PDF 候选需要优先级、风险和用户决策的可读表达。\n\n"
        "验收标准是重新打开 PDF 并核对引用。\n",
        encoding="utf-8",
    )
    draft = create_draft(
        root, ["notes.md", "data.txt"], task="制定中文迭代计划", artifact_kind="plan"
    )
    result = generate_result(root, draft, draft.contract_hash)
    frozen_v1 = render_pdf_export(root, result)
    first = render_pdf_export(root, result, export_version="v3-material-pdf-export-v2")
    second = render_pdf_export(root, result, export_version="v3-material-pdf-export-v2")
    assert first == second and first != frozen_v1
    reader = PdfReader(BytesIO(first))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert all(
        label in text for label in ("执行摘要", "P0", "行动", "交付物", "验收标准")
    )
    assert "绝对路径" not in text and str(root) not in text
    content = b"".join(page.get_contents().get_data() for page in reader.pages)
    assert b" re" in content and b" l" in content  # vector rectangles and arrow lines
    with _error("UNSUPPORTED_PDF_EXPORT_VERSION"):
        render_pdf_export(root, result, export_version="unknown")


def test_pdf_presentation_v2_uses_stage_and_section_boundaries(tmp_path: Path) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    from backend.app.pdf_export import _section_bullets

    root = _root(tmp_path)
    draft = create_draft(
        root, ["notes.md", "data.txt"], task="制定阶段计划", artifact_kind="plan"
    )
    result = generate_result(root, draft, draft.contract_hash)
    data = render_pdf_export(root, result, export_version="v3-material-pdf-export-v2")
    pages = PdfReader(BytesIO(data)).pages
    text = "\n".join(page.extract_text() or "" for page in pages)
    assert all(item in text for item in ("阶段 1", "阶段 2", "阶段 3", "阶段 4"))
    assert all(
        item in text for item in ("P0", "P1", "P2", "■ 紧急", "● 重要", "▲ 后续")
    )
    assert "阶段 1（P0）" not in text
    assert _section_bullets(
        [
            "## 计划总结",
            "- **风险：**不能进入信息框。",
            "## 依赖、阻断项与用户决策",
            "- **依赖：**资料可读取。",
            "### 阶段 1（P0）：标题不得误分类",
            "- **阻断项：**冲突未解决。",
            "## 引用",
            "- [S001] `notes.md` 第 1-1 行：正文。",
        ],
        "依赖、阻断项与用户决策",
    ) == ["依赖：资料可读取。", "阻断项：冲突未解决。"]
    assert (
        len(pages) <= 2 and min(len(page.extract_text() or "") for page in pages) >= 180
    )


def test_pdf_v2_priority_range_uses_highest_urgency_and_snake_order() -> None:
    from backend.app.pdf_export import _plan_stages

    stages = _plan_stages(
        [
            "### 阶段 1（P0）：建立基线",
            "- 目的：明确事实。",
            "### 阶段 2（P0/P1）：改善候选成果",
            "- 目的：改善可读性。",
            "### 阶段 3（P1）：执行验证",
            "- 目的：验证结果。",
            "### 阶段 4（P2）：用户决定",
            "- 目的：记录决定。",
        ]
    )
    assert [(item["阶段号"], item["优先级"], item["主优先级"]) for item in stages] == [
        ("1", "P0", "P0"),
        ("2", "P0/P1", "P0"),
        ("3", "P1", "P1"),
        ("4", "P2", "P2"),
    ]


def test_pdf_v2_flow_arrows_have_clear_shafts_between_boxes() -> None:
    from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
    from reportlab.lib import colors

    from backend.app.pdf_export import _flow_drawing

    palette = {
        "ink": colors.HexColor("#183A5A"),
        "muted": colors.HexColor("#55718A"),
        "p0": colors.HexColor("#B53D4B"),
        "p1": colors.HexColor("#D47918"),
        "p2": colors.HexColor("#2B7FA8"),
    }
    drawing = _flow_drawing(
        [
            {"优先级": "P0", "主优先级": "P0", "标题": "建立产品差距基线"},
            {"优先级": "P0/P1", "主优先级": "P0", "标题": "改善用户可读候选成果"},
            {"优先级": "P1", "主优先级": "P1", "标题": "工程验证与恢复矩阵"},
            {"优先级": "P2", "主优先级": "P2", "标题": "独立真人 Study 决策"},
        ],
        Drawing,
        Rect,
        Line,
        Polygon,
        String,
        colors,
        palette,
    )

    rectangles = [shape for shape in drawing.contents if isinstance(shape, Rect)]
    assert [(rect.x, rect.y, rect.width, rect.height) for rect in rectangles] == [
        (44, 104, 112, 44),
        (184, 104, 112, 44),
        (324, 104, 112, 44),
        (324, 32, 112, 44),
        (184, 32, 112, 44),
        (44, 32, 112, 44),
    ]
    assert 184 - (44 + 112) == 324 - (184 + 112) == 28
    assert 104 - (32 + 44) == 28

    horizontal_lines = [
        shape
        for shape in drawing.contents
        if isinstance(shape, Line) and shape.y1 == shape.y2
    ]
    assert [(line.x1, line.x2) for line in horizontal_lines] == [
        (156, 175),
        (296, 315),
        (324, 305),
        (184, 165),
    ]
    assert {abs(line.x2 - line.x1) for line in horizontal_lines} == {19}

    horizontal_heads = [
        shape
        for shape in drawing.contents
        if isinstance(shape, Polygon)
        and min(shape.points[1::2]) == max(shape.points[1::2]) - 8
    ]
    assert len(horizontal_heads) == 4
    assert [min(head.points[::2]) for head in horizontal_heads] == [170, 310, 305, 165]
    assert [max(head.points[::2]) for head in horizontal_heads] == [175, 315, 310, 170]

    vertical_lines = [
        shape
        for shape in drawing.contents
        if isinstance(shape, Line) and shape.x1 == shape.x2
    ]
    assert [(line.x1, line.y1, line.x2, line.y2) for line in vertical_lines] == [
        (380, 104, 380, 85)
    ]
    vertical_heads = [
        shape
        for shape in drawing.contents
        if isinstance(shape, Polygon)
        and min(shape.points[1::2]) == max(shape.points[1::2]) - 5
    ]
    assert [head.points for head in vertical_heads] == [[380, 85, 376, 90, 384, 90]]

    strings = [shape.text for shape in drawing.contents if isinstance(shape, String)]
    assert "独立真人 Study 决策" in strings
    assert not any("…" in text for text in strings)

    lifecycle_title_sets = (
        ("建立问题基线", "改善候选成果", "正负与恢复验证", "独立用户决策"),
        (
            "建立产品差距基线",
            "改善用户可读候选成果",
            "工程验证与恢复矩阵",
            "独立真人 Study 决策",
        ),
    )
    for titles in lifecycle_title_sets:
        title_drawing = _flow_drawing(
            [
                {"优先级": "P0", "主优先级": "P0", "标题": titles[0]},
                {"优先级": "P0/P1", "主优先级": "P0", "标题": titles[1]},
                {"优先级": "P1", "主优先级": "P1", "标题": titles[2]},
                {"优先级": "P2", "主优先级": "P2", "标题": titles[3]},
            ],
            Drawing,
            Rect,
            Line,
            Polygon,
            String,
            colors,
            palette,
        )
        title_strings = [
            shape.text for shape in title_drawing.contents if isinstance(shape, String)
        ]
        assert all(title in title_strings for title in titles)
        assert not any("…" in text for text in title_strings)


def test_generator_v3_runbook_is_explicit_and_pdf_tuple_fails_closed(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    from backend.app.pdf_export import _render_pdf_v3

    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "# Historical record\n\nA substantive delivery verification fact.\n",
        encoding="utf-8",
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    default = create_draft(root, ["notes.md"], task=task, artifact_kind="plan")
    assert (
        default.future_parameters.generator_version == "deterministic-grounded-plan-v2"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v3",
    )
    result = generate_result(root, draft, draft.contract_hash)
    required = (
        "执行摘要",
        "复验对象与证据盘点",
        "可重跑验证",
        "不可重跑历史证据",
        "用户持有的发布事项",
        "Verification Matrix",
        "PASS/FAIL 标准",
        "角色与 User Gate",
        "Reviewer-defined verification policy",
        "UNKNOWN/BLOCK",
    )
    assert all(item in result.preview_markdown for item in required)
    inventory = result.preview_markdown.split("## 复验对象与证据盘点\n", 1)[1].split(
        "\n\n## 可重跑验证", 1
    )[0]
    inventory_rows = [line for line in inventory.splitlines() if line.startswith("- ")]
    assert len(inventory_rows) == 9
    assert all(
        all(
            field in row
            for field in (
                "path-or-id:",
                "source:",
                "status:",
                "category:",
                "rerunnable:",
                "modifiable:",
                "final authority:",
            )
        )
        for row in inventory_rows
    )
    assert all(
        f"[{citation.id}]" in result.preview_markdown for citation in result.citations
    )
    for responsibility in (
        "manifest; source: candidate manifest; status: UNKNOWN/BLOCK; category: 不可重跑历史证据; rerunnable: no; modifiable: no; final authority: Verifier.",
        "provenance; source: result lineage; status: UNKNOWN/BLOCK; category: 不可重跑历史证据; rerunnable: no; modifiable: no; final authority: Verifier.",
        "Verifier：负责工程检查、fresh-root rerun、历史证据只读核验和证据记录；可 read/reopen/render/compare/verify，不可 overwrite/Apply/retain/discard/替换 final。",
        "Independent Study Reviewer：使用固定任务评鉴，记录 rating、disposition、PASS/REVISE/FAIL；不修改候选且不替 User 决定发布。",
        "User：独占 Accept/Retain/Discard/Apply/Publish/替换 final；没有明确 User 结论即不继续。",
    ):
        assert responsibility in result.preview_markdown
    first = render_pdf_export(root, result, export_version="v3-material-pdf-export-v3")
    assert first == render_pdf_export(
        root, result, export_version="v3-material-pdf-export-v3"
    )
    extracted = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(first)).pages
    )
    assert all(column in extracted for column in ("ID", "object", "category", "Owner"))
    order = (
        "执行摘要",
        "复验对象与证据盘点",
        "复验流程图",
        "三类复验层级",
        "Verification Matrix",
        "PASS/FAIL 标准",
        "角色与 User Gate",
    )
    assert [extracted.index(label) for label in order] == sorted(
        extracted.index(label) for label in order
    )
    for export_version in ("v3-material-pdf-export-v1", "v3-material-pdf-export-v2"):
        with _error("UNSUPPORTED_PDF_EXPORT_VERSION"):
            render_pdf_export(root, result, export_version=export_version)
    with _error("UNSUPPORTED_PDF_EXPORT_VERSION"):
        render_pdf_export(
            root,
            generate_result(root, default, default.contract_hash),
            export_version="v3-material-pdf-export-v3",
        )
    invalid = result.model_copy(
        update={
            "preview_markdown": result.preview_markdown.replace(
                "## PASS/FAIL 标准\n", "", 1
            )
        }
    )
    with pytest.raises(RuntimeError, match="INVALID_RUNBOOK_STRUCTURE"):
        _render_pdf_v3(invalid)
    duplicate = result.model_copy(
        update={
            "preview_markdown": result.preview_markdown.replace(
                "## 角色与 User Gate\n",
                "## 角色与 User Gate\n\n## 角色与 User Gate\n",
                1,
            )
        }
    )
    with pytest.raises(RuntimeError, match="INVALID_RUNBOOK_STRUCTURE"):
        _render_pdf_v3(duplicate)


def test_runbook_flow_drawing_is_vector_ordered_and_untruncated() -> None:
    from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
    from reportlab.lib import colors

    from backend.app.pdf_export import _runbook_flow_drawing

    palette = {"ink": colors.HexColor("#183A5A"), "line": colors.HexColor("#4B657A")}
    drawing = _runbook_flow_drawing(
        Drawing, Rect, Line, Polygon, String, colors, palette
    )
    rectangles = [shape for shape in drawing.contents if isinstance(shape, Rect)]
    lines = [shape for shape in drawing.contents if isinstance(shape, Line)]
    heads = [shape for shape in drawing.contents if isinstance(shape, Polygon)]
    labels = [shape.text for shape in drawing.contents if isinstance(shape, String)][
        1::2
    ]
    assert len(rectangles) == len(lines) + 1 == len(heads) + 1 == 8
    assert all((rect.width, rect.height) == (100, 36) for rect in rectangles)
    assert labels == [
        "开始/目标与约束",
        "复验对象盘点",
        "三类事项分类",
        "可重跑验证",
        "历史证据只读核验",
        "独立真人 Study",
        "User Gate",
        "Accept/Revise/Stop",
    ]
    assert not any("…" in label for label in labels)


def test_generator_v3_runbook_intent_is_narrow_and_citations_remain_contained(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "# Heading\n\nHistorical evidence is retained.\n\n# Delivery\n\nVerification is offline.\n",
        encoding="utf-8",
    )
    runbook = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    for task in (
        "制定 v3 本地资料工作流后续迭代计划",
        "制定 provider 配置计划",
        "制定离线维护检查清单",
    ):
        draft = create_draft(
            root,
            ["notes.md"],
            task=task,
            artifact_kind="plan",
            generator_version="deterministic-grounded-plan-v3",
        )
        assert (
            "Verification Matrix"
            not in generate_result(root, draft, draft.contract_hash).preview_markdown
        )
    draft = create_draft(
        root,
        ["notes.md"],
        task=runbook,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v3",
    )
    result = generate_result(root, draft, draft.contract_hash)
    hit = next(hit for hit in result.retrieval.hits if hit.segment_ordinal > 1)
    citation = next(item for item in result.citations if item.id == hit.citation_id)
    tampered = result.model_copy(
        update={
            "citations": (citation.model_copy(update={"line_start": 1, "line_end": 1}),)
            + tuple(item for item in result.citations if item.id != citation.id)
        }
    )
    assert not workflow.verify_result_integrity(_rehash(tampered))


def test_trial_manifest_v4_keeps_all_entries_and_changes_only_lineage() -> None:
    root = Path(__file__).resolve().parents[2] / "examples" / "v3-phase-2"
    v3 = json.loads(
        (root / "projecttown-trial-manifest-v3.json").read_text(encoding="utf-8")
    )
    v4 = json.loads(
        (root / "projecttown-trial-manifest-v4.json").read_text(encoding="utf-8")
    )
    assert v4["entries"] == v3["entries"]
    assert v4["schema_version"] == "v3-phase-2-projecttown-trial-candidates-v4"
    assert v4["candidate_profile"] == "projecttown-human-pdf-v4"


def test_generator_v4_t002_runbook_is_strict_and_rendered_in_two_pages(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    from backend.app.pdf_export import _render_pdf_v4

    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "# Evidence\n\nLocal verification evidence remains readable.\n",
        encoding="utf-8",
    )
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v4",
    )
    result = generate_result(root, draft, draft.contract_hash)
    required = (
        "Category depends on evidence provenance, not file format.",
        "read/reopen/render; isolated regen only as comparison evidence",
        "BLOCK：",
        "VERIFIED：",
        "READY FOR USER GATE：",
        "Basis",
        "S003：Inherited unresolved item - not claimed as verified。",
        "source documents selected:",
        "cited excerpts:",
    )
    assert all(item in result.preview_markdown for item in required)
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v4")
    reader = PdfReader(BytesIO(pdf))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) == 2
    assert [
        extracted.index(label)
        for label in ("执行摘要", "复验对象与证据盘点", "Verification Matrix", "Basis")
    ] == sorted(
        extracted.index(label)
        for label in ("执行摘要", "复验对象与证据盘点", "Verification Matrix", "Basis")
    )
    with _error("UNSUPPORTED_PDF_EXPORT_VERSION"):
        render_pdf_export(root, result, export_version="v3-material-pdf-export-v3")
    malformed = result.model_copy(
        update={
            "preview_markdown": result.preview_markdown.replace(
                "| Basis |\n", "| BasisX |\n", 1
            )
        }
    )
    with pytest.raises(RuntimeError, match="INVALID_RUNBOOK_STRUCTURE"):
        _render_pdf_v4(malformed)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.replace("## PASS/FAIL 标准\n", "", 1),
        lambda value: value.replace(
            "## 角色与 User Gate\n",
            "## 角色与 User Gate\n\n## 角色与 User Gate\n",
            1,
        ),
        lambda value: value.replace("## 可重跑验证\n", "## 用户持有的发布事项\n", 1),
        lambda value: value.replace("| Object | Source |", "| ObjectX | Source |", 1),
        lambda value: value.replace("| M08 |", "| M08X |", 1).replace(
            "| M08X |", "", 1
        ),
        lambda value: value.replace("| Basis |\n", "| BasisX |\n", 1),
        lambda value: value.replace("Fixed task：", "Fixed taskX：", 1),
    ),
    ids=(
        "missing_heading",
        "duplicate_heading",
        "out_of_order_heading",
        "inventory_columns",
        "matrix_row_count",
        "matrix_basis",
        "required_contract_phrase",
    ),
)
def test_generator_v4_runbook_invalid_structure_fails_closed(
    tmp_path: Path, mutate: object
) -> None:
    from backend.app.pdf_export import _render_pdf_v4

    root = _root(tmp_path)
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v4",
    )
    result = generate_result(root, draft, draft.contract_hash)
    invalid = result.model_copy(
        update={"preview_markdown": mutate(result.preview_markdown)}  # type: ignore[operator]
    )
    with pytest.raises(RuntimeError, match="INVALID_RUNBOOK_STRUCTURE"):
        _render_pdf_v4(invalid)


def test_generator_v4_matrix_basis_covers_required_directions(tmp_path: Path) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v4",
    )
    result = generate_result(root, draft, draft.contract_hash)
    matrix = result.preview_markdown.split("## Verification Matrix\n", 1)[1].split(
        "\n\n## PASS/FAIL 标准", 1
    )[0]
    assert all(source in matrix for source in ("S001", "S002", "S004", "S006"))
    assert (
        "fresh-run JSON" in matrix
        and "historical manifest + engineering JSON" in matrix
    )
    assert "\u2014" not in result.preview_markdown
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v4")
    extracted = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )
    assert len(PdfReader(BytesIO(pdf)).pages) == 2
    assert all(
        source in extracted
        for source in ("S001", "S002", "S003", "S004", "S005", "S006", "S007")
    )
    assert "source documents selected:" in extracted
    assert "cited excerpts:" in extracted


def test_generator_v4_falls_back_for_non_runbook_and_manifest_v5_is_additive(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    draft = create_draft(
        root,
        ["notes.md"],
        task="制定离线维护检查清单",
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v4",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert "Verification Matrix" not in result.preview_markdown
    fixture_root = Path(__file__).resolve().parents[2] / "examples" / "v3-phase-2"
    v4 = json.loads(
        (fixture_root / "projecttown-trial-manifest-v4.json").read_text(
            encoding="utf-8"
        )
    )
    v5 = json.loads(
        (fixture_root / "projecttown-trial-manifest-v5.json").read_text(
            encoding="utf-8"
        )
    )
    assert v5["entries"] == v4["entries"]
    assert v5["schema_version"] == "v3-phase-2-projecttown-trial-candidates-v5"
    assert v5["candidate_profile"] == "projecttown-human-pdf-v5"


def test_generator_v5_t002_refines_runbook_without_changing_v4_contract(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    from backend.app.pdf_export import _RUNBOOK_V5_MATRIX_WIDTHS_MM

    root = _root(tmp_path)
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v5",
    )
    result = generate_result(root, draft, draft.contract_hash)
    required = (
        "Final Authority",
        "Matrix Owner",
        "verification row",
        "Initial State: BLOCK",
        "isolated comparison regen",
        "Category depends on evidence provenance/state, not file format.",
        "S002 + S006",
        "| M05 | review event | 历史只读 | read-only | existence/readability | record consistent | UNKNOWN/BLOCK | Independent Study Reviewer | S002 |",
        "RP = Reviewer-defined verification policy",
    )
    assert all(value in result.preview_markdown for value in required)
    assert (
        "| M06 | historical final snapshot | 历史只读 | read-only | hash/provenance | snapshot consistent | UNKNOWN/BLOCK | Verifier | S002 + S006 |"
        in result.preview_markdown
    )
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v5")
    reader = PdfReader(BytesIO(pdf))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    page_two = reader.pages[1].extract_text() or ""
    assert len(reader.pages) == 2
    assert "runbook v6" in extracted
    assert "runbook v5" not in extracted
    assert sum(_RUNBOOK_V5_MATRIX_WIDTHS_MM) == 172
    assert "UNKNOWN/BLOCK" in page_two and "S002 + S006" in page_two
    assert "UNKNOWN/BLOC\nK" not in page_two and "S002 +\nS006" not in page_two
    assert "source documents selected: 1; cited excerpts: 1" in page_two
    assert "provider/embedding/MCP calls=0" in page_two
    assert all(
        value in extracted
        for value in (
            "Final Authority",
            "Matrix Owner",
            "verification row",
            "Initial State: BLOCK",
            "Verification Matrix",
            "Independent Study Reviewer",
            "S002",
            "S006",
        )
    )
    with _error("UNSUPPORTED_PDF_EXPORT_VERSION"):
        render_pdf_export(root, result, export_version="v3-material-pdf-export-v4")
    fixture_root = Path(__file__).resolve().parents[2] / "examples" / "v3-phase-2"
    v5 = json.loads(
        (fixture_root / "projecttown-trial-manifest-v5.json").read_text(
            encoding="utf-8"
        )
    )
    v6 = json.loads(
        (fixture_root / "projecttown-trial-manifest-v6.json").read_text(
            encoding="utf-8"
        )
    )
    assert v6["entries"] == v5["entries"]
    assert v6["schema_version"] == "v3-phase-2-projecttown-trial-candidates-v6"
    assert v6["candidate_profile"] == "projecttown-human-pdf-v6"


def test_generator_v6_runbook_blocks_until_all_run_bindings_are_supplied(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v6",
    )
    result = generate_result(root, draft, draft.contract_hash)
    preview = result.preview_markdown
    assert "## Run Binding\n- status: INCOMPLETE/BLOCK" in preview
    binding_keys = (
        "candidate_path",
        "preview_path",
        "manifest_path",
        "historical_evidence_root",
        "fresh_root",
        "fresh_evidence_root",
        "test_command",
        "expected_page_count",
        "approved_hash_provenance_tuple_source",
        "study_evidence_path",
    )
    assert all(f"- {key}: <TO BIND BEFORE RUN>" in preview for key in binding_keys)
    assert (
        "| Matrix ID | Object | Source | Status | Category | Rerun | Modify | Final Authority |"
        in preview
    )
    assert (
        "| M02 | candidate PDF | original delivery | pending | 可重跑验证 | compare only | no | User |"
        in preview
    )
    assert (
        "| M02 | preview | original Result | pending | 可重跑验证 | yes | no | User |"
        in preview
    )
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v6")
    reader = PdfReader(BytesIO(pdf))
    extracted = [page.extract_text() or "" for page in reader.pages]
    assert len(reader.pages) == 2
    assert "Run Binding" in extracted[0]
    assert "status: INCOMPLETE/BLOCK" in extracted[0]
    assert "Independent Study 最小执行合同" in extracted[0]
    assert "Verification Matrix" in extracted[1]
    assert "ProjectTown｜离线确定性 runbook v7｜User Gate 独占" in "\n".join(extracted)
    assert "runbook v6" not in "\n".join(extracted)
    flow_nodes = (
        "Initial State: BLOCK",
        "盘点与分类",
        "可重跑 + 历史只读",
        "VERIFIED",
        "Independent Study",
        "READY FOR USER GATE",
        "User disposition",
        "ACCEPT / REVISE / STOP",
        "记录下一步",
    )
    flow_text = extracted[0].split("复验流程图", 1)[1].split("三类复验层级", 1)[0]
    assert [flow_text.index(node) for node in flow_nodes] == sorted(
        flow_text.index(node) for node in flow_nodes
    )


def test_generator_v6_runbook_binds_execution_and_separates_state_rows(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = _root(tmp_path)
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    values = {
        "run_binding_candidate_path": "D:/evidence/very-long-candidate-delivery-name.pdf",
        "run_binding_preview_path": "D:/evidence/very-long-preview-result-name.md",
        "run_binding_manifest_path": "D:/evidence/very-long-candidate-manifest-name.json",
        "run_binding_historical_evidence_root": "D:/history/very-long-historical-evidence-root",
        "run_binding_fresh_root": "D:/fresh/very-long-fresh-root",
        "run_binding_fresh_evidence_root": "D:/fresh/evidence/very-long-fresh-evidence-root",
        "run_binding_test_command": "pytest -q",
        "run_binding_expected_page_count": "2",
        "run_binding_approved_hash_provenance_tuple_source": "D:/approved.json",
        "run_binding_study_evidence_path": "D:/study/T002.json",
    }
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        constraints=values,
        generator_version="deterministic-grounded-plan-v6",
    )
    result = generate_result(root, draft, draft.contract_hash)
    preview = result.preview_markdown
    assert "## Run Binding\n- status: BOUND; Initial State: BLOCK" in preview
    assert all(
        f"- {key.removeprefix('run_binding_')}: {value}" in preview
        for key, value in values.items()
    )
    assert (
        "| ID | Row Type | Object | Category | Operation | Check | PASS | Failure | Owner | Basis |"
        in preview
    )
    assert all(
        f"| {ident} | Mandatory Verification |" in preview
        for ident in ("M01", "M02", "M03", "M04", "M05", "M06")
    )
    assert "| M07 | User Decision |" in preview
    assert "| M08 | Conditional Release Action |" in preview
    assert (
        "| M02 | Mandatory Verification | candidate PDF + preview | Rerun | read/reopen/render; isolated comparison regen | reopen/text/render | original candidate readable; compare matches | BLOCK; retain original | Verifier | RP |"
        in preview
    )
    assert "3. Independent Study：仅在 VERIFIED 后" in preview
    assert (
        "4. READY FOR USER GATE：仅当 VERIFIED 且 Independent Study 完成后" in preview
    )
    assert (
        "Apply/Publish 仅在 ACCEPT 后经 User 单独授权，未授权不构成 verification failure。"
        in preview
    )
    pdf = render_pdf_export(root, result, export_version="v3-material-pdf-export-v6")
    assert pdf == render_pdf_export(
        root, result, export_version="v3-material-pdf-export-v6"
    )
    reader = PdfReader(BytesIO(pdf))
    extracted = [page.extract_text() or "" for page in reader.pages]
    assert len(reader.pages) == 2
    assert all(value in extracted[0] for value in values.values())
    assert "status: BOUND; Initial State: BLOCK" in extracted[0]
    required_page_two = (
        "Mandatory",
        "Verification",
        "User Decision",
        "Conditional Release Action",
        "BLOCK; retain",
        "original",
        "S002 + S006",
        "RP",
        "VERIFIED：M01-M06 全部完成且满足 PASS",
        "READY FOR USER GATE：VERIFIED 且 Independent Study 完成",
        "ACCEPT：仅 User 明确接受。REVISE：形成新 candidate 并重新验证。",
        "STOP：User 停止、Discard 或不可恢复 blocker。",
        "Apply/Publish：ACCEPT 后可选，且需 User 单独授权；未授权不是 verification failure。",
    )
    missing = [value for value in required_page_two if value not in extracted[1]]
    assert not missing, missing
    page_two = extracted[1]
    assert not any(
        broken in page_two
        for broken in (
            "UNKNOWN/B\nLOCK",
            "Categor\ny",
            "re\nnder",
            "consis\ntency",
            "Apply/Publi\nsh",
        )
    )
    with _error("UNSUPPORTED_PDF_EXPORT_VERSION"):
        render_pdf_export(root, result, export_version="v3-material-pdf-export-v5")


def test_generator_v6_renderer_rejects_malformed_v7_structure(tmp_path: Path) -> None:
    root = _root(tmp_path)
    task = (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    )
    draft = create_draft(
        root,
        ["notes.md"],
        task=task,
        artifact_kind="plan",
        generator_version="deterministic-grounded-plan-v6",
    )
    result = generate_result(root, draft, draft.contract_hash)
    from backend.app.pdf_export import _render_pdf_v6

    for preview in (
        result.preview_markdown.replace("## Run Binding", "## Broken Binding", 1),
        result.preview_markdown.replace("| ID | Row Type |", "| ID |", 1),
    ):
        malformed = result.model_copy(update={"preview_markdown": preview})
        with pytest.raises(RuntimeError, match="INVALID_RUNBOOK_STRUCTURE"):
            _render_pdf_v6(malformed)


def test_plan_stages_and_actions_change_with_evidence(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "second.md").write_text(
        "# Heading\nRelease must include a signed checklist.\n", encoding="utf-8"
    )
    first = generate_result(
        root,
        create_draft(root, ["notes.md"], task="research fact", artifact_kind="plan"),
        create_draft(
            root, ["notes.md"], task="research fact", artifact_kind="plan"
        ).contract_hash,
    )
    second = generate_result(
        root,
        create_draft(
            root, ["second.md"], task="release checklist", artifact_kind="plan"
        ),
        create_draft(
            root, ["second.md"], task="release checklist", artifact_kind="plan"
        ).contract_hash,
    )
    assert first.preview_markdown != second.preview_markdown
    assert (
        "A fact" in first.preview_markdown
        and "signed checklist" in second.preview_markdown
    )
    assert "# Notes" not in first.preview_markdown
    first_actions = re.findall(r"- 行动：(.*)", first.preview_markdown)
    first_deliverables = re.findall(r"- 交付物：(.*)", first.preview_markdown)
    first_acceptance = re.findall(r"- 验收标准：(.*)", first.preview_markdown)
    assert len(first_actions) >= 4 and len(set(first_actions[:4])) == 4
    assert len(set(first_deliverables[:4])) == 4 and len(set(first_acceptance[:4])) == 4
    assert "supports the linked action" not in first.preview_markdown


def test_v2_markdown_blocks_produce_multiple_grounded_citations(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "roadmap.md").write_text(
        "# Context\nOffline workflow has a user-visible gap.\n\n"
        "# Candidate\nPDF preview must be readable without JSON.\n\n"
        "# Weak\nEngineering acceptance passed. Users have tasks.\n\n"
        "# Validation\nRun positive, negative, and recovery checks in fresh roots.\n\n"
        "# Study\nAt least 7/10 tasks and real user acceptance control whether Apply remains blocked.\n",
        encoding="utf-8",
    )
    draft = create_draft(
        root,
        ["roadmap.md"],
        task="local material workflow iteration plan",
        artifact_kind="plan",
    )
    result = generate_result(root, draft, draft.contract_hash)
    assert len(result.segments) >= 4
    assert len({item.segment_ordinal for item in result.retrieval.hits}) >= 2
    assert "JSON 保留为工程记录" in result.preview_markdown
    assert "两轮 fresh roots" in result.preview_markdown
    assert (
        "至少 7/10" in result.preview_markdown
        and "不进入 Apply" in result.preview_markdown
    )
    sections = result.preview_markdown.split("### 阶段 ")
    assert any("工程验证与恢复矩阵" in item and "验证" in item for item in sections)
    assert any("独立真人 Study 决策" in item and "7/10" in item for item in sections)
    reference_section = result.preview_markdown.split("## 引用\n", 1)[1]
    assert "低信息结构线索" not in reference_section
    used_previews = [
        item.preview for item in result.citations if item.id in reference_section
    ]
    assert not any("Engineering acceptance passed" in value for value in used_previews)
    assert any("positive, negative, and recovery" in value for value in used_previews)
    assert any("7/10" in value for value in used_previews)


def test_v2_quota_handles_repository_multifile_product_plan() -> None:
    from io import BytesIO

    from pypdf import PdfReader

    root = Path(__file__).parents[2].resolve()
    draft = create_draft(
        root,
        [
            "README.md",
            "docs/v3-phase-0.md",
            "docs/v3-phase-1.md",
            "docs/v3-product-direction.md",
        ],
        task="制定本地资料工作流后续迭代计划",
        artifact_kind="plan",
    )
    result = generate_result(root, draft, draft.contract_hash)
    preview = result.preview_markdown
    stages = preview.split("### 阶段 ")
    assert any("工程验证与恢复矩阵" in stage and "正向" in stage for stage in stages)
    assert any(
        "独立真人 Study 决策" in stage
        and "两轮范围受限收官" in stage
        and "Phase 3A preflight" in stage
        and "不进入 Apply" in stage
        for stage in stages
    )
    assert "7/10" not in preview
    stage_two = next(stage for stage in stages if "改善用户可读候选成果" in stage)
    assert any(word in stage_two for word in ("预览", "导出", "下载", "preview", "PDF"))
    pdf = render_pdf_export(root, result)
    reader = PdfReader(BytesIO(pdf))
    page_text = [page.extract_text() or "" for page in reader.pages]
    assert len(page_text) == 2 and all(len(page.strip()) >= 100 for page in page_text)
    assert sum("离线边界" in page for page in page_text) == 1


def test_legacy_v1_frozen_generator_remains_independently_verifiable(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    current = create_draft(root, ["notes.md"], task="legacy", artifact_kind="plan")
    data = current.model_dump()
    data["future_parameters"]["generator_version"] = (
        "deterministic-grounded-template-v1"
    )
    data["future_parameters"]["retrieval_version"] = "segmented-deterministic-rag-v1"
    data["future_parameters"]["segmentation_version"] = "utf8-raw-240k-v1"
    data["contract_hash"] = workflow._hash(
        "projecttown/v3/material-contract/v1", workflow._contract_payload(data)
    )
    data["session_hash"] = workflow._hash(
        "projecttown/v3/material-session/v1", workflow._session_payload(data)
    )
    legacy = workflow.DraftSession.model_validate(data)
    result = generate_result(root, legacy, legacy.contract_hash)
    assert "Deterministic offline plan" in result.preview_markdown
    assert workflow.verify_result_integrity(result)


def test_unknown_or_crossed_frozen_version_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    draft = create_draft(root, ["notes.md"], task="x", artifact_kind="plan")
    data = draft.model_dump()
    data["future_parameters"]["retrieval_version"] = "segmented-deterministic-rag-v1"
    data["contract_hash"] = workflow._hash(
        "projecttown/v3/material-contract/v1", workflow._contract_payload(data)
    )
    data["session_hash"] = workflow._hash(
        "projecttown/v3/material-session/v1", workflow._session_payload(data)
    )
    crossed = workflow.DraftSession.model_validate(data)
    with _error("UNSUPPORTED_FROZEN_VERSION"):
        generate_result(root, crossed, crossed.contract_hash)


def test_generation_conflict_blocks_export_and_stale_source_fails(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "constraint: mode=one\nconstraint: mode=two\n", encoding="utf-8"
    )
    draft = create_draft(root, ["notes.md"], task="x", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    assert result.state == "needs_user_decision"
    with _error("UNRESOLVED_CONFLICT"):
        render_export(root, result)
    (root / "notes.md").write_text("changed\n", encoding="utf-8")
    # A frozen preview is independently recoverable after its source root changes.
    assert render_preview(result) == result.preview_markdown


def test_conflict_citations_fullwidth_and_no_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "约束： mode＝one\n要求: mode=two\n", encoding="utf-8"
    )
    draft = create_draft(root, ["notes.md"], task="mode", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    assert result.state == "needs_user_decision"
    assert parse_session_bytes(serialize_session(result)) == result
    conflict = result.conflicts[0]
    assert conflict.values == ("one", "two")
    assert all(
        next(item for item in result.citations if item.id == identifier).method
        == "conflict"
        for identifier in conflict.citation_ids
    )
    monkeypatch.setattr(
        workflow,
        "generate_result",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        workflow,
        "build_index",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        workflow, "search", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError())
    )
    assert render_preview(result) == result.preview_markdown
    assert workflow.revalidate_result_sources(root, result)
    with _error("UNRESOLVED_CONFLICT"):
        render_export(root, result)


def test_result_semantic_tamper_rejected_even_when_rehashed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    draft = create_draft(root, ["notes.md"], task="Notes", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    tampered = result.model_copy(
        update={"coverage": result.coverage.model_copy(update={"total_sources": 2})}
    )
    data = tampered.model_dump(mode="json")
    tampered = tampered.model_copy(
        update={
            "session_hash": workflow._result_hash(
                {key: value for key, value in data.items() if key != "session_hash"}
            )
        }
    )
    assert not workflow.verify_result_integrity(tampered)
    with _error("INVALID_SESSION_HASH"):
        serialize_session(tampered)


def _rehash(result: workflow.ResultSession) -> workflow.ResultSession:
    data = result.model_dump(mode="json")
    return result.model_copy(
        update={
            "session_hash": workflow._result_hash(
                {key: value for key, value in data.items() if key != "session_hash"}
            )
        }
    )


def test_nul_is_structural_material_not_rag_validation_leak(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "nul.txt").write_bytes(b"\n\x00offline evidence\n")
    draft = create_draft(root, ["nul.txt"], task="offline", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    assert not result.segments[0].retrievable
    assert result.citations[0].method == "structural"
    assert result.citations[0].line_start == 2


@pytest.mark.parametrize("kind", ["plan", "report", "readme"])
def test_artifacts_are_distinct_grounded_and_inert(tmp_path: Path, kind: str) -> None:
    root = _root(tmp_path)
    draft = create_draft(
        root,
        ["notes.md", "data.txt"],
        task="map evidence ``` D:/not-visible",
        artifact_kind=kind,
        readme_target="notes.md" if kind == "readme" else None,
        constraints={"scope": "local"},
    )
    result = generate_result(root, draft, draft.contract_hash)
    artifact = result.artifact_markdown
    assert "Deterministic offline" in artifact
    assert "provider/embedding/MCP calls=0" in artifact
    assert "[S" in artifact and "scope" in artifact
    assert "Review the cited material" not in artifact
    assert "Document the confirmed task" not in artifact
    assert "inventory above" not in artifact
    assert str(root) not in artifact
    assert "``` D:/not-visible" not in artifact


def test_rehashed_retrieval_and_conflict_tampering_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "constraint: mode=one\nconstraint: mode=two\nnotes evidence\n",
        encoding="utf-8",
    )
    draft = create_draft(root, ["notes.md"], task="notes", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    hit = result.retrieval.hits[0]
    duplicate = result.model_copy(
        update={"retrieval": result.retrieval.model_copy(update={"hits": (hit, hit)})}
    )
    assert not workflow.verify_result_integrity(_rehash(duplicate))
    conflict = result.conflicts[0]
    altered = result.model_copy(
        update={"conflicts": (conflict.model_copy(update={"values": ("evil", "two")}),)}
    )
    assert not workflow.verify_result_integrity(_rehash(altered))
    wrong_evidence = result.model_copy(
        update={
            "conflicts": (
                conflict.model_copy(
                    update={"citation_ids": (conflict.citation_ids[0],)}
                ),
            )
        }
    )
    assert not workflow.verify_result_integrity(_rehash(wrong_evidence))
    citation = result.citations[0]
    altered_hash = result.model_copy(
        update={
            "citations": (citation.model_copy(update={"raw_sha256": "0" * 64}),)
            + result.citations[1:]
        }
    )
    assert not workflow.verify_result_integrity(_rehash(altered_hash))
    with pytest.raises(workflow.ValidationError):
        workflow.ResultRetrieval.model_validate(
            {
                **result.retrieval.model_dump(),
                "hits": [item.model_dump() for item in result.retrieval.hits * 9],
            }
        )


def test_rehashed_forged_artifact_and_preview_are_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    draft = create_draft(root, ["notes.md"], task="Notes", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    forged_artifact = "# Deterministic offline forged\n"
    forged = result.model_copy(
        update={
            "artifact_markdown": forged_artifact,
            "preview_markdown": forged_artifact,
            "artifact_hash": workflow.hashlib.sha256(
                forged_artifact.encode("utf-8")
            ).hexdigest(),
            "preview_hash": workflow.hashlib.sha256(
                forged_artifact.encode("utf-8")
            ).hexdigest(),
        }
    )
    assert not workflow.verify_result_integrity(_rehash(forged))


def test_rehashed_segment_line_overflow_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    draft = create_draft(root, ["notes.md"], task="!!!", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    segment = result.segments[0].model_copy(
        update={"line_end": result.draft.material_manifest.entries[0].line_count + 1}
    )

    assert not workflow.verify_result_integrity(
        _rehash(result.model_copy(update={"segments": (segment,)}))
    )


def test_readme_and_source_markdown_are_inert(tmp_path: Path) -> None:
    root = tmp_path / "materials"
    root.mkdir()
    filename = "[hostile](link).md"
    (root / filename).write_text(
        "![image](x) <script>x</script> ``` D:/source\u202e\n",
        encoding="utf-8",
    )
    draft = create_draft(
        root,
        [filename],
        task="task",
        artifact_kind="readme",
        readme_target=filename,
        constraints={"x```\u202e": "value\n``` D:/private\x01"},
    )
    result = generate_result(root, draft, draft.contract_hash)
    artifact = result.artifact_markdown
    assert "``` D:/private" not in artifact
    assert "D:/private" not in artifact
    assert "\u202e" not in artifact and "\x01" not in artifact
    assert "ˋˋˋ" in artifact
    assert "`![image](x)" in artifact


def test_report_group_path_is_inert(tmp_path: Path) -> None:
    root = tmp_path / "materials"
    root.mkdir()
    filename = "hostile`![link](target).txt"
    (root / filename).write_text("grounded evidence\n", encoding="utf-8")
    draft = create_draft(
        root,
        [filename],
        task="grounded",
        artifact_kind="report",
    )

    artifact = generate_result(root, draft, draft.contract_hash).artifact_markdown

    assert f"### `{filename}`" not in artifact
    assert "### `hostileˋ![link](target).txt`" in artifact


def test_hostile_conflict_values_remain_a_decision_request(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "notes.md").write_text(
        "constraint: mode=one`x\nconstraint: mode=D:/secret\n", encoding="utf-8"
    )
    draft = create_draft(root, ["notes.md"], task="mode", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    assert result.state == "needs_user_decision"
    assert "D:/secret" not in result.artifact_markdown
    assert all(
        "ˋ" in value or "[local-path]" in value
        for value in result.conflicts[0].display_values
    )
    for identifier in result.conflicts[0].citation_ids:
        assert f"[{identifier}]" in result.artifact_markdown


def test_publish_staged_verification_precedes_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    monkeypatch.setattr(
        workflow.os,
        "read",
        lambda *_args: (_ for _ in ()).throw(OSError("staged read failure")),
    )
    with _error("OUTPUT_PUBLISH_FAILED"):
        publish_new_file(root, target, b"complete")
    assert not target.exists()


def test_post_link_durability_failure_is_not_reported_as_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    original_open = workflow.os.open

    def fail_directory_open(*args: object, **kwargs: object) -> int:
        if args[0] == output:
            raise OSError("durability unavailable")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(workflow.os, "open", fail_directory_open)
    publish_new_file(root, target, b"complete")
    assert target.read_bytes() == b"complete"


def test_publication_normal_success_is_immediately_single_link_loadable(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "draft.json"
    draft = create_draft(root, ["notes.md"], task="x", artifact_kind="plan")
    publish_new_file(root, target, serialize_session(draft))
    assert target.lstat().st_nlink == 1
    assert load_external_session(target) == draft


def test_temp_unlink_failure_rolls_back_without_content_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    original_unlink = Path.unlink

    def fail_temp(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith(".projecttown-"):
            raise OSError("temp unlink blocked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temp)
    with pytest.raises(workflow.PublicationRollbackError) as error:
        publish_new_file(root, target, b"complete")
    assert error.value.code == "PUBLICATION_ROLLED_BACK"
    assert not target.exists()
    assert all(item.stat().st_size == 0 for item in output.iterdir())


def test_temp_and_final_unlink_failure_requires_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    original_unlink = Path.unlink

    def fail_committed(path: Path, *args: object, **kwargs: object) -> None:
        if path == target or path.name.startswith(".projecttown-"):
            raise OSError("unlink blocked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_committed)
    with pytest.raises(workflow.PublicationAttentionError) as error:
        publish_new_file(root, target, b"complete")
    assert error.value.code == "COMMITTED_NEEDS_ATTENTION"
    assert target.read_bytes() == b"complete"
    assert target.lstat().st_nlink == 2


def test_preexisting_temp_from_failed_o_excl_is_never_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    temporary = output / ".projecttown-fixed.tmp"
    temporary.write_bytes(b"pre-existing")
    monkeypatch.setattr(workflow.secrets, "token_hex", lambda _size: "fixed")
    with _error("OUTPUT_PUBLISH_FAILED"):
        publish_new_file(root, output / "result.json", b"complete")
    assert temporary.read_bytes() == b"pre-existing"


def test_scrub_refuses_path_replacement_without_touching_replacement(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "owned.tmp"
    owned.write_bytes(b"owned")
    identity = owned.lstat()
    owned.unlink()
    owned.write_bytes(b"replacement")
    assert not workflow._scrub_then_remove_temporary(owned, identity)
    assert owned.read_bytes() == b"replacement"


def test_post_link_final_lstat_failure_requires_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    original_lstat = Path.lstat

    def fail_final_lstat(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        if path == target and path.exists():
            raise OSError("final metadata unavailable")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail_final_lstat)
    with pytest.raises(workflow.PublicationAttentionError) as error:
        publish_new_file(root, target, b"complete")
    assert error.value.code == "COMMITTED_NEEDS_ATTENTION"
    assert target.read_bytes() == b"complete"


def test_scrub_rechecks_identity_after_close_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / "owned.tmp"
    owned.write_bytes(b"owned")
    identity = owned.lstat()
    original_close = workflow.os.close

    def replace_after_close(descriptor: int) -> None:
        original_close(descriptor)
        if owned.exists():
            owned.unlink()
            owned.write_bytes(b"replacement")

    monkeypatch.setattr(workflow.os, "close", replace_after_close)
    assert not workflow._scrub_then_remove_temporary(owned, identity)
    assert owned.read_bytes() == b"replacement"


def test_post_link_temp_replacement_requires_attention_without_unlinking_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"
    original_link = workflow.os.link

    def replace_temp_after_link(source: Path, destination: Path) -> None:
        original_link(source, destination)
        source.unlink()
        source.write_bytes(b"replacement")

    monkeypatch.setattr(workflow.os, "link", replace_temp_after_link)
    with pytest.raises(workflow.PublicationAttentionError):
        publish_new_file(root, target, b"complete")
    temporary = next(output.glob(".projecttown-*.tmp"))
    assert temporary.read_bytes() == b"replacement"
    assert target.read_bytes() == b"complete"


def test_stable_read_then_final_replacement_requires_attention_without_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    target = output / "result.json"

    def replace_after_stable_read(path: Path, *_args: object, **_kwargs: object):
        if path == target:
            path.unlink()
            path.write_bytes(b"replacement")

    monkeypatch.setattr(workflow, "read_stable_regular_file", replace_after_stable_read)
    with pytest.raises(workflow.PublicationAttentionError):
        publish_new_file(root, target, b"complete")
    assert target.read_bytes() == b"replacement"


@pytest.mark.parametrize("count", [33, 100])
def test_many_sources_full_lifecycle(tmp_path: Path, count: int) -> None:
    root = tmp_path / "many"
    root.mkdir()
    names = []
    for index in range(count):
        name = f"f{index:03d}.txt"
        (root / name).write_text(f"evidence {index}\n", encoding="utf-8")
        names.append(name)
    draft = create_draft(root, names, task="evidence", artifact_kind="plan")
    result = generate_result(root, draft, draft.contract_hash)
    assert workflow.verify_result_integrity(result)
    assert parse_session_bytes(serialize_session(result)) == result


def test_exact_ten_mebibyte_lifecycle_with_unretrievable_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ten-mebibytes"
    root.mkdir()
    names = []
    for index in range(10):
        name = f"large{index}.txt"
        (root / name).write_bytes((b"." * (1_048_575)) + b"\n")
        names.append(name)
    draft = create_draft(root, names, task="!!!", artifact_kind="report")
    result = generate_result(root, draft, draft.contract_hash)
    assert result.coverage.total_sources == 10
    assert result.coverage.indexed_segments == 0
    assert parse_session_bytes(serialize_session(result)) == result
