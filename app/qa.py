from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from app.render import strip_code_fence
from app.siliconflow import SiliconFlowClient, SiliconFlowError


LogFn = Callable[[str], None]
BLOCKING_SEVERITIES = {"high", "critical", "medium"}
VISUAL_BATCH_SIZE = 4


def _json_object(text: str) -> dict:
    value = strip_code_fence(text)
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        value = value[start : end + 1]
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("模型没有返回 JSON 对象")
    return data


def _normalise_issue(item: dict, default_slide: int | None = None) -> dict:
    issue_type = str(item.get("type") or item.get("category") or "other").strip()
    severity = str(item.get("severity") or "medium").strip().lower()
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "medium"
    hard_layout_types = {
        "whitespace",
        "whitespace_imbalance",
        "bottom_whitespace",
        "imbalance",
        "unbalanced",
        "column_imbalance",
        "misalignment",
    }
    if issue_type.lower() in hard_layout_types and severity == "low":
        severity = "medium"
    try:
        slide = int(item.get("slide") or item.get("slide_number") or default_slide or 0)
    except (TypeError, ValueError):
        slide = default_slide or 0
    patch = item.get("patch")
    return {
        "slide": slide,
        "type": issue_type,
        "severity": severity,
        "detail": str(item.get("detail") or item.get("description") or "").strip(),
        "patch": patch if isinstance(patch, dict) else {},
    }


def _normalise_review(data: dict, default_slide: int | None = None) -> dict:
    issues = []
    for item in data.get("issues") or []:
        if isinstance(item, dict):
            issue = _normalise_issue(item, default_slide)
            if issue["detail"]:
                issues.append(issue)
    blocking = any(issue["severity"] in BLOCKING_SEVERITIES for issue in issues)
    return {"pass": not blocking, "issues": issues}


def extract_pptx_text(pptx_path: Path) -> list[dict]:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("缺少 python-pptx，请先安装: pip install python-pptx") from exc

    presentation = Presentation(pptx_path)
    result = []
    for slide_number, slide in enumerate(presentation.slides, 1):
        text_parts = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = shape.text.strip()
                if text:
                    text_parts.append(text)
        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        result.append(
            {
                "slide": slide_number,
                "text": "\n".join(text_parts),
                "notes": notes,
            }
        )
    return result


def content_review(
    client: SiliconFlowClient,
    source_text: str,
    pptx_path: Path,
) -> dict:
    slide_text = extract_pptx_text(pptx_path)
    system = (
        "你是学术幻灯片内容 QA 审查员。对照论文原文检查 PPT 文本，重点找数字无来源、"
        "事实编造、与原文相反、遗漏关键限定条件、把摘要整段照搬等问题。"
        "只输出 JSON 对象：{\"issues\":[{\"slide\":2,\"severity\":\"high\","
        "\"type\":\"numeric_error\",\"detail\":\"...\"}]}。"
        "只有 factual/numeric error 才标 high 或 medium；措辞、风格、轻微遗漏标 low，"
        "不要因为表达偏好标 blocking。没有问题返回空 issues。"
    )
    user = f"""幻灯片实际文本：
{json.dumps(slide_text, ensure_ascii=False, indent=2)}

论文原文：
{source_text[:45000]}
"""
    raw = client.chat(system, user, max_tokens=3000)
    return _normalise_review(_json_object(raw))


def _natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.stem)]


def _powerpoint_export(pptx_path: Path, out_dir: Path) -> list[Path]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    presentation = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = app.Presentations.Open(
            str(pptx_path.resolve()), True, False, False
        )
        presentation.Export(str(out_dir.resolve()), "PNG", 1600, 900)
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    return sorted(
        [path for path in out_dir.iterdir() if path.suffix.lower() in {".png", ".jpg"}],
        key=_natural_key,
    )


def _libreoffice_export(pptx_path: Path, out_dir: Path) -> list[Path]:
    executable = next(
        (
            path
            for name in ("soffice", "libreoffice")
            if (path := shutil.which(name))
        ),
        None,
    )
    if not executable:
        return []
    pdf_dir = out_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_dir),
            str(pptx_path),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=True,
    )
    pdf_path = pdf_dir / f"{pptx_path.stem}.pdf"
    if not pdf_path.is_file():
        return []
    import pymupdf

    document = pymupdf.open(pdf_path)
    images = []
    try:
        for page_number, page in enumerate(document, 1):
            output = out_dir / f"slide-{page_number:02d}.png"
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            pixmap.save(output)
            images.append(output)
    finally:
        document.close()
    return images


def export_pptx_images(pptx_path: Path, out_dir: Path, log: LogFn) -> tuple[list[Path], str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        try:
            images = _powerpoint_export(pptx_path, out_dir)
            if images:
                return images, "powerpoint"
        except Exception as exc:
            log(f"PowerPoint 导出预览失败，尝试 LibreOffice：{str(exc)[:220]}")
    try:
        images = _libreoffice_export(pptx_path, out_dir)
        if images:
            return images, "libreoffice"
    except Exception as exc:
        log(f"LibreOffice 导出预览失败：{str(exc)[:220]}")
    log("未找到可用的 PPTX 转图工具，视觉 QA 无法执行")
    return [], "unavailable"


def visual_review(
    client: SiliconFlowClient,
    images: list[Path],
    spec: dict,
) -> dict:
    slides = spec.get("slides") or []
    labels = []
    for index in range(len(images)):
        if index == 0:
            labels.append("封面")
        elif index - 1 < len(slides):
            labels.append(f"内容页 {index}：{slides[index - 1].get('title') or ''}")
        else:
            labels.append(f"幻灯片 {index + 1}")

    system = (
        "你是严格的幻灯片视觉 QA 审查员。检查文字遮挡/重叠、溢出、边缘裁切、"
        "低对比度、文字框过窄、元素碰撞、明显留白、列不平衡、对齐和边距问题。"
        "只输出 JSON：{\"issues\":[{\"slide\":2,\"severity\":\"high\","
        "\"type\":\"text_overflow\",\"detail\":\"...\",\"patch\":"
        "{\"font_scale\":0.9,\"title_scale\":0.95,\"image_scale\":0.9,"
        "\"line_spacing\":1.3,\"layout\":\"image_right\"}}]}。slide 使用整份演示中的全局页码，封面为 1。"
        "patch 可省略；可用字段仅限 font_scale、title_scale、image_scale、line_spacing、layout，"
        "layout 仅为 text、image_right、image_left。"
        "只有会明显影响阅读的问题标 high/medium；轻微审美问题标 low。"
        "可见的大面积留白、列不平衡和明显对齐错误不属于轻微审美问题，至少标 medium；"
        "每页都要检查正文下方三分之一是否出现无意义空置。"
        "没有 blocking 问题时返回空 issues。"
    )
    all_issues = []
    for start in range(0, len(images), VISUAL_BATCH_SIZE):
        batch = images[start : start + VISUAL_BATCH_SIZE]
        expected = "\n".join(
            f"图片 {offset + 1} = 幻灯片 {start + offset + 1}：{labels[start + offset]}"
            for offset in range(len(batch))
        )
        user = f"""请逐张审查以下图片，并报告所有 blocking 问题。
{expected}
"""
        raw = client.chat_vision(system, user, batch, max_tokens=2600)
        review = _normalise_review(_json_object(raw), start + 1)
        all_issues.extend(review["issues"])
    blocking = any(issue["severity"] in BLOCKING_SEVERITIES for issue in all_issues)
    return {"pass": not blocking, "issues": all_issues}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def apply_visual_fixes(spec: dict, issues: list[dict]) -> int:
    slides = spec.get("slides") or []
    cover = spec.setdefault("cover", {})
    changed = 0
    for issue in issues:
        if issue.get("severity") not in BLOCKING_SEVERITIES:
            continue
        try:
            slide_number = int(issue.get("slide") or 0)
        except (TypeError, ValueError):
            continue
        patch = issue.get("patch") or {}
        target = cover if slide_number == 1 else (
            slides[slide_number - 2] if 2 <= slide_number <= len(slides) + 1 else None
        )
        if target is None:
            continue

        before = json.dumps(target, sort_keys=True, ensure_ascii=False)
        if "font_scale" in patch:
            try:
                target["font_scale"] = _clamp(float(patch["font_scale"]), 0.68, 1.5)
            except (TypeError, ValueError):
                pass
        if "title_scale" in patch:
            try:
                target["title_scale"] = _clamp(float(patch["title_scale"]), 0.72, 1.05)
            except (TypeError, ValueError):
                pass
        if "image_scale" in patch:
            try:
                target["image_scale"] = _clamp(float(patch["image_scale"]), 0.68, 1.05)
            except (TypeError, ValueError):
                pass
        if "line_spacing" in patch:
            try:
                target["line_spacing"] = _clamp(float(patch["line_spacing"]), 1.05, 1.7)
            except (TypeError, ValueError):
                pass
        layout = str(patch.get("layout") or "").strip()
        if layout in {"text", "image_right", "image_left"} and slide_number > 1:
            target["layout"] = layout

        if before == json.dumps(target, sort_keys=True, ensure_ascii=False):
            issue_type = str(issue.get("type") or "").lower()
            if any(
                token in issue_type
                for token in ("overflow", "overlap", "edge_clip", "narrow", "word_break")
            ):
                target["font_scale"] = _clamp(float(target.get("font_scale", 1.0)) * 0.9, 0.68, 1.5)
            elif "title" in issue_type and any(
                token in issue_type for token in ("wrap", "break", "overflow")
            ):
                target["title_scale"] = _clamp(float(target.get("title_scale", 1.0)) * 0.9, 0.72, 1.05)
            elif any(
                token in issue_type
                for token in ("chart", "image_too_small", "small_image", "illegible_chart")
            ) and slide_number > 1:
                target["image_scale"] = _clamp(float(target.get("image_scale", 1.0)) * 1.07, 0.68, 1.05)
            elif "small_text" in issue_type and target.get("figure"):
                target["image_scale"] = _clamp(float(target.get("image_scale", 1.0)) * 1.07, 0.68, 1.05)
            elif any(
                token in issue_type
                for token in ("whitespace", "blank", "imbalance", "unbalanced", "sparse")
            ):
                target["font_scale"] = _clamp(float(target.get("font_scale", 1.0)) * 1.1, 0.68, 1.5)
                target["line_spacing"] = _clamp(float(target.get("line_spacing", 1.15)) + 0.15, 1.05, 1.7)
                if slide_number > 1 and target.get("figure"):
                    target["image_scale"] = _clamp(float(target.get("image_scale", 1.0)) * 1.05, 0.68, 1.05)
            else:
                target["font_scale"] = _clamp(float(target.get("font_scale", 1.0)) * 0.95, 0.68, 1.5)
        if before != json.dumps(target, sort_keys=True, ensure_ascii=False):
            changed += 1
    return changed
