"""Deterministic, offline PDF rendering for frozen v3 material results."""

from __future__ import annotations

import re
from html import escape
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

PDF_EXPORT_VERSION = "v3-material-pdf-export-v1"
PDF_RENDERER_VERSION = "projecttown-reportlab-pdf-v1"
PDF_EXPORT_VERSION_V2 = "v3-material-pdf-export-v2"
PDF_RENDERER_VERSION_V2 = "projecttown-reportlab-pdf-v2"
PDF_EXPORT_VERSION_V3 = "v3-material-pdf-export-v3"
PDF_RENDERER_VERSION_V3 = "projecttown-reportlab-pdf-v3"
PDF_EXPORT_VERSION_V4 = "v3-material-pdf-export-v4"
PDF_RENDERER_VERSION_V4 = "projecttown-reportlab-pdf-v4"
PDF_EXPORT_VERSION_V5 = "v3-material-pdf-export-v5"
PDF_RENDERER_VERSION_V5 = "projecttown-reportlab-pdf-v5"
PDF_EXPORT_VERSION_V6 = "v3-material-pdf-export-v6"
PDF_RENDERER_VERSION_V6 = "projecttown-reportlab-pdf-v6"
PDF_EXPORT_VERSION_V7 = "v3-material-pdf-export-v7"
PDF_RENDERER_VERSION_V7 = "projecttown-reportlab-pdf-v7"
PDF_EXPORT_VERSION_V8 = "v3-material-pdf-export-v8"
PDF_RENDERER_VERSION_V8 = "projecttown-reportlab-pdf-v8"
PDF_EXPORT_VERSION_V9 = "v3-material-pdf-export-v9"
PDF_RENDERER_VERSION_V9 = "projecttown-reportlab-pdf-v9"
_SUPPORTED_EXPORTS = {
    PDF_EXPORT_VERSION: PDF_RENDERER_VERSION,
    PDF_EXPORT_VERSION_V2: PDF_RENDERER_VERSION_V2,
    PDF_EXPORT_VERSION_V3: PDF_RENDERER_VERSION_V3,
    PDF_EXPORT_VERSION_V4: PDF_RENDERER_VERSION_V4,
    PDF_EXPORT_VERSION_V5: PDF_RENDERER_VERSION_V5,
    PDF_EXPORT_VERSION_V6: PDF_RENDERER_VERSION_V6,
    PDF_EXPORT_VERSION_V7: PDF_RENDERER_VERSION_V7,
    PDF_EXPORT_VERSION_V8: PDF_RENDERER_VERSION_V8,
    PDF_EXPORT_VERSION_V9: PDF_RENDERER_VERSION_V9,
}
_RUNBOOK_HEADINGS = (
    "执行摘要",
    "复验对象与证据盘点",
    "可重跑验证",
    "不可重跑历史证据",
    "用户持有的发布事项",
    "复验流程",
    "Verification Matrix",
    "PASS/FAIL 标准",
    "角色与 User Gate",
    "引用",
    "离线边界",
)
_RUNBOOK_V4_HEADINGS = (
    "执行摘要",
    "复验对象与证据盘点",
    "可重跑验证",
    "不可重跑历史证据",
    "用户持有的发布事项",
    "复验流程",
    "Verification Matrix",
    "PASS/FAIL 标准",
    "角色与 User Gate",
    "Independent Study 最小执行合同",
    "引用处置",
    "引用",
    "离线边界",
)
_RUNBOOK_V5_HEADINGS = _RUNBOOK_V4_HEADINGS
_RUNBOOK_V5_MATRIX_WIDTHS_MM = (8, 17, 15, 24, 17, 16, 24, 31, 20)
_RUNBOOK_V6_HEADINGS = (
    "执行摘要",
    "Run Binding",
    "复验对象与证据盘点",
    "三分类",
    "可重跑验证",
    "不可重跑历史证据",
    "用户持有的发布事项",
    "复验流程",
    "Verification Matrix",
    "状态合同",
    "PASS/FAIL 标准",
    "角色与 User Gate",
    "Independent Study 最小执行合同",
    "引用处置",
    "引用",
    "离线边界",
)
_RUNBOOK_V6_MATRIX_WIDTHS_MM = (7, 18, 16, 12, 24, 20, 14, 22, 23, 16)

if TYPE_CHECKING:
    from .material_workflow import ResultSession


def _font_path() -> Path:
    return Path(__file__).resolve().parents[2] / (
        "godot/assets/fonts/fusion-pixel-12px-proportional-zh-hans.ttf"
    )


def render_pdf(
    result: ResultSession, export_version: str = PDF_EXPORT_VERSION
) -> bytes:
    """Render a frozen result with an explicitly versioned presentation."""
    if export_version not in _SUPPORTED_EXPORTS:
        raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    version_tuple = (
        result.draft.future_parameters.generator_version,
        result.draft.future_parameters.retrieval_version,
        result.draft.future_parameters.segmentation_version,
    )
    if version_tuple[0] == "deterministic-grounded-plan-v8":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v8",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V8
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif version_tuple[0] == "deterministic-grounded-plan-v9":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v9",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V9
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif version_tuple[0] == "deterministic-grounded-plan-v7":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v7",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V7
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif version_tuple[0] == "deterministic-grounded-plan-v6":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v6",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V6
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif version_tuple[0] == "deterministic-grounded-plan-v5":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v5",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V5
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif version_tuple[0] == "deterministic-grounded-plan-v4":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v4",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V4
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif version_tuple[0] == "deterministic-grounded-plan-v3":
        if (
            version_tuple
            != (
                "deterministic-grounded-plan-v3",
                "segmented-deterministic-rag-v2",
                "markdown-block-v2",
            )
            or export_version != PDF_EXPORT_VERSION_V3
        ):
            raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    elif export_version in {
        PDF_EXPORT_VERSION_V3,
        PDF_EXPORT_VERSION_V4,
        PDF_EXPORT_VERSION_V5,
        PDF_EXPORT_VERSION_V6,
        PDF_EXPORT_VERSION_V7,
        PDF_EXPORT_VERSION_V8,
        PDF_EXPORT_VERSION_V9,
    }:
        raise RuntimeError("UNSUPPORTED_PDF_EXPORT_VERSION")
    if export_version == PDF_EXPORT_VERSION:
        return _render_pdf_v1(result)
    if export_version == PDF_EXPORT_VERSION_V3:
        return _render_pdf_v3(result)
    if export_version == PDF_EXPORT_VERSION_V4:
        return _render_pdf_v4(result)
    if export_version == PDF_EXPORT_VERSION_V5:
        return _render_pdf_v5(result)
    if export_version == PDF_EXPORT_VERSION_V6:
        return _render_pdf_v6(result)
    if export_version == PDF_EXPORT_VERSION_V7:
        return _render_pdf_v7(result)
    if export_version == PDF_EXPORT_VERSION_V8:
        return _render_pdf_v8(result)
    if export_version == PDF_EXPORT_VERSION_V9:
        return _render_pdf_v9(result)
    return _render_pdf_v2(result)


def _render_pdf_v1(result: ResultSession) -> bytes:
    """Render any frozen artifact kind without reading its source root."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    try:
        pdfmetrics.registerFont(TTFont("ProjectTownFusion", str(font_path)))
    except Exception as error:
        raise RuntimeError("PDF_FONT_UNAVAILABLE") from error
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "PTBody",
        parent=styles["BodyText"],
        fontName="ProjectTownFusion",
        fontSize=9.5,
        leading=13.5,
        spaceAfter=3,
    )
    title = ParagraphStyle(
        "PTTitle",
        parent=styles["Title"],
        fontName="ProjectTownFusion",
        fontSize=21,
        leading=28,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#183A5A"),
    )
    heading = ParagraphStyle(
        "PTHeading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusion",
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#183A5A"),
        spaceBefore=6,
        spaceAfter=3,
    )
    subheading = ParagraphStyle(
        "PTSubheading",
        parent=body,
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#315B7D"),
    )
    small = ParagraphStyle("PTSmall", parent=body, fontSize=8.8, leading=12)
    lines = result.preview_markdown.splitlines()
    document_title = next(
        (_clean(line[2:]) for line in lines if line.startswith("# ")),
        "ProjectTown 离线成果",
    )
    story = [Paragraph(document_title, title), Spacer(1, 10)]
    if result.draft.artifact_kind == "plan":
        story.extend(
            [
                Paragraph("计划总结（执行摘要）", heading),
                Paragraph(_clean(_summary(lines)), body),
                Paragraph("主要执行顺序", heading),
                Paragraph(
                    "确认目标与约束 → 核对实质证据 → 范围与方案 → 实施与里程碑 → 验收并决定下一轮",
                    subheading,
                ),
                Paragraph(
                    "起点：确认目标与约束。终点：记录验收结论与下一轮决定。", small
                ),
            ]
        )
    skip_plan_summary = False
    for line in lines:
        if not line.strip() or line.startswith(("```", "# ")):
            continue
        if result.draft.artifact_kind == "plan" and line == "## 计划总结":
            skip_plan_summary = True
            continue
        if result.draft.artifact_kind == "plan" and line == "## 主要执行流程":
            skip_plan_summary = False
            continue
        if skip_plan_summary:
            continue
        if line.startswith("## "):
            story.append(Paragraph(_clean(line[3:]), heading))
        elif line.startswith("### "):
            story.append(Paragraph(_clean(line[4:]), subheading))
        elif line.startswith("- "):
            story.append(Paragraph("• " + _clean(line[2:]), body))
        elif (
            not line.startswith("[")
            and not line.startswith("起点")
            and not line.startswith("      ")
        ):
            story.append(Paragraph(_clean(line), body))
    if not any(line == "## 离线边界" for line in lines):
        story.append(Spacer(1, 5))
        story.append(
            Paragraph(
                "离线边界：本 PDF 是冻结会话的确定性派生成果；provider/embedding/MCP calls=0，不构成真实模型结论。",
                small,
            )
        )
    stream = BytesIO()
    try:
        SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title="ProjectTown artifact",
            author="ProjectTown",
            invariant=1,
        ).build(story, onFirstPage=_footer, onLaterPages=_footer)
    except Exception as error:
        raise RuntimeError("PDF_RENDER_FAILED") from error
    return stream.getvalue()


def _render_pdf_v2(result: ResultSession) -> bytes:
    """Render the additive visual presentation; v1 remains byte-for-byte frozen."""
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    try:
        pdfmetrics.registerFont(TTFont("ProjectTownFusionV2", str(font_path)))
    except Exception as error:
        raise RuntimeError("PDF_FONT_UNAVAILABLE") from error

    styles = getSampleStyleSheet()
    palette = {
        "ink": colors.HexColor("#183A5A"),
        "muted": colors.HexColor("#4B657A"),
        "p0": colors.HexColor("#B23A48"),
        "p1": colors.HexColor("#D67A1F"),
        "p2": colors.HexColor("#277DA1"),
        "panel": colors.HexColor("#F2F6F8"),
        "risk": colors.HexColor("#FDECEC"),
    }
    body = ParagraphStyle(
        "PTV2Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV2",
        fontSize=9.3,
        leading=14,
        spaceAfter=4,
    )
    small = ParagraphStyle("PTV2Small", parent=body, fontSize=8.3, leading=11)
    title = ParagraphStyle(
        "PTV2Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV2",
        fontSize=21,
        leading=27,
        alignment=TA_CENTER,
        textColor=palette["ink"],
    )
    heading = ParagraphStyle(
        "PTV2Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV2",
        fontSize=14,
        leading=18,
        textColor=palette["ink"],
        spaceBefore=8,
        spaceAfter=5,
    )
    label = ParagraphStyle(
        "PTV2Label", parent=body, fontSize=9.2, leading=13, textColor=palette["ink"]
    )
    lines = result.preview_markdown.splitlines()
    document_title = next(
        (_plain(line[2:]) for line in lines if line.startswith("# ")),
        "ProjectTown 离线成果",
    )
    story = [Paragraph(escape(document_title), title), Spacer(1, 7)]

    if result.draft.artifact_kind == "plan":
        summary = _plain(_summary(lines))
        story.extend(
            [
                _info_box(
                    Paragraph("<b>执行摘要</b><br/>" + _emphasize(summary), body),
                    palette["panel"],
                    palette["ink"],
                ),
                Spacer(1, 7),
                Paragraph("主要执行流程", heading),
                _flow_drawing(
                    stages := _plan_stages(lines),
                    Drawing,
                    Rect,
                    Line,
                    Polygon,
                    String,
                    colors,
                    palette,
                ),
                Spacer(1, 5),
                Paragraph(
                    "从 <b>目标与约束</b> 出发，依次形成方案、实施并以验收结论决定下一轮。",
                    body,
                ),
                Paragraph("阶段计划与里程碑", heading),
            ]
        )
        for index, stage in enumerate(stages):
            priority = stage.get("优先级", ("P0", "P1", "P2")[min(index, 2)])
            primary_priority = stage.get("主优先级", priority)
            color = palette[primary_priority.lower()]
            story.append(
                _stage_card(
                    stage,
                    index + 1,
                    priority,
                    primary_priority,
                    color,
                    body,
                    label,
                    Paragraph,
                    KeepTogether,
                    Table,
                    TableStyle,
                    colors,
                )
            )
            story.append(Spacer(1, 5))
        _append_plan_callouts(story, lines, heading, body, palette, Paragraph, Spacer)
    else:
        _append_markdown(story, lines, heading, body, label, Paragraph)

    citations = _section_bullets(lines, "引用")
    if citations:
        story.append(Paragraph("引用与对应关系", heading))
        for citation in citations:
            story.append(
                Paragraph(
                    "<b>引用</b>｜" + _emphasize(_compact_citation(citation)), small
                )
            )
    if not any(line == "## 离线边界" for line in lines):
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                "<b>离线边界：</b>本 PDF 为冻结会话的确定性派生成果；provider/embedding/MCP calls=0，不构成真实模型结论。",
                small,
            )
        )

    stream = BytesIO()
    try:
        SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=13 * mm,
            bottomMargin=15 * mm,
            title="ProjectTown artifact",
            author="ProjectTown",
            invariant=1,
        ).build(story, onFirstPage=_footer_v2, onLaterPages=_footer_v2)
    except Exception as error:
        raise RuntimeError("PDF_RENDER_FAILED") from error
    return stream.getvalue()


def _runbook_sections(lines: list[str]) -> dict[str, list[str]]:
    headings = [line[3:] for line in lines if line.startswith("## ")]
    if any(headings.count(heading) != 1 for heading in _RUNBOOK_HEADINGS):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _runbook_flow_drawing(  # type: ignore[no-untyped-def]
    Drawing, Rect, Line, Polygon, String, colors, palette
):
    """Render an ordered vector runbook flow; labels and numbers survive grayscale."""
    drawing = Drawing(480, 156)
    nodes = (
        ("1", "开始/目标与约束", 0, 112),
        ("2", "复验对象盘点", 120, 112),
        ("3", "三类事项分类", 240, 112),
        ("4", "可重跑验证", 360, 112),
        ("5", "历史证据只读核验", 360, 38),
        ("6", "独立真人 Study", 240, 38),
        ("7", "User Gate", 120, 38),
        ("8", "Accept/Revise/Stop", 0, 38),
    )
    width, height = 100, 36
    for number, label, x, y in nodes:
        drawing.add(
            Rect(
                x,
                y,
                width,
                height,
                rx=5,
                ry=5,
                fillColor=colors.white,
                strokeColor=palette["line"],
                strokeWidth=1.2,
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 23,
                number,
                textAnchor="middle",
                fontName="ProjectTownFusionV3",
                fontSize=7,
                fillColor=palette["ink"],
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 10,
                label,
                textAnchor="middle",
                fontName="ProjectTownFusionV3",
                fontSize=7,
                fillColor=palette["ink"],
            )
        )
    for before, after in pairwise(nodes):
        _n, _label, x, y = before
        _next_n, _next_label, next_x, next_y = after
        if y == next_y and next_x > x:
            drawing.add(
                Line(
                    x + width,
                    y + height / 2,
                    next_x - 8,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        next_x - 8,
                        next_y + height / 2,
                        next_x - 13,
                        next_y + height / 2 + 4,
                        next_x - 13,
                        next_y + height / 2 - 4,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        elif y == next_y:
            drawing.add(
                Line(
                    x,
                    y + height / 2,
                    next_x + width + 8,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        next_x + width + 8,
                        next_y + height / 2,
                        next_x + width + 13,
                        next_y + height / 2 + 4,
                        next_x + width + 13,
                        next_y + height / 2 - 4,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        else:
            center = x + width / 2
            drawing.add(
                Line(
                    center,
                    y,
                    center,
                    next_y + height + 8,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        center,
                        next_y + height + 8,
                        center - 4,
                        next_y + height + 13,
                        center + 4,
                        next_y + height + 13,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
    return drawing


def _render_pdf_v3(result: ResultSession) -> bytes:
    """Render the structured delivery-verification runbook without reclassifying it."""
    from .material_workflow import _is_delivery_verification_runbook

    lines = result.preview_markdown.splitlines()
    if not _is_delivery_verification_runbook(result.draft):
        return _render_pdf_v2(result)
    sections = _runbook_sections(lines)
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    try:
        pdfmetrics.registerFont(TTFont("ProjectTownFusionV3", str(font_path)))
    except Exception as error:
        raise RuntimeError("PDF_FONT_UNAVAILABLE") from error
    styles = getSampleStyleSheet()
    palette = {
        "ink": colors.HexColor("#183A5A"),
        "panel": colors.HexColor("#F2F6F8"),
        "warn": colors.HexColor("#FDECEC"),
        "line": colors.HexColor("#4B657A"),
    }
    body = ParagraphStyle(
        "PTV3Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV3",
        fontSize=8.5,
        leading=11,
    )
    heading = ParagraphStyle(
        "PTV3Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV3",
        fontSize=12,
        leading=15,
        textColor=palette["ink"],
        spaceBefore=6,
        spaceAfter=3,
    )
    title = ParagraphStyle(
        "PTV3Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV3",
        fontSize=18,
        leading=23,
        alignment=TA_CENTER,
        textColor=palette["ink"],
    )
    small = ParagraphStyle("PTV3Small", parent=body, fontSize=7.3, leading=9)
    document_title = next(
        (_plain(line[2:]) for line in lines if line.startswith("# ")),
        "ProjectTown runbook",
    )
    story = [Paragraph(escape(document_title), title), Spacer(1, 6)]
    summary = " ".join(_plain(line) for line in sections["执行摘要"] if line.strip())
    story.append(
        _info_box(
            Paragraph("<b>执行摘要</b><br/>" + _emphasize(summary), body),
            palette["panel"],
            palette["ink"],
        )
    )
    story.append(Spacer(1, 5))
    story.append(Paragraph("复验对象与证据盘点", heading))
    for line in sections["复验对象与证据盘点"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line), small))
    story.append(Paragraph("复验流程图", heading))
    story.append(
        _runbook_flow_drawing(Drawing, Rect, Line, Polygon, String, colors, palette)
    )
    story.append(Spacer(1, 4))
    cards = []
    for symbol, name in (
        ("■", "可重跑验证"),
        ("●", "不可重跑历史证据"),
        ("▲", "用户持有的发布事项"),
    ):
        text = " ".join(
            _plain(line[2:]) for line in sections[name] if line.startswith("- ")
        )
        cards.append(
            [Paragraph(f"<b>{symbol} {name}</b><br/>{_emphasize(text)}", small)]
        )
    category_table = Table(cards, colWidths=[172 * mm])
    category_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([Paragraph("三类复验层级", heading), category_table])
    story.append(Paragraph("复验流程", heading))
    for line in sections["复验流程"]:
        if line.startswith("- ") or re.match(r"\d+\. ", line):
            story.append(Paragraph(_emphasize(line), body))
    story.append(Paragraph("Verification Matrix", heading))
    matrix_lines = [
        line for line in sections["Verification Matrix"] if line.startswith("|")
    ]
    matrix_rows = (matrix_lines[0], *matrix_lines[2:])
    matrix = [
        [Paragraph(escape(_plain(cell)), small) for cell in row.strip("|").split("|")]
        for row in matrix_rows
    ]
    if len(matrix) != 8 or any(len(row) != 8 for row in matrix):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    table = Table(
        matrix,
        colWidths=[
            13 * mm,
            20 * mm,
            23 * mm,
            27 * mm,
            23 * mm,
            26 * mm,
            27 * mm,
            20 * mm,
        ],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("TEXTCOLOR", (0, 0), (-1, 0), palette["ink"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(table)
    for name in ("PASS/FAIL 标准", "角色与 User Gate"):
        story.append(Paragraph(name, heading))
        for line in sections[name]:
            if line.startswith("- ") or re.match(r"\d+\. ", line):
                story.append(Paragraph(_emphasize(line), body))
    story.append(Paragraph("引用与对应关系", heading))
    for line in sections["引用"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    story.append(Paragraph("离线边界", heading))
    for line in sections["离线边界"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    stream = BytesIO()
    try:
        SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=13 * mm,
            bottomMargin=15 * mm,
            title="ProjectTown runbook",
            author="ProjectTown",
            invariant=1,
        ).build(story, onFirstPage=_footer_v3, onLaterPages=_footer_v3)
    except Exception as error:
        raise RuntimeError("PDF_RENDER_FAILED") from error
    return stream.getvalue()


def _runbook_v4_sections(lines: list[str]) -> dict[str, list[str]]:
    """Fail closed on the v4 runbook's exact ordered, non-duplicated contract."""
    headings = [line[3:] for line in lines if line.startswith("## ")]
    if headings != list(_RUNBOOK_V4_HEADINGS):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _v4_markdown_table(
    section: list[str], expected_columns: tuple[str, ...], expected_data_rows: int
) -> list[list[str]]:
    lines = [line for line in section if line.startswith("|")]
    if len(lines) != expected_data_rows + 2:
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    rows = [[_plain(cell) for cell in line.strip("|").split("|")] for line in lines]
    if tuple(rows[0]) != expected_columns or any(
        len(row) != len(expected_columns) for row in rows
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    if any(cell.strip(" -") != "" for cell in rows[1]):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    return rows


def _runbook_v4_flow_drawing(  # type: ignore[no-untyped-def]
    Drawing, Rect, Line, Polygon, String, colors, palette
):
    """A separate v4 vector flow: status gate is visible, not inferred from color."""
    drawing = Drawing(480, 154)
    nodes = (
        ("1", "BLOCK", 0, 108),
        ("2", "盘点与分类", 120, 108),
        ("3", "可重跑/历史/用户", 240, 108),
        ("4", "VERIFIED", 360, 108),
        ("5", "READY FOR USER GATE", 360, 34),
        ("6", "User disposition", 240, 34),
        ("7", "ACCEPT / REVISE / STOP", 120, 34),
        ("8", "记录下一步", 0, 34),
    )
    width, height = 100, 34
    for number, label, x, y in nodes:
        drawing.add(
            Rect(
                x,
                y,
                width,
                height,
                rx=5,
                ry=5,
                fillColor=colors.white,
                strokeColor=palette["line"],
                strokeWidth=1.2,
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 21,
                number,
                textAnchor="middle",
                fontName="ProjectTownFusionV4",
                fontSize=7,
                fillColor=palette["ink"],
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 8,
                label,
                textAnchor="middle",
                fontName="ProjectTownFusionV4",
                fontSize=6.5,
                fillColor=palette["ink"],
            )
        )
    for before, after in pairwise(nodes):
        _number, _label, x, y = before
        _next_number, _next_label, next_x, next_y = after
        if y == next_y and next_x > x:
            drawing.add(
                Line(
                    x + width,
                    y + height / 2,
                    next_x - 8,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        next_x - 8,
                        next_y + height / 2,
                        next_x - 13,
                        next_y + height / 2 + 4,
                        next_x - 13,
                        next_y + height / 2 - 4,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        elif y == next_y:
            drawing.add(
                Line(
                    x,
                    y + height / 2,
                    next_x + width + 8,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        next_x + width + 8,
                        next_y + height / 2,
                        next_x + width + 13,
                        next_y + height / 2 + 4,
                        next_x + width + 13,
                        next_y + height / 2 - 4,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        else:
            center = x + width / 2
            drawing.add(
                Line(
                    center,
                    y,
                    center,
                    next_y + height + 8,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        center,
                        next_y + height + 8,
                        center - 4,
                        next_y + height + 13,
                        center + 4,
                        next_y + height + 13,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
    return drawing


def _render_pdf_v4(result: ResultSession) -> bytes:
    """Render only the v4 T002 structure; malformed content never falls back."""
    from .material_workflow import _is_delivery_verification_runbook_v4

    if not _is_delivery_verification_runbook_v4(result.draft):
        return _render_pdf_v2(result)
    lines = result.preview_markdown.splitlines()
    sections = _runbook_v4_sections(lines)
    inventory = _v4_markdown_table(
        sections["复验对象与证据盘点"],
        ("Object", "Source", "Status", "Category", "Rerun", "Modify", "Authority"),
        9,
    )
    matrix = _v4_markdown_table(
        sections["Verification Matrix"],
        (
            "ID",
            "Object",
            "Category",
            "Operation",
            "Check",
            "PASS",
            "Failure",
            "Owner",
            "Basis",
        ),
        8,
    )
    required_phrases = (
        "Category depends on evidence provenance, not file format.",
        "isolated regeneration 仅作 comparison evidence",
        "Global PASS",
        "BLOCK：",
        "VERIFIED：",
        "READY FOR USER GATE：",
        "Fixed task：",
        "Rating dimensions：",
        "PASS / REVISE / FAIL",
        "evidence path",
        "S003：Inherited unresolved item - not claimed as verified。",
        "S005：Out of scope for this local delivery verification。",
        "S007：Out of scope for this local delivery verification。",
        "source documents selected:",
        "cited excerpts:",
    )
    markdown = result.preview_markdown
    if not all(phrase in markdown for phrase in required_phrases):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    try:
        pdfmetrics.registerFont(TTFont("ProjectTownFusionV4", str(font_path)))
    except Exception as error:
        raise RuntimeError("PDF_FONT_UNAVAILABLE") from error
    styles = getSampleStyleSheet()
    palette = {
        "ink": colors.HexColor("#183A5A"),
        "panel": colors.HexColor("#F2F6F8"),
        "line": colors.HexColor("#4B657A"),
    }
    body = ParagraphStyle(
        "PTV4Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV4",
        fontSize=8.2,
        leading=10.2,
    )
    small = ParagraphStyle("PTV4Small", parent=body, fontSize=7.3, leading=8.5)
    heading = ParagraphStyle(
        "PTV4Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV4",
        fontSize=11.2,
        leading=13,
        textColor=palette["ink"],
        spaceBefore=1,
        spaceAfter=1,
    )
    title = ParagraphStyle(
        "PTV4Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV4",
        fontSize=16,
        leading=19,
        alignment=TA_CENTER,
        textColor=palette["ink"],
    )
    document_title = next(
        (_plain(line[2:]) for line in lines if line.startswith("# ")),
        "ProjectTown runbook",
    )
    story = [Paragraph(escape(document_title), title), Spacer(1, 4)]
    summary = " ".join(_plain(line) for line in sections["执行摘要"] if line.strip())
    story.extend(
        [
            _info_box(
                Paragraph("<b>执行摘要</b><br/>" + _emphasize(summary), body),
                palette["panel"],
                palette["ink"],
            ),
            Spacer(1, 3),
            Paragraph("复验对象与证据盘点", heading),
        ]
    )
    inventory_table = Table(
        [
            [Paragraph(escape(cell), small) for cell in row]
            for row in (inventory[0], *inventory[2:])
        ],
        colWidths=[22 * mm, 24 * mm, 17 * mm, 28 * mm, 18 * mm, 17 * mm, 30 * mm],
        repeatRows=1,
    )
    inventory_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.extend(
        [
            inventory_table,
            Paragraph("复验流程图", heading),
            _runbook_v4_flow_drawing(
                Drawing, Rect, Line, Polygon, String, colors, palette
            ),
            Spacer(1, 2),
            Paragraph("三类复验层级", heading),
        ]
    )
    cards = []
    for symbol, name in (
        ("■", "可重跑验证"),
        ("●", "不可重跑历史证据"),
        ("▲", "用户持有的发布事项"),
    ):
        text = " ".join(
            _plain(line[2:]) for line in sections[name] if line.startswith("- ")
        )
        cards.append(
            [Paragraph(f"<b>{symbol} {name}</b><br/>{_emphasize(text)}", small)]
        )
    category_table = Table(cards, colWidths=[172 * mm])
    category_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.extend(
        [category_table, PageBreak(), Paragraph("Verification Matrix", heading)]
    )
    matrix_table = Table(
        [
            [Paragraph(escape(cell), small) for cell in row]
            for row in (matrix[0], *matrix[2:])
        ],
        colWidths=[
            9 * mm,
            20 * mm,
            18 * mm,
            30 * mm,
            23 * mm,
            21 * mm,
            19 * mm,
            17 * mm,
            15 * mm,
        ],
        repeatRows=1,
    )
    matrix_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    story.append(matrix_table)
    for name in (
        "PASS/FAIL 标准",
        "角色与 User Gate",
        "Independent Study 最小执行合同",
        "引用处置",
    ):
        story.append(Paragraph(name, heading))
        for line in sections[name]:
            if line.startswith("- ") or re.match(r"\d+\. ", line):
                story.append(Paragraph(_emphasize(line), body))
    story.append(Paragraph("引用与对应关系", heading))
    for line in sections["引用"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    story.append(Paragraph("离线边界", heading))
    for line in sections["离线边界"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    stream = BytesIO()
    try:
        SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=13 * mm,
            bottomMargin=15 * mm,
            title="ProjectTown runbook v4",
            author="ProjectTown",
            invariant=1,
        ).build(story, onFirstPage=_footer_v4, onLaterPages=_footer_v4)
    except Exception as error:
        raise RuntimeError("PDF_RENDER_FAILED") from error
    return stream.getvalue()


def _runbook_v5_sections(lines: list[str]) -> dict[str, list[str]]:
    """Parse the v6 runbook contract independently from its v5 predecessor."""
    headings = [line[3:] for line in lines if line.startswith("## ")]
    if headings != list(_RUNBOOK_V5_HEADINGS):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _v5_markdown_table(
    section: list[str], expected_columns: tuple[str, ...], expected_data_rows: int
) -> list[list[str]]:
    lines = [line for line in section if line.startswith("|")]
    if len(lines) != expected_data_rows + 2:
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    rows = [[_plain(cell) for cell in line.strip("|").split("|")] for line in lines]
    if tuple(rows[0]) != expected_columns or any(
        len(row) != len(expected_columns) for row in rows
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    if any(cell.strip(" -") != "" for cell in rows[1]):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    return rows


def _v5_matrix_cell(value: str) -> str:
    """Escape source text before preserving compact semantic tokens in v6 cells."""
    escaped = escape(value)
    for token in ("UNKNOWN/BLOCK", "S002 + S006", "S001 + RP", "S002 + S004"):
        escaped = escaped.replace(token, f"<nobr>{token}</nobr>")
    return escaped


def _runbook_v5_flow_drawing(  # type: ignore[no-untyped-def]
    Drawing, Rect, Line, Polygon, String, colors, palette
):
    """Render the v6 initial-block state explicitly as a vector flow."""
    drawing = Drawing(480, 154)
    nodes = (
        ("1", "Initial State: BLOCK", 0, 108),
        ("2", "盘点与分类", 120, 108),
        ("3", "可重跑/历史/用户", 240, 108),
        ("4", "VERIFIED", 360, 108),
        ("5", "READY FOR USER GATE", 360, 34),
        ("6", "User disposition", 240, 34),
        ("7", "ACCEPT / REVISE / STOP", 120, 34),
        ("8", "记录下一步", 0, 34),
    )
    width, height = 100, 34
    for number, label, x, y in nodes:
        drawing.add(
            Rect(
                x,
                y,
                width,
                height,
                rx=5,
                ry=5,
                fillColor=colors.white,
                strokeColor=palette["line"],
                strokeWidth=1.2,
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 21,
                number,
                textAnchor="middle",
                fontName="ProjectTownFusionV5",
                fontSize=7,
                fillColor=palette["ink"],
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 8,
                label,
                textAnchor="middle",
                fontName="ProjectTownFusionV5",
                fontSize=6.1,
                fillColor=palette["ink"],
            )
        )
    for before, after in pairwise(nodes):
        _number, _label, x, y = before
        _next_number, _next_label, next_x, next_y = after
        if y == next_y and next_x > x:
            drawing.add(
                Line(
                    x + width,
                    y + height / 2,
                    next_x - 8,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        next_x - 8,
                        next_y + height / 2,
                        next_x - 13,
                        next_y + height / 2 + 4,
                        next_x - 13,
                        next_y + height / 2 - 4,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        elif y == next_y:
            drawing.add(
                Line(
                    x,
                    y + height / 2,
                    next_x + width + 8,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        next_x + width + 8,
                        next_y + height / 2,
                        next_x + width + 13,
                        next_y + height / 2 + 4,
                        next_x + width + 13,
                        next_y + height / 2 - 4,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        else:
            center = x + width / 2
            drawing.add(
                Line(
                    center,
                    y,
                    center,
                    next_y + height + 8,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        center,
                        next_y + height + 8,
                        center - 4,
                        next_y + height + 13,
                        center + 4,
                        next_y + height + 13,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
    return drawing


def _render_pdf_v5(result: ResultSession) -> bytes:
    """Render the v6 minor-refinement runbook without changing v5 rendering."""
    from .material_workflow import _is_delivery_verification_runbook_v4

    if not _is_delivery_verification_runbook_v4(result.draft):
        return _render_pdf_v2(result)
    lines = result.preview_markdown.splitlines()
    sections = _runbook_v5_sections(lines)
    inventory = _v5_markdown_table(
        sections["复验对象与证据盘点"],
        (
            "Object",
            "Source",
            "Status",
            "Category",
            "Rerun",
            "Modify",
            "Final Authority",
        ),
        9,
    )
    matrix = _v5_markdown_table(
        sections["Verification Matrix"],
        (
            "ID",
            "Object",
            "Category",
            "Operation",
            "Check",
            "PASS",
            "Failure",
            "Owner",
            "Basis",
        ),
        8,
    )
    required_phrases = (
        "Final Authority means final disposition",
        "Category depends on evidence provenance/state, not file format.",
        "isolated comparison regen",
        "Initial State: BLOCK",
        "S002 + S006",
        "Independent Study Reviewer",
        "RP = Reviewer-defined verification policy",
        "S003：Inherited unresolved item - not claimed as verified。",
        "S005：Out of scope for this local delivery verification。",
        "S007：Out of scope for this local delivery verification。",
    )
    if not all(phrase in result.preview_markdown for phrase in required_phrases):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    try:
        pdfmetrics.registerFont(TTFont("ProjectTownFusionV5", str(font_path)))
    except Exception as error:
        raise RuntimeError("PDF_FONT_UNAVAILABLE") from error
    styles = getSampleStyleSheet()
    palette = {
        "ink": colors.HexColor("#183A5A"),
        "panel": colors.HexColor("#F2F6F8"),
        "line": colors.HexColor("#4B657A"),
    }
    body = ParagraphStyle(
        "PTV5Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV5",
        fontSize=8.2,
        leading=10.2,
    )
    small = ParagraphStyle("PTV5Small", parent=body, fontSize=7.3, leading=8.5)
    heading = ParagraphStyle(
        "PTV5Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV5",
        fontSize=11.2,
        leading=13,
        textColor=palette["ink"],
        spaceBefore=1,
        spaceAfter=1,
    )
    title = ParagraphStyle(
        "PTV5Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV5",
        fontSize=16,
        leading=19,
        alignment=TA_CENTER,
        textColor=palette["ink"],
    )
    document_title = next(
        (_plain(line[2:]) for line in lines if line.startswith("# ")),
        "ProjectTown runbook",
    )
    story = [Paragraph(escape(document_title), title), Spacer(1, 4)]
    summary = " ".join(_plain(line) for line in sections["执行摘要"] if line.strip())
    story.extend(
        [
            _info_box(
                Paragraph("<b>执行摘要</b><br/>" + _emphasize(summary), body),
                palette["panel"],
                palette["ink"],
            ),
            Spacer(1, 3),
            Paragraph("复验对象与证据盘点", heading),
        ]
    )
    inventory_table = Table(
        [
            [Paragraph(escape(cell), small) for cell in row]
            for row in (inventory[0], *inventory[2:])
        ],
        colWidths=[22 * mm, 24 * mm, 24 * mm, 28 * mm, 15 * mm, 15 * mm, 44 * mm],
        repeatRows=1,
    )
    inventory_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.extend(
        [
            inventory_table,
            Paragraph("复验流程图", heading),
            _runbook_v5_flow_drawing(
                Drawing, Rect, Line, Polygon, String, colors, palette
            ),
            Spacer(1, 2),
            Paragraph("三类复验层级", heading),
        ]
    )
    cards = []
    for symbol, name in (
        ("■", "可重跑验证"),
        ("●", "不可重跑历史证据"),
        ("▲", "用户持有的发布事项"),
    ):
        text = " ".join(
            _plain(line[2:]) for line in sections[name] if line.startswith("- ")
        )
        cards.append(
            [Paragraph(f"<b>{symbol} {name}</b><br/>{_emphasize(text)}", small)]
        )
    category_table = Table(cards, colWidths=[172 * mm])
    category_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.extend(
        [category_table, PageBreak(), Paragraph("Verification Matrix", heading)]
    )
    matrix_table = Table(
        [
            [Paragraph(_v5_matrix_cell(cell), small) for cell in row]
            for row in (matrix[0], *matrix[2:])
        ],
        colWidths=[width * mm for width in _RUNBOOK_V5_MATRIX_WIDTHS_MM],
        repeatRows=1,
    )
    matrix_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    story.append(matrix_table)
    for name in (
        "PASS/FAIL 标准",
        "角色与 User Gate",
        "Independent Study 最小执行合同",
        "引用处置",
    ):
        story.append(Paragraph(name, heading))
        for line in sections[name]:
            if line.startswith("- ") or re.match(r"\d+\. ", line):
                story.append(Paragraph(_emphasize(line), body))
    story.append(Paragraph("引用与对应关系", heading))
    for line in sections["引用"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    story.append(Paragraph("离线边界", heading))
    for line in sections["离线边界"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    stream = BytesIO()
    try:
        SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=13 * mm,
            bottomMargin=15 * mm,
            title="ProjectTown runbook v6",
            author="ProjectTown",
            invariant=1,
        ).build(story, onFirstPage=_footer_v5, onLaterPages=_footer_v5)
    except Exception as error:
        raise RuntimeError("PDF_RENDER_FAILED") from error
    return stream.getvalue()


def _runbook_v6_sections(lines: list[str]) -> dict[str, list[str]]:
    """Parse the additive v7 execution contract without accepting v6 markdown."""
    headings = [line[3:] for line in lines if line.startswith("## ")]
    if headings != list(_RUNBOOK_V6_HEADINGS):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _v6_markdown_table(
    section: list[str], expected_columns: tuple[str, ...], expected_data_rows: int
) -> list[list[str]]:
    """Reject malformed v7 tables before any visual layout is attempted."""
    lines = [line for line in section if line.startswith("|")]
    if len(lines) != expected_data_rows + 2:
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    rows = [[_plain(cell) for cell in line.strip("|").split("|")] for line in lines]
    if tuple(rows[0]) != expected_columns or any(
        len(row) != len(expected_columns) for row in rows
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    if any(cell.strip(" -") != "" for cell in rows[1]):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    return rows


def _v6_matrix_cell(value: str) -> str:
    """Preserve compact semantic tokens while keeping v7 rendering independent."""
    escaped = escape(value)
    for token in (
        "UNKNOWN/BLOCK",
        "S002 + S006",
        "S001 + S002",
        "S002 + S004",
        "Category",
        "Initial State: BLOCK",
        "Conditional Release Action",
        "Mandatory Verification",
        "User Decision",
        "RP",
    ):
        escaped = escaped.replace(token, f"<nobr>{token}</nobr>")
    escaped = escaped.replace(
        "reopen/text/render", "<nobr>reopen/text</nobr><br/><nobr>render</nobr>"
    )
    escaped = escaped.replace(
        "tuple/consistency", "<nobr>tuple/</nobr><br/><nobr>consistency</nobr>"
    )
    escaped = escaped.replace(
        "exist/readability", "<nobr>exist/</nobr><br/><nobr>readability</nobr>"
    )
    escaped = escaped.replace(
        "BLOCK; retain original", "<nobr>BLOCK; retain</nobr><br/><nobr>original</nobr>"
    )
    escaped = escaped.replace(
        "Apply/Publish", "<nobr>Apply/</nobr><br/><nobr>Publish</nobr>"
    )
    return escaped


def _runbook_v6_flow_drawing(  # type: ignore[no-untyped-def]
    Drawing, Rect, Line, Polygon, String, colors, palette
):
    """Draw the nine-node v7 fail-closed snake flow in its actual order."""
    drawing = Drawing(480, 150)
    nodes = (
        ("1", "Initial State: BLOCK", 0, 106),
        ("2", "盘点与分类", 120, 106),
        ("3", "可重跑 + 历史只读", 240, 106),
        ("4", "VERIFIED", 360, 106),
        ("5", "Independent Study", 360, 34),
        ("6", "READY FOR USER GATE", 270, 34),
        ("7", "User disposition", 180, 34),
        ("8", "ACCEPT / REVISE / STOP", 90, 34),
        ("9", "记录下一步", 0, 34),
    )
    width, height = 84, 32
    for number, label, x, y in nodes:
        drawing.add(
            Rect(
                x,
                y,
                width,
                height,
                rx=5,
                ry=5,
                fillColor=colors.white,
                strokeColor=palette["line"],
                strokeWidth=1.2,
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 20,
                number,
                textAnchor="middle",
                fontName="ProjectTownFusionV6",
                fontSize=6.8,
                fillColor=palette["ink"],
            )
        )
        drawing.add(
            String(
                x + width / 2,
                y + 8,
                label,
                textAnchor="middle",
                fontName="ProjectTownFusionV6",
                fontSize=4.8,
                fillColor=palette["ink"],
            )
        )
    for before, after in pairwise(nodes):
        _number, _label, x, y = before
        _next_number, _next_label, next_x, next_y = after
        if y == next_y and next_x > x:
            start_x, end_x = x + width, next_x - 7
            drawing.add(
                Line(
                    start_x,
                    y + height / 2,
                    end_x,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        end_x,
                        next_y + height / 2,
                        end_x - 5,
                        next_y + height / 2 + 3,
                        end_x - 5,
                        next_y + height / 2 - 3,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        elif y == next_y:
            start_x, end_x = x, next_x + width + 7
            drawing.add(
                Line(
                    start_x,
                    y + height / 2,
                    end_x,
                    next_y + height / 2,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        end_x,
                        next_y + height / 2,
                        end_x + 5,
                        next_y + height / 2 + 3,
                        end_x + 5,
                        next_y + height / 2 - 3,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
        else:
            center = x + width / 2
            drawing.add(
                Line(
                    center,
                    y,
                    center,
                    next_y + height + 7,
                    strokeColor=palette["line"],
                    strokeWidth=1.2,
                )
            )
            drawing.add(
                Polygon(
                    [
                        center,
                        next_y + height + 7,
                        center - 3,
                        next_y + height + 12,
                        center + 3,
                        next_y + height + 12,
                    ],
                    fillColor=palette["line"],
                    strokeColor=palette["line"],
                )
            )
    return drawing


def _render_pdf_v6(result: ResultSession) -> bytes:
    """Render the v7 bound-run runbook under its new, strict presentation pair."""
    from .material_workflow import _is_delivery_verification_runbook_v4

    if not _is_delivery_verification_runbook_v4(result.draft):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    lines = result.preview_markdown.splitlines()
    sections = _runbook_v6_sections(lines)
    binding_lines = [
        line[2:] for line in sections["Run Binding"] if line.startswith("- ")
    ]
    if len(binding_lines) != 11 or not binding_lines[0].startswith("status: "):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    binding_pairs = [
        (binding_lines[index], binding_lines[index + 1])
        for index in range(1, len(binding_lines), 2)
    ]
    inventory = _v6_markdown_table(
        sections["复验对象与证据盘点"],
        (
            "Matrix ID",
            "Object",
            "Source",
            "Status",
            "Category",
            "Rerun",
            "Modify",
            "Final Authority",
        ),
        9,
    )
    matrix = _v6_markdown_table(
        sections["Verification Matrix"],
        (
            "ID",
            "Row Type",
            "Object",
            "Category",
            "Operation",
            "Check",
            "PASS",
            "Failure",
            "Owner",
            "Basis",
        ),
        8,
    )
    required_phrases = (
        "M01-M06 必须 VERIFIED",
        "Independent Study",
        "READY FOR USER GATE",
        "BLOCK; retain original",
        "S002 + S006",
        "RP = Reviewer-defined verification policy",
        "S003：Inherited unresolved item - not claimed as verified。",
        "S005：Out of scope for this local delivery verification。",
        "S007：Out of scope for this local delivery verification。",
    )
    if not all(
        phrase in result.preview_markdown for phrase in required_phrases
    ) or not any(
        status in result.preview_markdown
        for status in ("INCOMPLETE/BLOCK", "BOUND; Initial State: BLOCK")
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    try:
        pdfmetrics.registerFont(TTFont("ProjectTownFusionV6", str(font_path)))
    except Exception as error:
        raise RuntimeError("PDF_FONT_UNAVAILABLE") from error
    styles = getSampleStyleSheet()
    palette = {
        "ink": colors.HexColor("#183A5A"),
        "panel": colors.HexColor("#F2F6F8"),
        "line": colors.HexColor("#4B657A"),
    }
    body = ParagraphStyle(
        "PTV6Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV6",
        fontSize=7.5,
        leading=8.7,
    )
    small = ParagraphStyle("PTV6Small", parent=body, fontSize=7.3, leading=8.1)
    heading = ParagraphStyle(
        "PTV6Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV6",
        fontSize=9.8,
        leading=11,
        textColor=palette["ink"],
        spaceBefore=1,
        spaceAfter=1,
    )
    title = ParagraphStyle(
        "PTV6Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV6",
        fontSize=14.5,
        leading=17,
        alignment=TA_CENTER,
        textColor=palette["ink"],
    )
    document_title = next(
        (_plain(line[2:]) for line in lines if line.startswith("# ")),
        "ProjectTown runbook",
    )
    story = [Paragraph(escape(document_title), title), Spacer(1, 2)]
    summary = " ".join(_plain(line) for line in sections["执行摘要"] if line.strip())
    story.extend(
        [
            _info_box(
                Paragraph("<b>执行摘要</b><br/>" + _emphasize(summary), body),
                palette["panel"],
                palette["ink"],
            ),
            Paragraph("Run Binding", heading),
            _info_box(
                Paragraph(_v6_matrix_cell(binding_lines[0]), small),
                palette["panel"],
                palette["ink"],
            ),
        ]
    )
    binding_table = Table(
        [
            [Paragraph(escape(left), small), Paragraph(escape(right), small)]
            for left, right in binding_pairs
        ],
        colWidths=[86 * mm, 86 * mm],
    )
    binding_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.extend([binding_table, Paragraph("复验对象与证据盘点", heading)])
    inventory_table = Table(
        [
            [Paragraph(_v6_matrix_cell(cell), small) for cell in row]
            for row in (inventory[0], *inventory[2:])
        ],
        colWidths=[
            9 * mm,
            20 * mm,
            23 * mm,
            22 * mm,
            17 * mm,
            14 * mm,
            13 * mm,
            54 * mm,
        ],
        repeatRows=1,
    )
    inventory_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.extend(
        [
            inventory_table,
            Paragraph("复验流程图", heading),
            _runbook_v6_flow_drawing(
                Drawing, Rect, Line, Polygon, String, colors, palette
            ),
            Paragraph("三类复验层级", heading),
        ]
    )
    cards = []
    for symbol, name in (
        ("■", "可重跑验证"),
        ("●", "不可重跑历史证据"),
        ("▲", "用户持有的发布事项"),
    ):
        text = " ".join(
            _plain(line[2:]) for line in sections[name] if line.startswith("- ")
        )
        cards.append(Paragraph(f"<b>{symbol} {name}</b><br/>{_emphasize(text)}", small))
    category_table = Table([cards], colWidths=[172 / 3 * mm] * 3)
    category_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    study_text = " ".join(
        _plain(line[2:])
        for line in sections["Independent Study 最小执行合同"]
        if line.startswith("- ")
    )
    story.extend(
        [
            category_table,
            Paragraph("Independent Study 最小执行合同", heading),
            _info_box(
                Paragraph(_emphasize(study_text), small),
                palette["panel"],
                palette["ink"],
            ),
            PageBreak(),
            Paragraph("Verification Matrix", heading),
        ]
    )
    matrix_table = Table(
        [
            [Paragraph(_v6_matrix_cell(cell), small) for cell in row]
            for row in (matrix[0], *matrix[2:])
        ],
        colWidths=[width * mm for width in _RUNBOOK_V6_MATRIX_WIDTHS_MM],
        repeatRows=1,
    )
    matrix_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(matrix_table)
    for name in ("状态合同", "PASS/FAIL 标准", "角色与 User Gate", "引用处置"):
        story.append(Paragraph(name, heading))
        for line in sections[name]:
            if line.startswith("- ") or re.match(r"\d+\. ", line):
                story.append(Paragraph(_emphasize(line), body))
    story.append(Paragraph("引用与对应关系", heading))
    for line in sections["引用"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    story.append(Paragraph("离线边界", heading))
    for line in sections["离线边界"]:
        if line.startswith("- "):
            story.append(Paragraph(_emphasize(line[2:]), small))
    stream = BytesIO()
    try:
        SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=11 * mm,
            bottomMargin=14 * mm,
            title="ProjectTown runbook v7",
            author="ProjectTown",
            invariant=1,
        ).build(story, onFirstPage=_footer_v6, onLaterPages=_footer_v6)
    except Exception as error:
        raise RuntimeError("PDF_RENDER_FAILED") from error
    return stream.getvalue()


def _plain(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
    value = (
        value.replace("**", "")
        .replace("`", "")
        .replace("ˋ", "")
        .replace("⏎", "")
        .replace("�", "")
    )
    return re.sub(r"\s+", " ", value).strip()


def _emphasize(value: str) -> str:
    """Escape all artifact text, then emphasize only deterministic safe labels."""
    escaped = escape(_plain(value))
    return re.sub(
        r"(P[0-2]|目标|行动|交付物|验收标准|风险|阻断项|用户决策|离线边界)",
        r'<font color="#183A5A"><b>\1</b></font>',
        escaped,
    )


def _flow_drawing(  # type: ignore[no-untyped-def]
    stages, Drawing, Rect, Line, Polygon, String, colors, palette
):
    """Two-row vector path that keeps every parsed stage visible."""
    drawing = Drawing(480, 164)
    box_width, box_height = 112, 44
    nodes = [("开始", "目标与约束", "ink", 44, 104)]
    positions = ((184, 104), (324, 104), (324, 32), (184, 32))
    for index, stage in enumerate(stages[:4]):
        priority = stage.get("优先级", "P2")
        primary_priority = stage.get("主优先级", priority)
        nodes.append(
            (
                priority,
                _plain(stage.get("标题", "阶段计划")),
                primary_priority.lower(),
                *positions[index],
            )
        )
    nodes.append(("结束", "验收与决定", "ink", 44, 32))
    for tag, text, color_key, x, y in nodes:
        color = palette[color_key]
        drawing.add(
            Rect(
                x,
                y,
                box_width,
                box_height,
                rx=6,
                ry=6,
                fillColor=colors.white,
                strokeColor=color,
                strokeWidth=1.6,
            )
        )
        drawing.add(
            String(
                x + box_width / 2,
                y + 29,
                tag,
                textAnchor="middle",
                fontName="ProjectTownFusionV2",
                fontSize=9,
                fillColor=color,
            )
        )
        drawing.add(
            String(
                x + box_width / 2,
                y + 14,
                text,
                textAnchor="middle",
                fontName="ProjectTownFusionV2",
                fontSize=9,
                fillColor=palette["ink"],
            )
        )
    for before, after in pairwise(nodes):
        _tag, _text, _color, x, y = before
        _next_tag, _next_text, _next_color, next_x, next_y = after
        if y == next_y and next_x > x:
            start_x, start_y = x + box_width, y + box_height / 2
            end_x, end_y = next_x, next_y + box_height / 2
            drawing.add(
                Line(
                    start_x,
                    start_y,
                    end_x - 9,
                    end_y,
                    strokeColor=palette["muted"],
                    strokeWidth=1.3,
                )
            )
            drawing.add(
                Polygon(
                    [end_x - 9, end_y, end_x - 14, end_y + 4, end_x - 14, end_y - 4],
                    fillColor=palette["muted"],
                    strokeColor=palette["muted"],
                )
            )
        elif y == next_y:
            start_x, start_y = x, y + box_height / 2
            end_x, end_y = next_x + box_width, next_y + box_height / 2
            drawing.add(
                Line(
                    start_x,
                    start_y,
                    end_x + 9,
                    end_y,
                    strokeColor=palette["muted"],
                    strokeWidth=1.3,
                )
            )
            drawing.add(
                Polygon(
                    [end_x + 9, end_y, end_x + 14, end_y + 4, end_x + 14, end_y - 4],
                    fillColor=palette["muted"],
                    strokeColor=palette["muted"],
                )
            )
        else:
            center_x = x + box_width / 2
            start_y, end_y = y, next_y + box_height
            drawing.add(
                Line(
                    center_x,
                    start_y,
                    center_x,
                    end_y + 9,
                    strokeColor=palette["muted"],
                    strokeWidth=1.3,
                )
            )
            drawing.add(
                Polygon(
                    [
                        center_x,
                        end_y + 9,
                        center_x - 4,
                        end_y + 14,
                        center_x + 4,
                        end_y + 14,
                    ],
                    fillColor=palette["muted"],
                    strokeColor=palette["muted"],
                )
            )
    return drawing


def _plan_stages(lines: list[str]) -> list[dict[str, str]]:
    stages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    key_map = {
        "目的：": "目的",
        "行动：": "行动",
        "交付物：": "交付物",
        "验收标准：": "验收标准",
    }
    for line in lines:
        if line.startswith("### 阶段"):
            if current:
                stages.append(current)
            title = _plain(line[4:])
            match = re.match(
                r"^阶段\s*(\d+)\s*[（(]\s*(P[0-2](?:\s*/\s*P?[0-2])*)\s*[）)]\s*[：:]\s*(.+)$",
                title,
            )
            priority = match.group(2).replace(" ", "") if match else "P2"
            priority_tokens = re.findall(r"P([0-2])", priority)
            primary_priority = f"P{min(priority_tokens)}" if priority_tokens else "P2"
            current = {
                "标题": match.group(3) if match else title,
                "阶段号": match.group(1) if match else str(len(stages) + 1),
                "优先级": priority,
                "主优先级": primary_priority,
            }
        elif current and line.startswith("- "):
            value = _plain(line[2:])
            for prefix, key in key_map.items():
                if value.startswith(prefix):
                    current[key] = value[len(prefix) :].strip()
                    break
    if current:
        stages.append(current)
    return stages[:4] or [
        {
            "标题": "阶段计划",
            "阶段号": "1",
            "优先级": "P2",
            "主优先级": "P2",
            "目的": "形成可执行计划",
            "行动": "依据冻结成果推进",
            "交付物": "阶段记录",
            "验收标准": "结论可追溯",
        }
    ]


def _stage_card(  # type: ignore[no-untyped-def]
    stage,
    number,
    priority,
    primary_priority,
    color,
    body,
    label,
    Paragraph,
    KeepTogether,
    Table,
    TableStyle,
    colors,
):
    shape = {"P0": "■ 紧急", "P1": "● 重要", "P2": "▲ 后续"}[primary_priority]
    header = Table(
        [
            [
                Paragraph(f"<b>{priority}</b><br/>{shape}", label),
                Paragraph(
                    f"<b>阶段 {stage.get('阶段号', number)}｜{escape(stage.get('标题', '阶段计划'))}</b>",
                    label,
                ),
            ]
        ],
        colWidths=(70, 398),
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F9FA")),
                ("BOX", (0, 0), (-1, -1), 1.3, color),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    rows = [
        [
            Paragraph(f"<b>{key}</b>", label),
            Paragraph(_emphasize(stage.get(key, "未记录")), body),
        ]
        for key in ("目的", "行动", "交付物", "验收标准")
    ]
    table = Table(rows, colWidths=(78, 390), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.3, color),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return KeepTogether([header, table])


def _info_box(flowable, background, border):  # type: ignore[no-untyped-def]
    from reportlab.platypus import Table, TableStyle

    table = Table([[flowable]], colWidths=(468,))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 1.2, border),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _section_bullets(lines: list[str], section: str) -> list[str]:
    """Return only direct bullets below one exact level-two Markdown section."""
    active = False
    values: list[str] = []
    for line in lines:
        if line.startswith("## "):
            active = _plain(line[3:]) == section
            continue
        if active and line.startswith("- "):
            values.append(_plain(line[2:]))
    return values


def _compact_citation(value: str) -> str:
    plain = _plain(value)
    match = re.match(
        r"(\[S\d{3}\]\s+`?[^`\s]+`?\s+第\s*\d+-\d+\s*行[^：:]*[：:]?)\s*(.*)", plain
    )
    if not match:
        return plain[:180]
    excerpt = match.group(2)
    if len(excerpt) > 120:
        excerpt = excerpt[:119].rstrip("，。；; ") + "…"
    return f"{match.group(1)} {excerpt}".strip()


def _append_plan_callouts(  # type: ignore[no-untyped-def]
    story, lines, heading, body, palette, Paragraph, Spacer
):
    values = _section_bullets(lines, "依赖、阻断项与用户决策")
    labels = (
        ("依赖：", "■ 依赖", "panel", "ink"),
        ("阻断项：", "× 阻断项", "risk", "p0"),
        ("用户决策点：", "? 用户决策点", "panel", "p1"),
        ("未知项：", "△ 未知项", "panel", "p2"),
    )
    boxes = []
    for value in values:
        for prefix, label, background, border in labels:
            if value.startswith(prefix):
                boxes.append((label, value[len(prefix) :].strip(), background, border))
                break
    if not boxes:
        return
    story.append(Paragraph("依赖、阻断项与用户决策", heading))
    for label, value, background, border in boxes:
        story.append(
            _info_box(
                Paragraph(f"<b>{label}</b>｜" + _emphasize(value), body),
                palette[background],
                palette[border],
            )
        )
        story.append(Spacer(1, 3))


def _append_markdown(  # type: ignore[no-untyped-def]
    story, lines, heading, body, label, Paragraph
):
    for line in lines:
        if not line.strip() or line.startswith(("```", "# ")):
            continue
        if line.startswith("## "):
            story.append(Paragraph(escape(_plain(line[3:])), heading))
        elif line.startswith("### "):
            story.append(Paragraph("<b>" + escape(_plain(line[4:])) + "</b>", label))
        elif line.startswith("- "):
            story.append(Paragraph("• " + _emphasize(line[2:]), body))
        elif not line.startswith("["):
            story.append(Paragraph(_emphasize(line), body))


def _footer_v2(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setStrokeColorRGB(0.09, 0.23, 0.35)
    canvas.line(46, 38, canvas._pagesize[0] - 46, 38)
    canvas.setFont("ProjectTownFusionV2", 8)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 PDF｜文字和形状标签均可灰阶识别")
    canvas.drawRightString(canvas._pagesize[0] - 46, 26, f"第 {document.page} 页")
    canvas.restoreState()


def _footer_v3(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setStrokeColorRGB(0.09, 0.23, 0.35)
    canvas.line(46, 38, canvas._pagesize[0] - 46, 38)
    canvas.setFont("ProjectTownFusionV3", 8)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook｜User gate 独占")
    canvas.drawRightString(canvas._pagesize[0] - 46, 26, f"第 {document.page} 页")
    canvas.restoreState()


def _footer_v4(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setStrokeColorRGB(0.09, 0.23, 0.35)
    canvas.line(46, 38, canvas._pagesize[0] - 46, 38)
    canvas.setFont("ProjectTownFusionV4", 8)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook v4｜User Gate 独占")
    canvas.drawRightString(canvas._pagesize[0] - 46, 26, f"第 {document.page} 页")
    canvas.restoreState()


def _footer_v5(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("ProjectTownFusionV5", 8)
    canvas.setFillColorRGB(0.094, 0.227, 0.353)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook v6｜User Gate 独占")
    canvas.drawRightString(548, 26, f"{document.page}")
    canvas.restoreState()


def _footer_v6(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("ProjectTownFusionV6", 8)
    canvas.setFillColorRGB(0.094, 0.227, 0.353)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook v7｜User Gate 独占")
    canvas.drawRightString(548, 26, f"{document.page}")
    canvas.restoreState()


def _footer_v7(canvas, document) -> None:  # type: ignore[no-untyped-def]
    """Footer for the additive v8 presentation only (v6 bytes stay frozen)."""
    canvas.saveState()
    canvas.setFont("ProjectTownFusionV7", 8)
    canvas.setFillColorRGB(0.094, 0.227, 0.353)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook v8｜User Gate 独占")
    canvas.drawRightString(548, 26, f"{document.page}")
    canvas.restoreState()


def _summary(lines: list[str]) -> str:
    start = next(
        (index for index, line in enumerate(lines) if line == "## 计划总结"), 0
    )
    return next(
        (
            line
            for line in lines[start + 1 :]
            if line.strip() and not line.startswith("#")
        ),
        "离线计划尚无可显示摘要。",
    )


def _clean(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
    value = value.replace("**", "").replace("`", "").replace("ˋ", "")
    value = value.replace("⏎", "").replace("�", "")
    for source, target in (
        ("Purpose:", "目的："),
        ("Action:", "行动："),
        ("Basis:", "依据："),
        ("Deliverable:", "交付物："),
        ("Acceptance:", "验收标准："),
    ):
        value = value.replace(source, target)
    value = re.sub(r"\s+", " ", value).strip()
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _footer(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("ProjectTownFusion", 8)
    canvas.drawRightString(canvas._pagesize[0] - 51, 34, str(document.page))
    canvas.restoreState()


def _render_pdf_v7(result: ResultSession) -> bytes:
    """Balanced four-page v8 runbook renderer; intentionally independent of v6."""
    if (
        result.draft.future_parameters.generator_version
        != "deterministic-grounded-plan-v7"
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    pdfmetrics.registerFont(TTFont("ProjectTownFusionV7", str(font_path)))
    styles = getSampleStyleSheet()
    ink, panel, line = (
        colors.HexColor("#183A5A"),
        colors.HexColor("#F2F6F8"),
        colors.HexColor("#4B657A"),
    )
    body = ParagraphStyle(
        "V7Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV7",
        fontSize=8.2,
        leading=10.1,
    )
    cell = ParagraphStyle(
        "V7Cell",
        parent=body,
        fontSize=8,
        leading=9.5,
        splitLongWords=False,
    )
    center = ParagraphStyle("V7Center", parent=cell, alignment=TA_CENTER)
    heading = ParagraphStyle(
        "V7Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV7",
        fontSize=11.5,
        leading=14,
        textColor=ink,
        spaceBefore=4,
        spaceAfter=3,
    )
    title = ParagraphStyle(
        "V7Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV7",
        fontSize=17,
        leading=20,
        alignment=TA_CENTER,
        textColor=ink,
    )
    lines = result.preview_markdown.splitlines()
    sections: dict[str, list[str]] = {}
    current = ""
    for item in lines:
        if item.startswith("## "):
            current = item[3:]
            sections[current] = []
        elif current:
            sections[current].append(item)
    if (
        "M00 Run Binding preflight" not in sections
        or "Verification Matrix" not in sections
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")

    def para(text: str, style=body):
        return Paragraph(_clean(text.lstrip("- ")), style)

    def table(rows, widths, header=True, compact=False):
        data = [
            [
                para(str(x), center if (r == 0 or c in (0, 1, 3, 8)) else cell)
                for c, x in enumerate(row)
            ]
            for r, row in enumerate(rows)
        ]
        item = Table(data, colWidths=[x * mm for x in widths], repeatRows=1)
        item.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.55, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, line),
                    ("BACKGROUND", (0, 0), (-1, 0), panel),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1 if compact else 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1 if compact else 3),
                ]
            )
        )
        return item

    document_title = next(
        (_clean(x[2:]) for x in lines if x.startswith("# ")), "ProjectTown runbook v8"
    )

    def binding_display(value: str) -> str:
        """Keep long binding paths scannable while canonical values remain in Result."""
        value = _clean(value)
        # Binding placeholders are source data, not ReportLab markup.  A neutral
        # display marker avoids rendering escaped HTML entities in the PDF.
        value = value.replace("&lt;", "[").replace("&gt;", "]")
        if len(value) <= 46:
            return value
        return f"{value[:15]}…{value[-29:]}"

    def binding_label(value: str) -> str:
        """Use display-only labels that keep the Value column independent."""
        return {
            "approved_hash_provenance_tuple_source": "approved_hash_tuple_source",
            "planned_study_evidence_output": "planned_study_output",
        }.get(value, value)

    story = [Paragraph(document_title, title), Spacer(1, 3)]
    summary = " ".join(x for x in sections.get("执行摘要", []) if x.strip())
    story += [
        # `_clean()` escapes source text.  Keep the controlled ReportLab markup
        # outside it so the PDF never renders literal `<b>` / `<br/>` tokens.
        _info_box(
            Paragraph("<b>执行摘要</b><br/>" + _clean(summary), body), panel, ink
        ),
        Paragraph("M00 Run Binding preflight", heading),
    ]
    bind_rows = [["Group", "Binding", "Value"]]
    for item in sections["M00 Run Binding preflight"]:
        if item.startswith("- ") and " | " in item:
            group, binding, value = (part.strip() for part in item[2:].split(" | ", 2))
            bind_rows.append([group, binding_label(binding), binding_display(value)])
    story += [
        table(bind_rows, (22, 47, 103), compact=True),
        Paragraph("复验对象与证据盘点", heading),
    ]
    inv = [
        x.strip("|").split("|")
        for x in sections.get("复验对象与证据盘点", [])
        if x.startswith("|")
    ]
    if len(inv) >= 3:
        # The canonical preview retains the full status/category phrases.  The
        # Inventory is a display-only scan aid, so use the compact labels that
        # fit their cells without colliding with neighbouring columns.
        inventory_rows = [[z.strip() for z in row] for row in [inv[0], *inv[2:]]]
        for row in inventory_rows[1:]:
            if len(row) != 8:
                raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
            row[3] = {"UNKNOWN/BLOCK": "UNKNOWN"}.get(row[3], row[3])
            row[4] = {
                "可重跑验证": "Rerun",
                "不可重跑历史证据": "History",
                "用户持有的发布事项": "User",
            }.get(row[4], row[4])
        inventory_data = [
            [
                para(
                    value,
                    center
                    if (row_index == 0 or column in (0, 3, 4, 5, 6, 7))
                    else cell,
                )
                for column, value in enumerate(row)
            ]
            for row_index, row in enumerate(inventory_rows)
        ]
        inventory_table = Table(
            inventory_data,
            colWidths=[
                13 * mm,
                24 * mm,
                24 * mm,
                18 * mm,
                17 * mm,
                12 * mm,
                12 * mm,
                52 * mm,
            ],
            repeatRows=1,
        )
        inventory_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.55, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, line),
                    ("BACKGROUND", (0, 0), (-1, 0), panel),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story += [
            inventory_table,
            Paragraph(
                "Inventory UNKNOWN is blocking; detailed category and failure semantics are defined by M00-M08 Matrix.",
                cell,
            ),
        ]
    story.append(PageBreak())
    story += [Paragraph("执行语义与流程", heading)]
    # A deliberately vertical sequence avoids the ambiguous reverse arrows in
    # the former snake table.  Drawing objects remain vector content in PDF.
    flow_labels = (
        "Initial State: BLOCK",
        "盘点与分类",
        "M00 PREFLIGHT PASS",
        "M01-M06 verification",
        "VERIFIED",
        "Independent Study",
        "READY FOR USER GATE",
        "User disposition: ACCEPT / REVISE / STOP",
    )
    flow_drawing = Drawing(172 * mm, 101 * mm)
    node_width, node_height, left, top = 132 * mm, 8.5 * mm, 20 * mm, 91 * mm
    for index, label in enumerate(flow_labels):
        y = top - index * 11.5 * mm
        flow_drawing.add(
            Rect(
                left,
                y,
                node_width,
                node_height,
                rx=3 * mm,
                ry=3 * mm,
                strokeColor=line,
                fillColor=panel,
                strokeWidth=1.0,
            )
        )
        flow_drawing.add(
            String(
                left + node_width / 2,
                y + 2.8 * mm,
                label,
                fontName="ProjectTownFusionV7",
                fontSize=9,
                fillColor=ink,
                textAnchor="middle",
            )
        )
        if index < len(flow_labels) - 1:
            x = left + node_width / 2
            flow_drawing.add(
                Line(x, y, x, y - 2.2 * mm, strokeColor=line, strokeWidth=1.2)
            )
            flow_drawing.add(
                Polygon(
                    [
                        x - 2.0 * mm,
                        y - 1.4 * mm,
                        x + 2.0 * mm,
                        y - 1.4 * mm,
                        x,
                        y - 4.3 * mm,
                    ],
                    strokeColor=line,
                    fillColor=line,
                )
            )
    story += [flow_drawing, Paragraph("状态与边界", heading)]
    boundary_rows = [["Boundary", "Execution rule"]]
    for name in ("可重跑验证", "不可重跑历史证据", "用户持有的发布事项"):
        values = [item.removeprefix("- ").strip() for item in sections.get(name, [])]
        if not values:
            raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
        boundary_rows.append([name, " ".join(values)])
    # The matrix on page three is detailed; this compact block is the page-two
    # scan aid and intentionally keeps body text at the eight-point floor.
    story.append(table(boundary_rows, (35, 137)))
    story.append(Spacer(1, 3))
    for name in ("Independent Study 最小执行合同",):
        if name in sections:
            story.append(Paragraph(name, heading))
            for item in sections[name]:
                if item.startswith("-") or item[:1].isdigit():
                    story.append(para(item))
    story.append(PageBreak())
    story.append(Paragraph("Verification Matrix", heading))
    matrix = [
        x.strip("|").split("|")
        for x in sections["Verification Matrix"]
        if x.startswith("|")
    ]
    if len(matrix) < 3 or any(len(row) != 10 for row in matrix):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    clean_rows = [[z.strip() for z in row] for row in [matrix[0], *matrix[2:]]]
    if clean_rows[0] != [
        "ID",
        "Row Type",
        "Object",
        "Category",
        "Operation",
        "Check",
        "PASS",
        "Failure",
        "Owner",
        "Basis",
    ] or [row[0] for row in clean_rows[1:]] != [
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
    ]:
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    # Preserve all ten fields while splitting at the natural M04/M05 scan break.
    story.append(
        para(
            "Scan order: M00 preflight; M01-M06 verification; then M07/M08 User actions. "
            "Row type: Mandatory = Mandatory Verification; Conditional = Conditional Release Action; "
            "RP = Reviewer-defined verification policy.",
            cell,
        )
    )
    story.append(Spacer(1, 3))
    for chunk in (clean_rows[:6], [clean_rows[0], *clean_rows[6:]]):
        story.append(table(chunk, (7, 18, 20, 13, 25, 19, 18, 19, 25, 14)))
        story.append(Spacer(1, 4))
    story.append(PageBreak())
    for name in (
        "状态合同",
        "PASS/FAIL 标准",
        "角色与 User Gate",
        "引用处置",
        "引用",
        "离线边界",
    ):
        if name in sections:
            story.append(Paragraph(name, heading))
            for item in sections[name]:
                if item.startswith("-"):
                    story.append(para(item, cell))
    stream = BytesIO()
    SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=13 * mm,
        bottomMargin=14 * mm,
        title="ProjectTown runbook v8",
        author="ProjectTown",
        invariant=1,
    ).build(story, onFirstPage=_footer_v7, onLaterPages=_footer_v7)
    return stream.getvalue()


def runbook_v9_flow_geometry(
    *,
    node_count: int,
    page_width_mm: float = 172,
    node_width_mm: float = 132,
    node_height_mm: float = 10,
    gap_mm: float = 7,
    scale: float = 1,
    page_height_mm: float = 148,
) -> tuple[
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[float, float, float, float], ...],
]:
    """Return node and connector rectangles in mm; connectors stay outside nodes."""
    if (
        node_count < 1
        or min(
            page_width_mm,
            page_height_mm,
            node_width_mm,
            node_height_mm,
            gap_mm,
            scale,
        )
        <= 0
    ):
        raise ValueError("INVALID_FLOW_GEOMETRY")
    if node_width_mm + 12 > page_width_mm or gap_mm < 6:
        raise ValueError("INVALID_FLOW_GEOMETRY")
    width, height, gap = node_width_mm * scale, node_height_mm * scale, gap_mm * scale
    left, top = (page_width_mm - width) / 2, (node_count - 1) * (height + gap)
    nodes = tuple(
        (left, top - index * (height + gap), width, height)
        for index in range(node_count)
    )
    if any(
        x < 0 or y < 0 or x + width > page_width_mm or y + height > page_height_mm
        for x, y, width, height in nodes
    ):
        raise ValueError("INVALID_FLOW_GEOMETRY")
    # Connector rectangle is the line segment between node borders.  Its ends
    # are inset one millimetre from neither node: arrow heads remain outside.
    connectors = tuple(
        (
            left + width / 2,
            nodes[index][1] - 1.0 * scale,
            left + width / 2,
            nodes[index + 1][1] + height + 1.0 * scale,
        )
        for index in range(node_count - 1)
    )
    return nodes, connectors


def _footer_v8(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("ProjectTownFusionV8", 8)
    canvas.setFillColorRGB(0.094, 0.227, 0.353)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook v9｜User Gate 独占")
    canvas.drawRightString(548, 26, f"{document.page}")
    canvas.restoreState()


def _render_pdf_v8(result: ResultSession) -> bytes:
    """Content-measured v9 renderer; no forced page breaks or v8 mutation."""
    if (
        result.draft.future_parameters.generator_version
        != "deterministic-grounded-plan-v8"
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    if result.draft.task != (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    ):
        # v9 is a narrow T002 presentation refinement.  Other manifest plan
        # fixtures retain the established generic visual contract.
        return _render_pdf_v2(result)
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    pdfmetrics.registerFont(TTFont("ProjectTownFusionV8", str(font_path)))
    styles = getSampleStyleSheet()
    ink, panel, line = (
        colors.HexColor("#183A5A"),
        colors.HexColor("#F2F6F8"),
        colors.HexColor("#4B657A"),
    )
    body = ParagraphStyle(
        "V8Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV8",
        fontSize=8.5,
        leading=11,
    )
    cell = ParagraphStyle(
        "V8Cell", parent=body, fontSize=8, leading=9.7, splitLongWords=False
    )
    center = ParagraphStyle("V8Center", parent=cell, alignment=TA_CENTER)
    heading = ParagraphStyle(
        "V8Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV8",
        fontSize=11.7,
        leading=14,
        textColor=ink,
        spaceBefore=6,
        spaceAfter=4,
        keepWithNext=True,
    )
    title = ParagraphStyle(
        "V8Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV8",
        fontSize=17,
        leading=21,
        alignment=TA_CENTER,
        textColor=ink,
    )
    lines = result.preview_markdown.splitlines()
    sections: dict[str, list[str]] = {}
    current = ""
    for item in lines:
        if item.startswith("## "):
            current = item[3:]
            sections[current] = []
        elif current:
            sections[current].append(item)
    required = {
        "M00 Run Binding preflight",
        "Verification Matrix",
        "VERIFIED Criteria",
        "Citation Usage Audit",
    }
    if not required.issubset(sections):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")

    def para(value: str, style=body):
        return Paragraph(_clean(value.lstrip("- ")), style)

    def table(rows, widths, centered=(), *, compact=False):
        if sum(widths) > 172:
            raise RuntimeError("INVALID_RUNBOOK_TABLE_WIDTH")
        data = [
            [
                para(
                    str(value), center if row_index == 0 or column in centered else cell
                )
                for column, value in enumerate(row)
            ]
            for row_index, row in enumerate(rows)
        ]
        item = Table(
            data, colWidths=[width * mm for width in widths], repeatRows=1, splitByRow=1
        )
        padding = 0.8 if compact else 2
        item.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.55, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, line),
                    ("BACKGROUND", (0, 0), (-1, 0), panel),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), padding),
                    ("RIGHTPADDING", (0, 0), (-1, -1), padding),
                    ("TOPPADDING", (0, 0), (-1, -1), padding),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
                ]
            )
        )
        return item

    story = [
        Paragraph(
            next(
                (_clean(item[2:]) for item in lines if item.startswith("# ")),
                "ProjectTown runbook v9",
            ),
            title,
        ),
        Spacer(1, 4),
    ]
    summary = " ".join(sections.get("执行摘要", []))
    m00_status = next(
        (
            item.removeprefix("- status: ").strip()
            for item in sections["M00 Run Binding preflight"]
            if item.startswith("- status: ")
        ),
        "BINDING BLOCKED",
    )
    story += [
        _info_box(
            Paragraph("<b>执行摘要</b><br/>" + _clean(summary), body), panel, ink
        ),
        Paragraph("M00 Run Binding preflight", heading),
        _info_box(
            Paragraph(
                "<b>" + _clean(m00_status) + "</b><br/>"
                "Opaque refs have no repository resolver; field presence does not equal PREFLIGHT PASS.",
                cell,
            ),
            panel,
            ink,
        ),
        Spacer(1, 2),
    ]
    binding_by_group: dict[str, list[list[str]]] = {}
    for item in sections["M00 Run Binding preflight"]:
        if item.startswith("- ") and " | " in item and not item.startswith("- M00-"):
            group, binding_name, display_ref, *_ = [
                part.strip() for part in item[2:].split(" | ")
            ]
            binding_by_group.setdefault(group, []).append([binding_name, display_ref])
    for group in (
        "Input",
        "Historical Input",
        "Execution",
        "Fresh Output",
        "User Output",
    ):
        rows = binding_by_group.get(group)
        if rows:
            story += [
                Paragraph(f"Run Binding - {group}", heading),
                table(
                    [["Binding", "Opaque display reference/state"], *rows], (64, 108)
                ),
                Spacer(1, 3),
            ]
    checks = [["ID", "Check", "Actual result", "Evidence", "Outcome"]]
    for item in sections["M00 Run Binding preflight"]:
        if item.startswith("- M00-"):
            checks.append([part.strip() for part in item[2:].split(" | ")])
    story += [
        table(checks, (17, 46, 28, 60, 21), (0, 2, 4)),
        Paragraph("复验对象与证据盘点", heading),
    ]
    inv = [
        item.strip("|").split("|")
        for item in sections.get("复验对象与证据盘点", [])
        if item.startswith("|")
    ]
    if len(inv) >= 3:
        story.append(
            table(
                [[part.strip() for part in row] for row in [inv[0], *inv[2:]]],
                (16, 27, 28, 19, 18, 16, 18, 30),
                (0, 3, 4, 5, 6, 7),
            )
        )
    story += [Paragraph("执行语义与流程", heading)]
    labels = (
        "Initial State: BLOCK",
        "M00 preflight",
        "M01-M06 verification",
        "VERIFIED",
        "Independent Study",
        "READY FOR USER GATE",
        "User disposition",
        "Record next step",
    )
    nodes, connectors = runbook_v9_flow_geometry(node_count=len(labels))
    drawing = Drawing(172 * mm, 148 * mm)
    # Layer 1: connectors/arrows, Layer 2: nodes, Layer 3: labels.
    for _x1, y1, _x2, y2 in connectors:
        x = 86 * mm
        upper, lower = y1 * mm, y2 * mm
        drawing.add(Line(x, upper, x, lower, strokeColor=line, strokeWidth=1.1))
        drawing.add(
            Polygon(
                [
                    x - 1.8 * mm,
                    lower + 3.4 * mm,
                    x + 1.8 * mm,
                    lower + 3.4 * mm,
                    x,
                    lower,
                ],
                strokeColor=line,
                fillColor=line,
            )
        )
    for index, (x, y, width, height) in enumerate(nodes):
        drawing.add(
            Rect(
                x * mm,
                y * mm,
                width * mm,
                height * mm,
                rx=3 * mm,
                ry=3 * mm,
                strokeColor=line,
                fillColor=panel,
                strokeWidth=1.0,
            )
        )
    for index, (x, y, width, height) in enumerate(nodes):
        drawing.add(
            String(
                (x + width / 2) * mm,
                (y + 3.35) * mm,
                labels[index],
                fontName="ProjectTownFusionV8",
                fontSize=9.4,
                fillColor=ink,
                textAnchor="middle",
            )
        )
    story += [drawing, Paragraph("Verification Matrix", heading)]
    matrix = [
        item.strip("|").split("|")
        for item in sections["Verification Matrix"]
        if item.startswith("|")
    ]
    matrix = [row for row in matrix if len(row) == 10]
    if len(matrix) < 3:
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    matrix_rows = [[part.strip() for part in row] for row in [matrix[0], *matrix[2:]]]
    matrix_a = [[row[index] for index in (0, 1, 2, 3, 4, 9)] for row in matrix_rows]
    matrix_b = [[row[index] for index in (0, 5, 6, 7, 8)] for row in matrix_rows]
    for label, chunk, widths, centered in (
        (
            "Matrix A - action ownership",
            matrix_a,
            (12, 24, 25, 30, 53, 28),
            (0, 1, 2, 5),
        ),
        (
            "Matrix B - expected state and blocking",
            matrix_b,
            (12, 35, 35, 48, 42),
            (0,),
        ),
    ):
        story += [
            Paragraph(label, heading),
            table(chunk, widths, centered),
            Spacer(1, 4),
        ]
    for name in (
        "VERIFIED Criteria",
        "角色与 User Gate",
        "Citation Usage Audit",
        "引用",
        "离线边界",
    ):
        if name in sections:
            story.append(Paragraph(name, heading))
            if name == "Citation Usage Audit":
                audit = [
                    item.strip("|").split("|")
                    for item in sections[name]
                    if item.startswith("|")
                ]
                if len(audit) < 3 or any(len(row) != 5 for row in audit):
                    raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
                story.append(
                    table(
                        [
                            [value.strip() for value in row]
                            for row in [audit[0], *audit[2:]]
                        ],
                        (18, 30, 42, 34, 48),
                        (0,),
                        compact=True,
                    )
                )
            elif name == "引用":
                # Pair citations across the full printable width.  Keeping the
                # complete source span next to each ID preserves the audit
                # contract while preventing a long one-column citation index
                # from stranding the offline boundary on an orphan page.
                citation_entries: list[list[str]] = []
                for item in sections[name]:
                    if not item.startswith("- ["):
                        continue
                    match = re.match(r"- \[(S\d+)]\s+(.+?)\s+(\d+)-(\d+):", item)
                    if match is None:
                        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
                    citation_entries.append(
                        [
                            match.group(1),
                            f"{match.group(2).strip('`')}:{match.group(3)}-{match.group(4)}",
                        ]
                    )
                citation_rows = [
                    [
                        "S-ID",
                        "Repository-relative source span",
                        "S-ID",
                        "Repository-relative source span",
                    ]
                ]
                for index in range(0, len(citation_entries), 2):
                    left = citation_entries[index]
                    right = (
                        citation_entries[index + 1]
                        if index + 1 < len(citation_entries)
                        else ["", ""]
                    )
                    citation_rows.append([*left, *right])
                story.append(
                    table(citation_rows, (16, 70, 16, 70), (0, 2), compact=True)
                )
            else:
                story.extend(
                    para(item, cell) for item in sections[name] if item.startswith("-")
                )
    stream = BytesIO()
    SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=13 * mm,
        bottomMargin=14 * mm,
        title="ProjectTown runbook v9",
        author="ProjectTown",
        invariant=1,
    ).build(story, onFirstPage=_footer_v8, onLaterPages=_footer_v8)
    return stream.getvalue()


def runbook_v10_flow_geometry(
    *,
    node_width_mm: float = 42,
    node_height_mm: float = 10,
    gap_mm: float = 7,
    arrow_clearance_mm: float = 1.2,
) -> tuple[
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[tuple[float, float], ...], ...],
]:
    """Return v10 flow nodes/connectors with arrows outside every node.

    The first seven nodes are the mandatory vertical path.  The final three
    are the User branches.  Connector endpoints stop before node borders, so
    the arrow head cannot be hidden by a node fill or border.
    """
    if min(node_width_mm, node_height_mm, gap_mm, arrow_clearance_mm) <= 0:
        raise ValueError("INVALID_FLOW_GEOMETRY")
    if gap_mm < 6 or arrow_clearance_mm < 1:
        raise ValueError("INVALID_FLOW_GEOMETRY")
    x = 65.0
    top = 123.0
    nodes = tuple(
        (x, top - index * (node_height_mm + gap_mm), node_width_mm, node_height_mm)
        for index in range(7)
    ) + (
        (12.0, 4.0, node_width_mm, node_height_mm),
        (65.0, 4.0, node_width_mm, node_height_mm),
        (118.0, 4.0, node_width_mm, node_height_mm),
    )
    connectors = tuple(
        (
            (x + node_width_mm / 2, nodes[index][1] - arrow_clearance_mm),
            (
                x + node_width_mm / 2,
                nodes[index + 1][1] + node_height_mm + arrow_clearance_mm,
            ),
        )
        for index in range(6)
    ) + tuple(
        (
            (x + node_width_mm / 2, nodes[6][1] - arrow_clearance_mm),
            (
                x + node_width_mm / 2,
                nodes[7 + branch][1] + node_height_mm + gap_mm / 2,
            ),
            (
                branch_x + node_width_mm / 2,
                nodes[7 + branch][1] + node_height_mm + gap_mm / 2,
            ),
            (
                branch_x + node_width_mm / 2,
                nodes[7 + branch][1] + node_height_mm + arrow_clearance_mm,
            ),
        )
        for branch, branch_x in enumerate((12.0, 65.0, 118.0))
    )
    return nodes, connectors


def _footer_v9(canvas, document) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("ProjectTownFusionV9", 8)
    canvas.setFillColorRGB(0.094, 0.227, 0.353)
    canvas.drawString(46, 26, "ProjectTown｜离线确定性 runbook v10｜User Gate 独占")
    canvas.drawRightString(548, 26, f"{document.page}")
    canvas.restoreState()


def _render_pdf_v9(result: ResultSession) -> bytes:
    """Render the additive v10 T002 procedure in its mandated three pages."""
    if (
        result.draft.future_parameters.generator_version
        != "deterministic-grounded-plan-v9"
    ):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    if result.draft.task != (
        "制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项"
    ):
        return _render_pdf_v2(result)
    try:
        from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF_BACKEND_UNAVAILABLE") from error
    font_path = _font_path()
    if not font_path.is_file():
        raise RuntimeError("PDF_FONT_UNAVAILABLE")
    pdfmetrics.registerFont(TTFont("ProjectTownFusionV9", str(font_path)))
    styles = getSampleStyleSheet()
    ink, panel, line, accent = (
        colors.HexColor("#183A5A"),
        colors.HexColor("#F2F6F8"),
        colors.HexColor("#4B657A"),
        colors.HexColor("#A43D50"),
    )
    body = ParagraphStyle(
        "V9Body",
        parent=styles["BodyText"],
        fontName="ProjectTownFusionV9",
        fontSize=7.4,
        leading=9.0,
    )
    tiny = ParagraphStyle(
        "V9Tiny", parent=body, fontSize=6.2, leading=7.4, splitLongWords=False
    )
    binding_cell = ParagraphStyle(
        "V9Binding", parent=tiny, fontSize=6.0, leading=6.5, splitLongWords=False
    )
    m00_cell = ParagraphStyle(
        "V9M00", parent=tiny, fontSize=5.8, leading=6.5, splitLongWords=False
    )
    mono = ParagraphStyle(
        "V9Mono",
        parent=tiny,
        fontName="Courier",
        fontSize=5.8,
        leading=7.0,
        splitLongWords=False,
    )
    center = ParagraphStyle("V9Center", parent=tiny, alignment=TA_CENTER)
    heading = ParagraphStyle(
        "V9Heading",
        parent=styles["Heading2"],
        fontName="ProjectTownFusionV9",
        fontSize=10.5,
        leading=12.5,
        textColor=ink,
        spaceBefore=3,
        spaceAfter=2,
        keepWithNext=True,
    )
    title = ParagraphStyle(
        "V9Title",
        parent=styles["Title"],
        fontName="ProjectTownFusionV9",
        fontSize=14,
        leading=17,
        alignment=TA_CENTER,
        textColor=ink,
    )
    lines = result.preview_markdown.splitlines()
    sections: dict[str, list[str]] = {}
    current = ""
    for item in lines:
        if item.startswith("## "):
            current = item[3:]
            sections[current] = []
        elif current:
            sections[current].append(item)
    required = {
        "Run Binding",
        "M00 Run Binding preflight",
        "Verification Matrix",
        "Citation Usage Audit",
    }
    if not required.issubset(sections):
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")

    def para(value: str, style=body):
        cleaned = _clean(value.lstrip("- "))
        selected = (
            mono if ("\\" in value or ":/" in value or "PATH_REF" in value) else style
        )
        return Paragraph(cleaned, selected)

    def breakable(value: str) -> str:
        """Escape a guarded local value with deterministic separator-only wraps."""
        return escape(value).replace("; ", ";<br/>")

    def path_breaks(value: str) -> str:
        separator = "\\" if "\\" in value else "/"
        parts = value.replace("/", "\\").split("\\")
        if len(parts) < 4:
            return escape(value)
        split_at = max(2, len(parts) // 2)
        return (
            escape(separator.join(parts[:split_at]))
            + escape(separator)
            + "<br/>"
            + escape(separator.join(parts[split_at:]))
        )

    def table(rows, widths, centered=(), *, compact=False):
        if sum(widths) > 172:
            raise RuntimeError("INVALID_RUNBOOK_TABLE_WIDTH")
        data = [
            [
                para(str(value), center if index == 0 or column in centered else tiny)
                for column, value in enumerate(row)
            ]
            for index, row in enumerate(rows)
        ]
        item = Table(
            data, colWidths=[width * mm for width in widths], repeatRows=1, splitByRow=1
        )
        pad = 0.45 if compact else 0.8
        item.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.45, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.2, line),
                    ("BACKGROUND", (0, 0), (-1, 0), panel),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), pad),
                    ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                    ("TOPPADDING", (0, 0), (-1, -1), pad),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
                ]
            )
        )
        return item

    def markdown_table(name: str, count: int) -> list[list[str]]:
        rows = [
            item.strip("|").split("|")
            for item in sections[name]
            if item.startswith("|")
        ]
        rows = [row for row in rows if len(row) == count]
        if len(rows) < 3:
            raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
        cleaned = [[part.strip() for part in row] for row in rows]

        def separator(row: list[str]) -> bool:
            return all(
                set(cell.replace(" ", "")) <= {"-", ":"} and "-" in cell for cell in row
            )

        return [cleaned[0], *(row for row in cleaned[1:] if not separator(row))]

    from .material_workflow import _v10_bindings

    declared_bindings = _v10_bindings(result.draft)
    if not declared_bindings:
        raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
    binding = markdown_table("Run Binding", 7)
    binding[0][3] = "Canonical declared value (PDF only)"
    for row in binding[1:]:
        key = row[1]
        if key not in declared_bindings:
            raise RuntimeError("INVALID_RUNBOOK_STRUCTURE")
        row[3] = declared_bindings[key]
    binding_display = [
        ["Group / binding", "Canonical declared value", "Type / access / state"]
    ]
    for row in binding[1:]:
        binding_display.append(
            [
                f"{escape(row[0])} • <b>{escape(row[1])}</b>",
                path_breaks(row[3]),
                f"{escape(row[4])} • {escape(row[5])} • {escape(row[6])}",
            ]
        )
    m00 = markdown_table("M00 Run Binding preflight", 12)
    inventory = markdown_table("复验对象与证据盘点", 8)
    matrix = markdown_table("Verification Matrix", 11)
    audit = markdown_table("Citation Usage Audit", 5)
    story = [
        Paragraph(
            next(
                (_clean(item[2:]) for item in lines if item.startswith("# ")),
                "ProjectTown runbook v10",
            ),
            title,
        ),
        Spacer(1, 3),
    ]
    story.extend(
        [
            Paragraph("Binding and M00 preflight", heading),
            Paragraph(
                "<b>Initial State: BLOCK</b> — BOUND-UNVALIDATED until M00 records actual evidence. "
                "LOCAL-ONLY — canonical paths are disclosed only in this guarded v10 PDF",
                tiny,
            ),
            Table(
                [
                    [
                        Paragraph(value, center if row_index == 0 else binding_cell)
                        for value in row
                    ]
                    for row_index, row in enumerate(binding_display)
                ],
                colWidths=[25 * mm, 120 * mm, 27 * mm],
                repeatRows=1,
                splitByRow=1,
                style=TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.45, line),
                        ("INNERGRID", (0, 0), (-1, -1), 0.2, line),
                        ("BACKGROUND", (0, 0), (-1, 0), panel),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0.6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0.6),
                        ("TOPPADDING", (0, 0), (-1, -1), 0.45),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.45),
                    ]
                ),
            ),
            Paragraph("M00-01 to M00-07 — configuration / result", heading),
        ]
    )
    m00_names = (
        "Check",
        "PASS",
        "Expected",
        "Actual",
        "Resolved path",
        "Command",
        "Exit",
        "Verifier",
        "Timestamp",
        "Evidence",
        "Outcome",
    )
    m00_declared_paths = {
        "M00-01": "see binding catalog",
        "M00-02": declared_bindings["candidate_path"],
        "M00-03": declared_bindings["preview_record_path"],
        "M00-04": "; ".join(
            (
                declared_bindings["historical_result_json_path"],
                declared_bindings["approved_provenance_tuple_source"],
            )
        ),
        "M00-05": declared_bindings["fresh_result_output_path"],
        "M00-06": "; ".join(
            (
                declared_bindings["working_directory"],
                declared_bindings["material_source_root"],
            )
        ),
        "M00-07": declared_bindings["candidate_path"],
    }

    def m00_value(name: str, value: str) -> str:
        if name != "Resolved path":
            return breakable(value)
        return ";<br/>".join(path_breaks(part.strip()) for part in value.split(";"))

    m00_cards = []
    for row in m00[1:]:
        fields = dict(zip(m00_names, row[1:], strict=True))
        fields["Resolved path"] = m00_declared_paths[row[0]]
        detail = " • ".join(
            f"<b>{name}</b>: {m00_value(name, fields[name])}" for name in m00_names
        )
        m00_cards.append(
            Table(
                [[Paragraph(escape(row[0]), center), Paragraph(detail, m00_cell)]],
                colWidths=[16 * mm, 156 * mm],
                style=TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.35, line),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (0, -1), panel),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0.7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0.7),
                        ("TOPPADDING", (0, 0), (-1, -1), 0.55),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.55),
                    ]
                ),
            )
        )
    story += [
        *m00_cards,
        PageBreak(),
        Paragraph("Inventory, flow and User Gate", heading),
        table(
            inventory,
            (14, 31, 31, 16, 17, 20, 18, 25),
            (0, 3, 4, 5, 6, 7),
            compact=True,
        ),
        Paragraph("State flow", heading),
    ]
    labels = (
        "Initial BLOCK",
        "M00 Preflight",
        "M01-M06",
        "VERIFIED",
        "Independent Study",
        "READY FOR USER GATE",
        "User disposition",
        "ACCEPT / RETAIN",
        "REVISE",
        "DISCARD / STOP",
    )
    nodes, connectors = runbook_v10_flow_geometry()
    drawing = Drawing(172 * mm, 140 * mm)
    # Connectors first, nodes second, labels last: arrowheads are never occluded.
    for connector in connectors:
        for (x1, y1), (x2, y2) in pairwise(connector):
            drawing.add(
                Line(
                    x1 * mm,
                    y1 * mm,
                    x2 * mm,
                    y2 * mm,
                    strokeColor=line,
                    strokeWidth=1.05,
                )
            )
        x2, y2 = connector[-1]
        drawing.add(
            Polygon(
                [
                    x2 * mm - 1.6 * mm,
                    y2 * mm + 3 * mm,
                    x2 * mm + 1.6 * mm,
                    y2 * mm + 3 * mm,
                    x2 * mm,
                    y2 * mm,
                ],
                strokeColor=line,
                fillColor=line,
            )
        )
    for index, (x, y, width, height) in enumerate(nodes):
        fill = panel if index < 7 else colors.HexColor("#FFF6F2")
        drawing.add(
            Rect(
                x * mm,
                y * mm,
                width * mm,
                height * mm,
                rx=2.2 * mm,
                ry=2.2 * mm,
                strokeColor=accent if index >= 7 else line,
                fillColor=fill,
                strokeWidth=0.9,
            )
        )
        drawing.add(
            String(
                (x + width / 2) * mm,
                (y + 3.25) * mm,
                labels[index],
                fontName="ProjectTownFusionV9",
                fontSize=7.9,
                fillColor=ink,
                textAnchor="middle",
            )
        )
    story += [drawing, Paragraph("Independent Study / M07 / M08", heading)]
    for section in ("Independent Study 最小执行合同", "M07/M08 状态机"):
        story.extend(
            para(item, tiny)
            for item in sections.get(section, [])
            if item.startswith("-")
        )
    story += [
        Paragraph("Responsibility boundary", heading),
        para(
            "Verifier owns engineering checks and records; Independent Study Reviewer rates but does not modify the candidate; User alone owns disposition and release authorization.",
            tiny,
        ),
        PageBreak(),
        Paragraph("Verification Matrix", heading),
    ]
    matrix_a = [[row[index] for index in (0, 1, 2, 3, 4, 9, 10)] for row in matrix]
    matrix_b = [[row[index] for index in (0, 5, 6, 7, 8)] for row in matrix]
    story += [
        Paragraph("Matrix A — action ownership", heading),
        table(matrix_a, (11, 22, 18, 26, 45, 25, 25), (0, 1, 2, 5), compact=True),
        Paragraph("Matrix B — expected state and blocking", heading),
        table(matrix_b, (11, 35, 35, 48, 43), (0,), compact=True),
        Paragraph("VERIFIED Criteria", heading),
    ]
    story.extend(
        para(item, tiny)
        for item in sections.get("VERIFIED Criteria", [])
        if item.startswith("-")
    )
    story += [
        Paragraph("Citation Usage Audit", heading),
        table(audit, (15, 23, 61, 34, 39), (0,), compact=True),
        Paragraph("Citation index", heading),
    ]
    citations = []
    for item in sections.get("引用", []):
        match = re.match(r"- \[(S\d+)]\s+(.+?)\s+(\d+)-(\d+):", item)
        if match:
            citations.append(
                [
                    match.group(1),
                    f"{match.group(2).strip('`')}:{match.group(3)}-{match.group(4)}",
                ]
            )
    citation_rows = [["ID", "Repository-relative source span"], *citations]
    story += [
        table(citation_rows, (18, 154), (0,), compact=True),
        Paragraph("Offline boundary", heading),
    ]
    story.extend(
        para(item, tiny)
        for item in sections.get("离线边界", [])
        if item.startswith("-")
    )
    stream = BytesIO()
    SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=8 * mm,
        bottomMargin=10 * mm,
        title="ProjectTown runbook v10",
        author="ProjectTown",
        invariant=1,
    ).build(story, onFirstPage=_footer_v9, onLaterPages=_footer_v9)
    return stream.getvalue()
