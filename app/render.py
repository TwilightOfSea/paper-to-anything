from __future__ import annotations

import html
import io
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


WECHAT_COVER_SIZE = (900, 383)
POSTER_SIZE = (2400, 1600)
XHS_COVER_SIZE = (1080, 1440)

POSTER_PALETTES = {
    "自动匹配": {
        "ink": "#17252A",
        "paper": "#FFFDF7",
        "card": "#FFFFFF",
        "accent": "#D95D39",
        "secondary": "#2A9D8F",
        "soft": "#F2E9DA",
    },
    "学术简约": {
        "ink": "#172033",
        "paper": "#F7F8FA",
        "card": "#FFFFFF",
        "accent": "#2F5D8A",
        "secondary": "#7A8FA6",
        "soft": "#E8EEF4",
    },
    "数据叙事": {
        "ink": "#20242B",
        "paper": "#F8F5EF",
        "card": "#FFFFFF",
        "accent": "#C14924",
        "secondary": "#3C7A75",
        "soft": "#EEE4D6",
    },
}


def markdown_to_html(markdown_text: str) -> str:
    import markdown

    return markdown.markdown(
        markdown_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )


def strip_code_fence(text: str) -> str:
    value = (text or "").strip()
    match = re.match(r"^```(?:html|json|markdown|md)?\s*(.*?)\s*```$", value, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def write_wechat_html(title: str, digest: str, body_html: str, out_path: Path) -> None:
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
</head>
<body style="margin:0;background:#f5f6f8;color:#252525;font-family:'Microsoft YaHei','PingFang SC',sans-serif;">
<main style="max-width:760px;margin:0 auto;padding:32px 20px;background:#fff;line-height:1.85;font-size:16px;">
<h1 style="font-size:28px;line-height:1.35;margin:0 0 12px;color:#111827;">{_escape(title)}</h1>
<p style="color:#6b7280;margin:0 0 28px;font-size:14px;">{_escape(digest)}</p>
{body_html}
</main>
</body>
</html>
"""
    out_path.write_text(doc, encoding="utf-8")


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _font_path() -> str | None:
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "simhei.ttf",
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    return next((str(path) for path in candidates if path.exists()), None)


def _fit_font(draw, text: str, max_width: int, max_height: int, start: int = 54) -> tuple:
    font_path = _font_path()
    size = start
    while size >= 20:
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        words = list(re.finditer(r"[A-Za-z0-9]+|.", text))
        lines: list[str] = []
        current = ""
        for match in words:
            unit = match.group(0)
            candidate = current + unit
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = "" if unit.isspace() else unit
        if current:
            lines.append(current)
        line_height = int(size * 1.3)
        if line_height * len(lines) <= max_height:
            return font, lines, line_height
        size -= 2
    font = ImageFont.truetype(font_path, 20) if font_path else ImageFont.load_default()
    return font, [text[max(0, len(text) - 18) :]], 26


def _draw_cover_title(canvas: Image.Image, title: str) -> Image.Image:
    draw = ImageDraw.Draw(canvas)
    font, lines, line_height = _fit_font(draw, title, 500, 180, 48)
    x, y = 34, 34
    for line in lines:
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255),
            stroke_width=2,
            stroke_fill=(12, 22, 34),
        )
        y += line_height
    return canvas


def build_cover(title: str, image_bytes: bytes | None, fallback_image: Path | None, out_path: Path) -> str:
    target_w, target_h = WECHAT_COVER_SIZE
    if image_bytes:
        from app.siliconflow import bytes_to_image

        source = bytes_to_image(image_bytes).convert("RGB")
        scale = max(target_w / source.width, target_h / source.height)
        resized = source.resize((int(source.width * scale), int(source.height * scale)), Image.LANCZOS)
        left = (resized.width - target_w) // 2
        top = (resized.height - target_h) // 2
        canvas = resized.crop((left, top, left + target_w, top + target_h))
        canvas = _draw_cover_title(canvas, title)
        canvas.save(out_path, "JPEG", quality=92)
        return "ai_generated"

    if fallback_image and fallback_image.exists():
        try:
            canvas = Image.new("RGB", (target_w, target_h), (24, 42, 58))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([0, 0, 9, target_h], fill=(72, 170, 190))
            font, lines, line_height = _fit_font(draw, title, 330, target_h - 56, 42)
            y = (target_h - line_height * len(lines)) // 2
            for line in lines:
                draw.text((30, y), line, font=font, fill=(255, 255, 255))
                y += line_height
            source = Image.open(fallback_image).convert("RGB")
            box = (390, 32, target_w - 30, target_h - 32)
            area_w, area_h = box[2] - box[0], box[3] - box[1]
            scale = min(area_w / source.width, area_h / source.height)
            resized = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))), Image.LANCZOS)
            canvas.paste(resized, (box[0] + (area_w - resized.width) // 2, box[1] + (area_h - resized.height) // 2))
            canvas.save(out_path, "JPEG", quality=92)
            return "paper_figure_composed"
        except Exception:
            pass

    canvas = Image.new("RGB", (target_w, target_h), (20, 35, 50))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, 9, target_h], fill=(72, 170, 190))
    font, lines, line_height = _fit_font(draw, title, target_w - 80, target_h - 70, 48)
    y = (target_h - line_height * len(lines)) // 2
    for line in lines:
        draw.text((40, y), line, font=font, fill=(255, 255, 255))
        y += line_height
    canvas.save(out_path, "JPEG", quality=92)
    return "local_text"


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _bounded_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return default


def _trim_white_border(image: Image.Image) -> Image.Image:
    grayscale = ImageOps.grayscale(image)
    mask = ImageOps.invert(grayscale).point(lambda value: 255 if value > 18 else 0)
    box = mask.getbbox()
    if not box:
        return image
    padding = max(6, int(min(image.width, image.height) * 0.012))
    box = (
        max(0, box[0] - padding),
        max(0, box[1] - padding),
        min(image.width, box[2] + padding),
        min(image.height, box[3] + padding),
    )
    if (box[2] - box[0]) * (box[3] - box[1]) >= image.width * image.height * 0.98:
        return image
    return image.crop(box)


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    units = list(re.finditer(r"[A-Za-z0-9][A-Za-z0-9._/:+-]*|.", text or ""))
    no_start = "”’）)】》」』〉］｝，。、；：？！…·"
    lines: list[str] = []
    current = ""
    for match in units:
        unit = match.group(0)
        candidate = current + unit
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width or not current:
            current = candidate
        elif unit in no_start:
            current += unit
        else:
            lines.append(current)
            current = "" if unit.isspace() else unit
    if current:
        lines.append(current)
    return lines or [""]


def _fit_bullets(
    draw,
    bullets: list[str],
    max_width: int,
    max_height: int,
    start_size: int = 28,
    min_size: int = 18,
) -> tuple[object, list[tuple[str, bool]], int]:
    font_path = _font_path()
    for size in range(start_size, min_size - 1, -2):
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        rendered: list[tuple[str, bool]] = []
        line_height = int(size * 1.32)
        for bullet in bullets:
            lines = _wrap_text(draw, bullet, font, max_width - 30)
            for index, line in enumerate(lines):
                rendered.append((line, index == 0))
            rendered.append(("", False))
        if line_height * len(rendered) <= max_height:
            return font, rendered, line_height

    font = ImageFont.truetype(font_path, min_size) if font_path else ImageFont.load_default()
    line_height = int(min_size * 1.32)
    rendered = []
    max_lines = max(1, max_height // line_height)
    for bullet in bullets:
        for index, line in enumerate(_wrap_text(draw, bullet, font, max_width - 30)):
            if len(rendered) >= max_lines:
                break
            rendered.append((line, index == 0))
        if len(rendered) >= max_lines:
            break
    if rendered:
        text, first = rendered[-1]
        rendered[-1] = (text.rstrip(" .;:") + "…" if len(text) > 8 else text + "…", first)
    return font, rendered, line_height


def _draw_bullets(draw, x: int, y: int, bullets: list[str], font, rendered, line_height: int, color) -> None:
    cursor = y
    for line, first in rendered:
        if not line:
            cursor += max(6, line_height // 3)
            continue
        if first:
            draw.ellipse([x, cursor + line_height // 2 - 4, x + 8, cursor + line_height // 2 + 4], fill=color)
        draw.text((x + 24, cursor), line, font=font, fill=color)
        cursor += line_height


def _section_image(section: dict, images: list[dict], base_dir: Path) -> tuple[Path, str] | None:
    index = section.get("figure")
    if not isinstance(index, int) or not 1 <= index <= len(images):
        return None
    item = images[index - 1]
    path = base_dir / str(item.get("relative") or "")
    return (path, str(item.get("caption") or "")) if path.is_file() else None


def write_poster_html(spec: dict, images: list[dict], out_dir: Path) -> None:
    palette = POSTER_PALETTES.get(str(spec.get("style") or ""), POSTER_PALETTES["自动匹配"])
    cards = []
    for section in spec.get("sections") or []:
        bullets = "".join(f"<li>{html.escape(str(item))}</li>" for item in section.get("bullets") or [])
        image_html = ""
        found = _section_image(section, images, out_dir)
        if found:
            relative = images[int(section["figure"]) - 1]["relative"]
            caption = html.escape(str(images[int(section["figure"]) - 1].get("caption") or ""))
            image_html = (
                f'<figure><img src="{html.escape(relative)}" alt="{caption or "论文配图"}">'
                f"<figcaption>{caption}</figcaption></figure>"
            )
        cards.append(
            '<section class="card">'
            f'<h2>{html.escape(str(section.get("heading") or ""))}</h2>'
            f"{image_html}<ul>{bullets}</ul></section>"
        )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(str(spec.get("title") or "学术海报"))}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:{palette['soft']};color:{palette['ink']};font-family:'Microsoft YaHei','PingFang SC',sans-serif}}
.poster{{width:min(100%,2400px);min-height:1600px;margin:0 auto;padding:58px;background:{palette['paper']};display:flex;flex-direction:column;gap:26px}}
header{{border-bottom:8px solid {palette['accent']};padding:4px 8px 24px}}
h1{{margin:0 0 12px;font-size:66px;line-height:1.1}}
.subtitle{{font-size:30px;line-height:1.35;max-width:2200px}}
.authors{{margin-top:12px;color:#5f6b70;font-size:21px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:26px;flex:1}}
.card{{background:{palette['card']};border-top:7px solid {palette['secondary']};border-radius:8px;padding:24px 26px;overflow:hidden}}
.card:nth-child(3n+2){{border-top-color:{palette['accent']}}}
h2{{margin:0 0 18px;font-size:32px;line-height:1.22;color:{palette['ink']}}}
ul{{margin:0;padding-left:24px;font-size:25px;line-height:1.48}}
li{{margin:0 0 13px}}
figure{{margin:0 0 18px;text-align:center}}
img{{display:block;max-width:100%;max-height:280px;width:auto;height:auto;margin:0 auto;object-fit:contain}}
figcaption{{margin-top:8px;font-size:16px;line-height:1.3;color:#677277}}
footer{{border-top:3px solid {palette['accent']};padding:18px 8px 0;font-size:29px;line-height:1.4;font-weight:700}}
@media(max-width:900px){{.poster{{min-height:0;padding:24px}}.grid{{grid-template-columns:1fr}}h1{{font-size:42px}}}}
</style>
</head>
<body>
<main class="poster">
<header>
<h1>{html.escape(str(spec.get("title") or ""))}</h1>
<div class="subtitle">{html.escape(str(spec.get("subtitle") or ""))}</div>
<div class="authors">{html.escape(str(spec.get("authors") or ""))}</div>
</header>
<div class="grid">{''.join(cards)}</div>
<footer>{html.escape(str(spec.get("takeaway") or ""))}</footer>
</main>
</body>
</html>
"""
    (out_dir / "poster.html").write_text(document, encoding="utf-8")


def _paste_image_contain(canvas: Image.Image, source: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width, height = max(1, right - left), max(1, bottom - top)
    scale = min(width / source.width, height / source.height)
    size = (max(1, int(source.width * scale)), max(1, int(source.height * scale)))
    resized = source.resize(size, Image.LANCZOS)
    x = left + (width - resized.width) // 2
    y = top + (height - resized.height) // 2
    canvas.paste(resized, (x, y))
    return x, y, x + resized.width, y + resized.height


def build_poster_png(spec: dict, images: list[dict], out_dir: Path, out_path: Path) -> None:
    palette = POSTER_PALETTES.get(str(spec.get("style") or ""), POSTER_PALETTES["自动匹配"])
    ink, accent, secondary = _rgb(palette["ink"]), _rgb(palette["accent"]), _rgb(palette["secondary"])
    paper, card, soft = _rgb(palette["paper"]), _rgb(palette["card"]), _rgb(palette["soft"])
    canvas = Image.new("RGB", POSTER_SIZE, paper)
    draw = ImageDraw.Draw(canvas)
    font_path = _font_path()
    width, height = POSTER_SIZE
    margin = 58

    title_font = ImageFont.truetype(font_path, 76) if font_path else ImageFont.load_default()
    title_lines = _wrap_text(draw, str(spec.get("title") or ""), title_font, width - margin * 2 - 16)
    while font_path and len(title_lines) * 88 > 170 and title_font.size > 44:
        title_font = ImageFont.truetype(font_path, title_font.size - 4)
        title_lines = _wrap_text(draw, str(spec.get("title") or ""), title_font, width - margin * 2 - 16)
    y = 40
    for line in title_lines[:2]:
        draw.text((margin + 8, y), line, font=title_font, fill=ink)
        y += int(title_font.size * 1.16)
    draw.rectangle([margin, y + 8, width - margin, y + 16], fill=accent)
    subtitle_font = ImageFont.truetype(font_path, 31) if font_path else ImageFont.load_default()
    subtitle_lines = _wrap_text(draw, str(spec.get("subtitle") or ""), subtitle_font, width - margin * 2 - 16)
    y += 28
    for line in subtitle_lines[:2]:
        draw.text((margin + 8, y), line, font=subtitle_font, fill=ink)
        y += 40
    authors = str(spec.get("authors") or "").strip()
    if authors:
        draw.text((margin + 8, y + 2), authors, font=ImageFont.truetype(font_path, 21) if font_path else ImageFont.load_default(), fill=(91, 103, 108))

    body_top = 286
    footer_top = height - 108
    gap = 26
    card_width = (width - margin * 2 - gap * 2) // 3
    card_height = (footer_top - body_top - gap) // 2
    sections = list(spec.get("sections") or [])[:6]

    for index, section in enumerate(sections):
        column, row = index % 3, index // 3
        left = margin + column * (card_width + gap)
        top = body_top + row * (card_height + gap)
        right, bottom = left + card_width, top + card_height
        draw.rounded_rectangle([left, top, right, bottom], radius=9, fill=card, outline=(221, 224, 225), width=2)
        draw.rectangle([left, top, right, top + 8], fill=secondary if index % 3 == 0 else accent)
        heading_font = ImageFont.truetype(font_path, 32) if font_path else ImageFont.load_default()
        heading_lines = _wrap_text(draw, str(section.get("heading") or ""), heading_font, card_width - 48)
        heading_y = top + 24
        for line in heading_lines[:2]:
            draw.text((left + 24, heading_y), line, font=heading_font, fill=ink)
            heading_y += 39
        content_top = heading_y + 10

        found = _section_image(section, images, out_dir)
        if found:
            path, caption = found
            try:
                with Image.open(path) as raw:
                    image = ImageOps.exif_transpose(raw).convert("RGB")
                    image_box = (left + 24, content_top, right - 24, content_top + min(250, card_height // 2))
                    actual = _paste_image_contain(canvas, image, image_box)
                    draw.rectangle(actual, outline=(215, 219, 221), width=2)
                content_top = image_box[3] + 28
                if caption:
                    caption_font = ImageFont.truetype(font_path, 16) if font_path else ImageFont.load_default()
                    caption_lines = _wrap_text(draw, caption, caption_font, card_width - 48)
                    for line in caption_lines[:2]:
                        draw.text((left + 24, content_top), line, font=caption_font, fill=(100, 111, 116))
                        content_top += 20
                    content_top += 12
            except Exception:
                content_top += 8

        content_height = max(60, bottom - 22 - content_top)
        bullets = [str(item) for item in section.get("bullets") or []]
        bullet_font, rendered, line_height = _fit_bullets(draw, bullets, card_width - 48, content_height)
        _draw_bullets(draw, left + 24, content_top, bullets, bullet_font, rendered, line_height, ink)

    draw.rectangle([margin, footer_top - 16, width - margin, footer_top - 10], fill=accent)
    footer_font = ImageFont.truetype(font_path, 29) if font_path else ImageFont.load_default()
    footer_lines = _wrap_text(draw, str(spec.get("takeaway") or ""), footer_font, width - margin * 2 - 20)
    footer_y = footer_top + 4
    for line in footer_lines[:2]:
        draw.text((margin + 8, footer_y), line, font=footer_font, fill=ink)
        footer_y += 38
    canvas.save(out_path, "PNG")


def build_xhs_cover(
    title: str,
    subtitle: str,
    image_path: Path | None,
    out_path: Path,
    image_bytes: bytes | None = None,
) -> str:
    width, height = XHS_COVER_SIZE
    navy, cream, accent = (22, 34, 51), (248, 245, 237), (217, 93, 57)
    canvas = Image.new("RGB", (width, height), navy)
    draw = ImageDraw.Draw(canvas)
    font_path = _font_path()

    draw.rectangle([0, 0, 14, height], fill=accent)
    draw.text((70, 58), "RESEARCH NOTE", font=ImageFont.truetype(font_path, 24) if font_path else ImageFont.load_default(), fill=(174, 196, 204))
    title_font, title_lines, title_line_height = _fit_font(draw, title, width - 140, 330, 84)
    title_y = 120
    for line in title_lines:
        draw.text((70, title_y), line, font=title_font, fill=cream)
        title_y += title_line_height
    if subtitle:
        subtitle_font = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
        subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, width - 140)
        subtitle_y = max(420, title_y + 22)
        for line in subtitle_lines[:2]:
            draw.text((70, subtitle_y), line, font=subtitle_font, fill=(174, 196, 204))
            subtitle_y += 34

    source: Image.Image | None = None
    if image_bytes:
        from app.siliconflow import bytes_to_image

        source = bytes_to_image(image_bytes).convert("RGB")
    elif image_path and image_path.is_file():
        with Image.open(image_path) as raw:
            source = ImageOps.exif_transpose(raw).convert("RGB")

    if source is not None:
        available = (70, 520, width - 70, height - 68)
        scale = min((available[2] - available[0] - 64) / source.width, (available[3] - available[1] - 64) / source.height)
        image_size = (max(1, int(source.width * scale)), max(1, int(source.height * scale)))
        image = source.resize(image_size, Image.LANCZOS)
        card_width, card_height = image.width + 64, image.height + 64
        card_left = (width - card_width) // 2
        card_top = available[1] + 16
        draw.rounded_rectangle(
            [card_left, card_top, card_left + card_width, card_top + card_height],
            radius=24,
            fill=cream,
            outline=(221, 224, 225),
            width=2,
        )
        canvas.paste(image, (card_left + 32, card_top + 32))
        canvas.save(out_path, "PNG")
        return "paper_figure_composed"

    panel = (70, 540, width - 70, height - 80)
    draw.rounded_rectangle(panel, radius=24, fill=(30, 47, 69), outline=(50, 73, 96), width=2)
    draw.rectangle([panel[0] + 38, panel[1] + 56, panel[0] + 160, panel[1] + 70], fill=accent)
    note_font = ImageFont.truetype(font_path, 38) if font_path else ImageFont.load_default()
    note_lines = _wrap_text(draw, subtitle or title, note_font, panel[2] - panel[0] - 80)
    note_y = panel[1] + 118
    for line in note_lines[:8]:
        draw.text((panel[0] + 40, note_y), line, font=note_font, fill=cream)
        note_y += 52
    canvas.save(out_path, "PNG")
    return "local_text"


def render_slides_pptx(spec: dict, images: list[dict], base_dir: Path, out_path: Path) -> int:
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise RuntimeError("缺少 python-pptx，请先安装: pip install python-pptx") from exc

    palette = POSTER_PALETTES.get(str(spec.get("style") or ""), POSTER_PALETTES["自动匹配"])
    ink = RGBColor.from_string(palette["ink"].lstrip("#"))
    paper = RGBColor.from_string(palette["paper"].lstrip("#"))
    accent = RGBColor.from_string(palette["accent"].lstrip("#"))
    secondary = RGBColor.from_string(palette["secondary"].lstrip("#"))
    card = RGBColor.from_string("FFFFFF")
    muted = RGBColor.from_string("647078")
    font_name = "Microsoft YaHei"

    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]
    cover_config = spec.get("cover") if isinstance(spec.get("cover"), dict) else {}
    cover_title_scale = _bounded_float(cover_config.get("title_scale"), 1.0, 0.72, 1.05)
    cover_subtitle_scale = _bounded_float(cover_config.get("font_scale"), 1.0, 0.72, 1.2)

    cover = presentation.slides.add_slide(blank)
    cover.background.fill.solid()
    cover.background.fill.fore_color.rgb = ink
    left_bar = cover.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.16), Inches(7.5))
    left_bar.fill.solid()
    left_bar.fill.fore_color.rgb = accent
    left_bar.line.fill.background()
    cover_title = cover.shapes.add_textbox(Inches(0.8), Inches(1.15), Inches(11.6), Inches(2.6))
    frame = cover_title.text_frame
    frame.clear()
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.text = str(spec.get("deck_title") or "")
    paragraph.font.name = font_name
    paragraph.font.size = Pt((38 if len(paragraph.text) < 60 else 31) * cover_title_scale)
    paragraph.font.bold = True
    paragraph.font.color.rgb = paper
    subtitle_box = cover.shapes.add_textbox(Inches(0.82), Inches(3.8), Inches(11.2), Inches(1.3))
    subtitle_frame = subtitle_box.text_frame
    subtitle_frame.word_wrap = True
    subtitle_frame.text = str(spec.get("subtitle") or "")
    subtitle_frame.paragraphs[0].font.name = font_name
    subtitle_frame.paragraphs[0].font.size = Pt(22 * cover_subtitle_scale)
    subtitle_frame.paragraphs[0].font.color.rgb = RGBColor.from_string("B8C8CD")
    cover_shape = cover.shapes.add_shape(1, Inches(10.75), Inches(5.75), Inches(1.75), Inches(0.12))
    cover_shape.fill.solid()
    cover_shape.fill.fore_color.rgb = secondary
    cover_shape.line.fill.background()

    slides = list(spec.get("slides") or [])
    for index, item in enumerate(slides, 2):
        slide = presentation.slides.add_slide(blank)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = paper
        accent_bar = slide.shapes.add_shape(1, Inches(0), Inches(0), Inches(13.333), Inches(0.12))
        accent_bar.fill.solid()
        accent_bar.fill.fore_color.rgb = accent if index % 2 else secondary
        accent_bar.line.fill.background()

        title_box = slide.shapes.add_textbox(Inches(0.6), Inches(0.38), Inches(12.1), Inches(0.9))
        title_frame = title_box.text_frame
        title_frame.clear()
        title_frame.word_wrap = True
        title_paragraph = title_frame.paragraphs[0]
        title_paragraph.text = str(item.get("title") or f"Slide {index - 1}")
        title_paragraph.font.name = font_name
        title_scale = _bounded_float(item.get("title_scale"), 1.0, 0.72, 1.05)
        title_length = len(title_paragraph.text)
        title_size = 28 if title_length < 26 else 24 if title_length < 38 else 21 if title_length < 50 else 19
        title_paragraph.font.size = Pt(title_size * title_scale)
        title_paragraph.font.bold = True
        title_paragraph.font.color.rgb = ink

        bullets = [
            str(value).strip().rstrip("。；;.!！？?")
            for value in item.get("bullets") or []
        ]
        total_chars = sum(len(value) for value in bullets)
        figure_index = item.get("figure")
        figure = None
        if isinstance(figure_index, int) and 1 <= figure_index <= len(images):
            candidate = base_dir / str(images[figure_index - 1].get("relative") or "")
            if candidate.is_file():
                figure = candidate

        layout = str(item.get("layout") or ("image_right" if figure else "text"))
        if layout not in {"text", "image_right", "image_left"}:
            layout = "image_right" if figure else "text"
        if not figure:
            layout = "text"

        if layout == "image_left":
            text_left = Inches(6.9)
            text_width = Inches(5.77)
            picture_base = (0.62, 1.65, 5.72, 5.0)
        elif layout == "image_right":
            text_left = Inches(0.62)
            text_width = Inches(6.0)
            picture_base = (7.0, 1.65, 5.72, 5.0)
        else:
            text_left = Inches(0.62)
            text_width = Inches(12.05)
            picture_base = (0.0, 0.0, 0.0, 0.0)
        text_top = Inches(1.5)
        text_height = Inches(5.35)
        text_box = slide.shapes.add_textbox(text_left, text_top, text_width, text_height)
        text_frame = text_box.text_frame
        text_frame.clear()
        text_frame.word_wrap = True
        text_frame.vertical_anchor = MSO_ANCHOR.TOP
        if figure:
            font_size = 20 if total_chars < 300 else 18 if total_chars < 430 else 16
        else:
            font_size = 27 if total_chars < 300 else 24 if total_chars < 430 else 21
        font_size *= _bounded_float(item.get("font_scale"), 1.0, 0.68, 1.5)
        line_spacing = _bounded_float(item.get("line_spacing"), 1.15, 1.05, 1.7)
        for bullet_index, bullet in enumerate(bullets):
            paragraph = text_frame.paragraphs[0] if bullet_index == 0 else text_frame.add_paragraph()
            paragraph.text = "•  " + bullet
            paragraph.font.name = font_name
            paragraph.font.size = Pt(font_size)
            paragraph.font.color.rgb = ink
            paragraph.space_after = Pt(9 * line_spacing)
            paragraph.line_spacing = line_spacing
            properties = paragraph._p.get_or_add_pPr()
            properties.set("marL", "320040")
            properties.set("indent", "-320040")

        if figure:
            image_scale = _bounded_float(item.get("image_scale"), 1.0, 0.68, 1.05)
            scaled_width = picture_base[2] * image_scale
            scaled_height = picture_base[3] * image_scale
            picture_box = (
                picture_base[0] + (picture_base[2] - scaled_width) / 2,
                picture_base[1] + (picture_base[3] - scaled_height) / 2,
                scaled_width,
                scaled_height,
            )
            try:
                with Image.open(figure) as raw:
                    image = _trim_white_border(
                        ImageOps.exif_transpose(raw).convert("RGB")
                    )
                    image_width, image_height = image.size
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG", optimize=True)
                    buffer.seek(0)
                scale = min(picture_box[2] / image_width, picture_box[3] / image_height)
                draw_width = image_width * scale
                draw_height = image_height * scale
                picture_left = picture_box[0] + (picture_box[2] - draw_width) / 2
                picture_top = picture_box[1]
                slide.shapes.add_picture(
                    buffer,
                    Inches(picture_left),
                    Inches(picture_top),
                    width=Inches(draw_width),
                    height=Inches(draw_height),
                )
            except Exception:
                figure = None

        page_box = slide.shapes.add_textbox(Inches(12.45), Inches(7.05), Inches(0.6), Inches(0.25))
        page_frame = page_box.text_frame
        page_frame.text = str(index - 1)
        page_frame.paragraphs[0].font.name = font_name
        page_frame.paragraphs[0].font.size = Pt(10)
        page_frame.paragraphs[0].font.color.rgb = muted
        page_frame.paragraphs[0].alignment = PP_ALIGN.RIGHT

        notes = str(item.get("notes") or "").strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    presentation.save(out_path)
    return len(presentation.slides)
