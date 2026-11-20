from __future__ import annotations

import json
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from bs4 import BeautifulSoup

from app.qa import (
    apply_visual_fixes,
    content_review,
    export_pptx_images,
    visual_review,
)
from app.render import (
    build_cover,
    build_poster_png,
    build_xhs_cover,
    markdown_to_html,
    render_slides_pptx,
    strip_code_fence,
    write_poster_html,
    write_wechat_html,
)
from app.siliconflow import FREE_IMAGE_MODEL, SiliconFlowClient, SiliconFlowError
from app.sources import SourceBundle, SourceError, SourceImage, load_source


LogFn = Callable[[str], None]
MAX_SOURCE_CHARS = 50000


@contextmanager
def _waiting_progress(log: LogFn, message: str, heartbeat: str, interval: float = 15.0):
    started = time.monotonic()
    stop = Event()
    log(message)

    def worker() -> None:
        while not stop.wait(interval):
            elapsed = int(time.monotonic() - started)
            log(f"{heartbeat}（已等待 {elapsed} 秒）")

    thread = Thread(target=worker, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)
        log(f"等待结束，用时 {time.monotonic() - started:.1f} 秒")


@dataclass
class RunOptions:
    include_images: bool = True
    html_style: str = "自动匹配"
    html_language: str = "跟随原文"
    wechat_angle: str = "通俗科研解读"
    wechat_length: int = 1800
    ai_cover: bool = False
    image_model: str = FREE_IMAGE_MODEL
    output_language: str = "跟随原文"
    poster_style: str = "自动匹配"
    xhs_tone: str = "专业科普"
    slides_length: int = 10
    visual_qa: bool = True


@dataclass
class JobResult:
    source: str
    ok: bool
    output_dir: str
    message: str


class ImageGenerationGate:
    """Keep generated-image requests at least 20 seconds apart."""

    def __init__(self, interval_seconds: int = 20) -> None:
        self.interval_seconds = interval_seconds
        self._next_allowed = 0.0

    def wait(self, log: LogFn) -> None:
        delay = self._next_allowed - time.monotonic()
        if delay > 0:
            log(f"生图间隔限制：等待 {delay:.0f} 秒后继续")
            time.sleep(delay)
        self._next_allowed = time.monotonic() + self.interval_seconds


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^\w\u3400-\u9fff.-]+", "_", value, flags=re.UNICODE).strip("._ ")
    return cleaned[:80] or "source"


def _unique_dir(parent: Path, stem: str) -> Path:
    candidate = parent / _safe_name(stem)
    index = 2
    while candidate.exists():
        candidate = parent / f"{_safe_name(stem)}_v{index}"
        index += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _truncate(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[正文已截断：仅使用前 50000 字符供本次生成]"


def _copy_pipeline_images(bundle: SourceBundle, out_dir: Path, max_images: int) -> list[dict]:
    if not bundle.images:
        return []
    selected: list[SourceImage] = sorted(bundle.images, key=lambda item: item.area, reverse=True)[:max_images]
    image_dir = out_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    for index, image in enumerate(selected, 1):
        suffix = image.path.suffix.lower() or ".png"
        dest = image_dir / f"figure_{index:02d}{suffix}"
        shutil.copy2(image.path, dest)
        copied.append(
            {
                "file": dest.name,
                "relative": f"images/{dest.name}",
                "caption": image.caption,
                "page": image.page,
            }
        )
    return copied


def _extract_json_object(text: str) -> dict:
    value = strip_code_fence(text)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(value[start : end + 1])


def _language_instruction(value: str) -> str:
    if value == "中文":
        return "所有面向读者可见的文字使用简体中文；必要英文术语可保留，并首次给出中文解释。"
    if value == "English":
        return "All reader-facing text must be written in English."
    return "面向读者的文字跟随论文主要语言；必要术语可保留原文。"


def _string_list(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _figure_number(value, image_count: int) -> int | None:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 1 <= index <= image_count else None


def _image_manifest(copied: list[dict]) -> str:
    if not copied:
        return "无可用图片。不得引用任何图片文件。"
    return "\n".join(
        f"{index}. {item['relative']} | 页 {item.get('page') or '-'} | {item.get('caption') or '无图注'}"
        for index, item in enumerate(copied, 1)
    )


def _write_job_info(out_dir: Path, task: str, options: RunOptions) -> None:
    data = {"task": task, "options": asdict(options)}
    (out_dir / "job.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_html_images(html: str, out_dir: Path, log: LogFn) -> str:
    soup = BeautifulSoup(html, "html.parser")
    removed = 0
    for tag in soup.find_all("img"):
        src = (tag.get("src") or "").strip()
        if src.startswith("images/"):
            if not (out_dir / src).is_file():
                tag.decompose()
                removed += 1
        if tag.get("src") and not (tag.get("alt") or "").strip():
            tag["alt"] = "论文配图"
    if removed:
        log(f"已移除 {removed} 个引用不存在的图片")
    return str(soup)


def _inject_responsive_css(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.head is None:
        soup.html.insert(0, soup.new_tag("head"))
    style = soup.new_tag("style", id="paper2anything-responsive")
    style.string = """
*,*::before,*::after{box-sizing:border-box}
html,body{max-width:100%!important;overflow-x:hidden!important}
body *{max-width:100%!important}
h1,h2,h3,p,li,a,code,td,th{overflow-wrap:anywhere}
img{max-width:100%!important;height:auto!important}
pre,table{max-width:100%!important;overflow-x:auto!important}
@media (max-width:640px){
  body{padding-left:12px!important;padding-right:12px!important}
  h1{font-size:clamp(32px,9vw,54px)!important;line-height:1.18!important}
  h2{font-size:clamp(24px,7vw,38px)!important}
}
"""
    soup.head.append(style)
    return str(soup)


def _normalize_html_output(raw: str) -> str:
    value = strip_code_fence(raw)
    lower = value.lower()
    start = lower.find("<!doctype html")
    end = lower.rfind("</html>")
    if start >= 0 and end >= start:
        return value[start : end + len("</html>")].strip()
    if start >= 0:
        value = value[start:].rstrip()
        lower = value.lower()
        if "</body>" not in lower:
            value += "\n</body>"
        if "</html>" not in lower:
            value += "\n</html>"
        return value
    html_start = lower.find("<html")
    if html_start >= 0:
        value = "<!DOCTYPE html>\n" + value[html_start:].rstrip()
        if "</html>" not in value.lower():
            value += "\n</html>"
        return value
    return value


def _generate_html(
    bundle: SourceBundle,
    client: SiliconFlowClient,
    out_dir: Path,
    options: RunOptions,
    log: LogFn,
) -> None:
    copied = _copy_pipeline_images(bundle, out_dir, 10) if options.include_images else []
    image_note = (
        "\n".join(f"- {item['relative']} | 页 {item['page'] or '-'} | {item['caption'] or '无图注'}" for item in copied)
        if copied
        else "无可用图片。不得引用任何图片文件。"
    )
    system = (
        "你是学术论文项目主页设计师。根据用户提供的论文正文和真实图片清单，生成一个完整、"
        "可直接打开的单页学术项目主页。只输出 HTML，不要 Markdown 代码围栏，不要解释。"
        "禁止编造作者、机构、数据、结论或外部链接。所有图片只能引用给定清单中的相对路径。"
    )
    user = f"""论文文件：{bundle.source_path.name}
论文标题候选：{bundle.title or '未提供'}
设计语言：{options.html_style}
页面语言：{options.html_language}

可用图片清单：
{image_note}

硬性要求：
1. 输出以 <!DOCTYPE html> 开头、以 </html> 结尾。
2. 使用内联 CSS，自适应桌面和手机，不要横向溢出。
3. 首页包含真实标题、摘要/一句话贡献、核心方法、主要结果和局限（若原文有）。
4. 图片使用 <img src="images/...">，必须有非空 alt，不能拉伸变形。
5. 不引用不存在的图片，不使用空链接 href="#"。
6. 不要输出生成过程或说明文字。

论文正文：
{_truncate(bundle.text)}
"""
    log("正在调用文本模型生成项目主页")
    raw = client.chat(system, user, max_tokens=12000)
    (out_dir / "raw_model_output.txt").write_text(raw, encoding="utf-8")
    html = _normalize_html_output(raw)
    lower = html.lower()
    if "<!doctype html" not in lower or "</html>" not in lower:
        raise SiliconFlowError(f"模型没有返回完整 HTML 文档: {raw[:240]}")
    html = _clean_html_images(html, out_dir, log)
    html = _inject_responsive_css(html)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "source.txt").write_text(bundle.text, encoding="utf-8")
    _write_job_info(out_dir, "html", options)


def _wechat_prompt(bundle: SourceBundle, options: RunOptions, copied: list[dict]) -> tuple[str, str]:
    image_note = (
        "\n".join(f"- {item['relative']} | {item['caption'] or '无图注'}" for item in copied)
        if copied
        else "无可用图片。"
    )
    system = (
        "你是严谨的学术公众号作者。请基于论文原文撰写深度解读，忠实准确，不夸大，不编造数据。"
        "只输出一个 JSON 对象，不要 Markdown 代码围栏。JSON 字段必须为："
        "title（中文标题）、digest（不超过120字摘要）、cover_title（短封面标题）、"
        "body_markdown（完整 Markdown 正文）。"
    )
    user = f"""论文标题候选：{bundle.title or '未提供'}
写作角度：{options.wechat_angle}
目标篇幅：约 {options.wechat_length} 个中文字符

可用正文图片：
{image_note}

写作要求：
1. 用 H2 分节，讲清问题、方法、关键结果、意义与局限。
2. 可解释直觉和背景，但必须区分论文事实与作者点评。
3. body_markdown 中只引用给定图片清单里的相对路径；没有图片就不要编造图片。
4. 标题有吸引力但不要标题党，摘要准确。
5. JSON 字符串必须正确转义。

论文原文：
{_truncate(bundle.text)}
"""
    return system, user


def _generate_wechat(
    bundle: SourceBundle,
    client: SiliconFlowClient,
    out_dir: Path,
    options: RunOptions,
    gate: ImageGenerationGate,
    log: LogFn,
) -> None:
    copied = _copy_pipeline_images(bundle, out_dir, 4) if options.include_images else []
    system, user = _wechat_prompt(bundle, options, copied)
    log("正在调用文本模型生成公众号文章")
    raw = strip_code_fence(client.chat(system, user, max_tokens=10000))
    try:
        article = _extract_json_object(raw)
    except Exception as exc:
        raise SiliconFlowError(f"公众号文章 JSON 解析失败: {exc}") from exc

    title = str(article.get("title") or bundle.title or bundle.source_path.stem).strip()
    digest = str(article.get("digest") or "论文深度解读").strip()
    cover_title = str(article.get("cover_title") or title).strip()
    body = str(article.get("body_markdown") or "").strip()
    if len(body) < 200:
        raise SiliconFlowError("模型返回的公众号正文过短")

    if copied and not any(item["relative"] in body for item in copied):
        body += "\n\n## 论文配图\n\n"
        for index, item in enumerate(copied, 1):
            caption = item["caption"] or f"论文配图 {index}"
            body += f"![{caption}]({item['relative']})\n\n"

    (out_dir / "wechat_article.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    metadata = {
        "title": title,
        "digest": digest,
        "cover_title": cover_title,
        "word_count": len(re.sub(r"\s+", "", body)),
    }
    (out_dir / "wechat_article.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    body_html = markdown_to_html(body)
    write_wechat_html(title, digest, body_html, out_dir / "wechat_article.html")
    (out_dir / "source.txt").write_text(bundle.text, encoding="utf-8")

    image_error = ""
    generated: bytes | None = None
    if options.ai_cover:
        gate.wait(log)
        log(f"正在使用 {options.image_model} 生成公众号封面")
        prompt = (
            f"为学术论文公众号文章设计一张专业、简洁的横版封面。主题：{cover_title}。"
            f"文章摘要：{digest[:300]}。不要画具体数据图、坐标轴、表格或虚构界面，"
            "不要出现真实人物照片。画面不要出现任何文字、字母、数字、标志或水印，"
            "构图留出清晰标题区域，视觉风格克制、现代。"
        )
        try:
            generated = client.generate_image(prompt, options.image_model)
        except Exception as exc:
            image_error = str(exc)
            log(f"生图失败，回退本地封面：{image_error[:200]}")

    fallback = Path(copied[0]["relative"]).name if copied else None
    fallback_path = out_dir / "images" / fallback if fallback else None
    cover_source = build_cover(cover_title, generated, fallback_path, out_dir / "cover.jpg")
    metadata["cover_source"] = cover_source
    if image_error:
        metadata["image_error"] = image_error[:500]
    (out_dir / "wechat_article.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_job_info(out_dir, "wechat", options)


def _generate_poster(
    bundle: SourceBundle,
    client: SiliconFlowClient,
    out_dir: Path,
    options: RunOptions,
    log: LogFn,
) -> None:
    copied = _copy_pipeline_images(bundle, out_dir, 6) if options.include_images else []
    system = (
        "你是学术会议海报编辑。请从论文原文提炼一张可独立阅读的横向会议海报，"
        "忠实准确，不编造作者、数据、结论或引用。只输出一个 JSON 对象，不要 Markdown 代码围栏。"
        "JSON 字段必须为：title、subtitle、authors、sections、takeaway。"
        "sections 是 6 个对象的数组，每个对象字段为 heading、bullets、figure_index。"
        "bullets 为 2-3 条短句；figure_index 只能取图片清单中的序号，没有合适图片时填 null。"
    )
    user = f"""论文标题候选：{bundle.title or '未提供'}
海报视觉风格：{options.poster_style}
输出语言：{_language_instruction(options.output_language)}

可用论文图片：
{_image_manifest(copied)}

编辑要求：
1. 六区建议覆盖：研究问题、数据与对象、方法、核心发现、证据/对比、意义与局限。
2. 每个分区只保留海报尺度能读清的内容；每条 bullet 中文不超过 45 字，英文不超过 25 词。
3. 标题与一句话贡献必须来自原文；作者信息仅在原文明确出现时填写，否则留空。
4. 不得把模型猜测写成事实；不得引用清单之外的图片。
5. takeaway 用一句话给出最重要的可靠结论。

论文原文：
{_truncate(bundle.text)}
"""
    log("正在调用文本模型提炼海报内容")
    raw = strip_code_fence(client.chat(system, user, max_tokens=6500))
    (out_dir / "poster_raw_model.txt").write_text(raw, encoding="utf-8")
    try:
        data = _extract_json_object(raw)
    except Exception as exc:
        raise SiliconFlowError(f"海报 JSON 解析失败: {exc}") from exc

    sections = []
    for item in data.get("sections") or []:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or "").strip()
        bullets = _string_list(item.get("bullets"), 3)
        if not heading or not bullets:
            continue
        sections.append(
            {
                "heading": heading,
                "bullets": bullets,
                "figure": _figure_number(item.get("figure_index"), len(copied)),
            }
        )
    if len(sections) < 4:
        raise SiliconFlowError("模型返回的海报分区不足，无法形成完整海报")

    spec = {
        "title": str(data.get("title") or bundle.title or bundle.source_path.stem).strip(),
        "subtitle": str(data.get("subtitle") or "").strip(),
        "authors": str(data.get("authors") or "").strip(),
        "sections": sections[:6],
        "takeaway": str(data.get("takeaway") or "").strip(),
        "style": options.poster_style,
    }
    (out_dir / "poster_spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_poster_html(spec, copied, out_dir)
    log("正在本地渲染海报 PNG")
    build_poster_png(spec, copied, out_dir, out_dir / "poster.png")
    (out_dir / "source.txt").write_text(bundle.text, encoding="utf-8")
    _write_job_info(out_dir, "poster", options)


def _generate_xhs(
    bundle: SourceBundle,
    client: SiliconFlowClient,
    out_dir: Path,
    options: RunOptions,
    gate: ImageGenerationGate,
    log: LogFn,
) -> None:
    copied = _copy_pipeline_images(bundle, out_dir, 6) if options.include_images else []
    system = (
        "你是严谨的学术内容编辑，正在把论文改写为小红书图文笔记。"
        "只输出一个 JSON 对象，不要 Markdown 代码围栏。字段必须为："
        "title、body、cover_text、hashtags、figure_index。"
        "title 不超过 20 个中文字符；body 为 500-900 个中文字符的纯文本；"
        "cover_text 不超过 16 个中文字符；hashtags 为 4-6 个字符串；"
        "figure_index 只能取图片清单序号，没有合适图片时填 null。"
    )
    user = f"""论文标题候选：{bundle.title or '未提供'}
文案语气：{options.xhs_tone}
输出语言：{_language_instruction(options.output_language)}

可用论文图片：
{_image_manifest(copied)}

写作要求：
1. 开头三行必须直接交代问题、方法和最有价值的发现。
2. 用短段落讲清研究设计、关键结果、临床/应用意义与局限。
3. 可以有自然的分段和适度 emoji 分区，但不要夸大疗效或因果，不编造数字。
4. 不要输出 Markdown 标题、代码围栏或解释说明；body 中不要自己添加话题标签。
5. 封面文字要短、明确、有信息量，避免“震惊”“必看”等标题党。

论文原文：
{_truncate(bundle.text)}
"""
    log("正在调用文本模型生成小红书文案")
    raw = strip_code_fence(client.chat(system, user, max_tokens=4500))
    (out_dir / "xhs_raw_model.txt").write_text(raw, encoding="utf-8")
    try:
        data = _extract_json_object(raw)
    except Exception as exc:
        raise SiliconFlowError(f"小红书 JSON 解析失败: {exc}") from exc

    title = str(data.get("title") or bundle.title or bundle.source_path.stem).strip()[:40]
    body = str(data.get("body") or "").strip()
    cover_text = str(data.get("cover_text") or title).strip()[:40]
    hashtags = _string_list(data.get("hashtags"), 8)
    if len(re.sub(r"\s+", "", body)) < 250:
        raise SiliconFlowError("模型返回的小红书正文过短")
    if not hashtags:
        hashtags = ["#论文分享", "#科研阅读", "#医学研究"]

    figure_index = _figure_number(data.get("figure_index"), len(copied)) or (1 if copied else None)
    cover_image = (
        out_dir / copied[figure_index - 1]["relative"]
        if figure_index and copied
        else None
    )
    image_error = ""
    generated: bytes | None = None
    if options.ai_cover:
        gate.wait(log)
        log(f"正在使用 {options.image_model} 生成小红书封面")
        prompt = (
            f"为学术论文小红书笔记设计一张克制、清晰的竖版主视觉。主题：{cover_text}。"
            f"论文一句话背景：{str(data.get('title') or '')}。"
            "不要画具体数据图、坐标轴、表格或虚构界面，不要出现真实人物照片；"
            "画面不要出现文字、字母、数字、标志或水印，主体居中，适合竖版裁切。"
        )
        try:
            generated = client.generate_image(prompt, options.image_model)
        except Exception as exc:
            image_error = str(exc)
            log(f"生图失败，回退论文配图封面：{image_error[:200]}")

    cover_source = build_xhs_cover(
        cover_text,
        bundle.title or title,
        cover_image,
        out_dir / "cover.png",
        generated,
    )
    post_dir = out_dir / "post_images"
    post_dir.mkdir(parents=True, exist_ok=True)
    post_images = []
    for index, item in enumerate(copied, 1):
        source = out_dir / item["relative"]
        destination = post_dir / f"p{index}{source.suffix.lower() or '.png'}"
        shutil.copy2(source, destination)
        post_images.append(destination.name)

    tags_line = " ".join(hashtags)
    markdown = f"![封面](cover.png)\n\n# {title}\n\n{body}\n\n{tags_line}\n"
    (out_dir / "xhs_post.md").write_text(markdown, encoding="utf-8")
    metadata = {
        "title": title,
        "cover_text": cover_text,
        "hashtags": hashtags,
        "cover_source": cover_source,
        "post_images": post_images,
        "word_count": len(re.sub(r"\s+", "", body)),
    }
    if image_error:
        metadata["image_error"] = image_error[:500]
    (out_dir / "xhs_post.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "source.txt").write_text(bundle.text, encoding="utf-8")
    _write_job_info(out_dir, "xhs", options)


def _normalise_slide_outline(
    data: dict,
    copied: list[dict],
    target: int,
    fallback_title: str,
) -> dict:
    slides = []
    for item in data.get("slides") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        bullets = _string_list(item.get("bullets"), 4)
        if not title or not bullets:
            continue
        slides.append(
            {
                "title": title,
                "bullets": bullets,
                "figure": _figure_number(item.get("figure_index"), len(copied)),
                "notes": str(item.get("notes") or "").strip(),
            }
        )
    if len(slides) < 5:
        raise SiliconFlowError("模型返回的幻灯片页数不足")
    return {
        "deck_title": str(data.get("deck_title") or fallback_title).strip(),
        "subtitle": str(data.get("subtitle") or "").strip(),
        "slides": slides[:target],
        "style": "自动匹配",
    }


def _build_slide_spec(outline: dict) -> dict:
    slides = []
    for index, item in enumerate(outline.get("slides") or []):
        figure = item.get("figure")
        layout = (
            "image_right"
            if index % 2 == 0 and figure
            else "image_left"
            if figure
            else "text"
        )
        slides.append(
            {
                **item,
                "layout": layout,
                "font_scale": 1.0,
                "title_scale": 1.0,
                "image_scale": 1.0,
                "line_spacing": 1.15,
            }
        )
    return {
        "deck_title": outline.get("deck_title") or "",
        "subtitle": outline.get("subtitle") or "",
        "style": outline.get("style") or "自动匹配",
        "cover": {"title_scale": 1.0, "font_scale": 1.0},
        "slides": slides,
    }


def _write_slide_json(out_dir: Path, outline: dict, spec: dict) -> None:
    (out_dir / "slide_outline.json").write_text(
        json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "slide_spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _render_slides(spec: dict, copied: list[dict], out_dir: Path, log: LogFn) -> int:
    log("正在本地渲染 PPTX")
    slide_count = render_slides_pptx(spec, copied, out_dir, out_dir / "slides.pptx")
    if not (out_dir / "slides.pptx").is_file() or (out_dir / "slides.pptx").stat().st_size < 1024:
        raise SiliconFlowError("PPTX 渲染结果无效")
    return slide_count


def _generate_slides(
    bundle: SourceBundle,
    client: SiliconFlowClient,
    out_dir: Path,
    options: RunOptions,
    log: LogFn,
) -> None:
    copied = _copy_pipeline_images(bundle, out_dir, 10) if options.include_images else []
    target = max(6, min(12, int(options.slides_length)))
    system = (
        "你是学术论文汇报设计师。请把论文提炼为逻辑完整、可直接汇报的演示文稿大纲。"
        "只输出一个 JSON 对象，不要 Markdown 代码围栏。字段必须为：deck_title、subtitle、slides。"
        "slides 中每页字段为 title、bullets、figure_index、notes。"
        "bullets 为 2-4 条短句；figure_index 只能取图片清单序号，无合适图片时填 null；"
        "notes 为演讲者备注。"
    )
    user = f"""论文标题候选：{bundle.title or '未提供'}
目标页数：约 {target} 页内容页
视觉风格：{options.poster_style}
输出语言：{_language_instruction(options.output_language)}

可用论文图片：
{_image_manifest(copied)}

叙事要求：
1. 从研究问题、背景缺口、数据与对象、方法、主要结果、机制/解释、局限与结论展开。
2. 每页只承担一个清晰叙述任务，标题写成结论句而非泛泛栏目名。
3. 所有数字和术语必须有原文依据；不得编造作者、基金、期刊或外部事实。
4. 图表只在清单中有合适图片时引用；不要为了每页配图而强行引用。
5. 演讲备注补充页面上没有展开的解释，但不得引入新事实。

论文原文：
{_truncate(bundle.text)}
"""
    with _waiting_progress(
        log,
        "正在调用文本模型生成幻灯片大纲",
        "模型仍在生成幻灯片大纲",
    ):
        raw = strip_code_fence(client.chat(system, user, max_tokens=7500))
    log("幻灯片大纲已返回，正在解析结构化内容")
    (out_dir / "slides_raw_model.txt").write_text(raw, encoding="utf-8")
    try:
        data = _extract_json_object(raw)
    except Exception as exc:
        raise SiliconFlowError(f"幻灯片 JSON 解析失败: {exc}") from exc

    outline = _normalise_slide_outline(
        data, copied, target, bundle.title or bundle.source_path.stem
    )
    outline["style"] = options.poster_style
    log(f"已解析 {len(outline['slides'])} 页内容，正在准备论文图片与版式")
    spec = _build_slide_spec(outline)
    _write_slide_json(out_dir, outline, spec)
    log(f"幻灯片结构已就绪：{len(copied)} 张候选论文图片，开始排版")
    slide_count = _render_slides(spec, copied, out_dir, log)
    (out_dir / "source.txt").write_text(bundle.text, encoding="utf-8")

    qa_log = {
        "visual_qa": bool(options.visual_qa),
        "mode": "single_pass",
        "content_rounds": [],
        "visual_rounds": [],
    }
    with _waiting_progress(
        log,
        "正在执行内容 QA",
        "内容 QA 模型仍在审查",
    ):
        content_result = content_review(client, bundle.text, out_dir / "slides.pptx")
    qa_log["content_rounds"].append({"round": 1, **content_result})
    qa_log["content_pass"] = content_result["pass"]
    if content_result["pass"]:
        log(f"内容 QA 通过，记录 {len(content_result['issues'])} 条低优先级提示")
    else:
        log(f"内容 QA 发现 {len(content_result['issues'])} 个问题，已写入 qa_log.json")

    visual_fixes_applied = 0
    visual_pass: bool | None = None
    if options.visual_qa:
        preview_dir = out_dir / "qa" / "visual_round_1"
        images, exporter = export_pptx_images(
            out_dir / "slides.pptx", preview_dir, log
        )
        if not images:
            qa_log["visual_rounds"].append(
                {"round": 1, "status": "unavailable", "issues": []}
            )
            log("视觉 QA 已跳过：本机没有可用的 PowerPoint/LibreOffice 转图环境")
        else:
            with _waiting_progress(
                log,
                f"正在执行视觉 QA（{len(images)} 页）",
                "视觉 QA 模型仍在审查",
            ):
                visual_result = visual_review(client, images, spec)
            qa_log["visual_rounds"].append(
                {
                    "round": 1,
                    "exporter": exporter,
                    **visual_result,
                }
            )
            if visual_result["pass"]:
                visual_pass = True
                log("视觉 QA 通过")
            else:
                visual_pass = False
                log(f"视觉 QA 发现 {len(visual_result['issues'])} 个问题，正在应用一次布局修正")
                visual_fixes_applied = apply_visual_fixes(spec, visual_result["issues"])
                if visual_fixes_applied:
                    (out_dir / "slide_spec.json").write_text(
                        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    slide_count = _render_slides(spec, copied, out_dir, log)
                    log(f"已应用 {visual_fixes_applied} 项布局修正，未执行第二轮视觉复查")
                else:
                    log("视觉问题无法自动映射为布局修改，已保留审查记录")
    else:
        log("视觉 QA 已按设置关闭")

    qa_log["visual_pass"] = visual_pass
    qa_log["visual_fixes_applied"] = visual_fixes_applied
    (out_dir / "qa_log.json").write_text(
        json.dumps(qa_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_job_info(out_dir, "slides", options)
    log(f"PPTX 已生成：{slide_count} 页（含封面）")


def run_task(
    task: str,
    files: list[Path],
    client: SiliconFlowClient,
    output_root: Path,
    options: RunOptions,
    gate: ImageGenerationGate,
    log: LogFn,
) -> list[JobResult]:
    if task not in {"html", "wechat", "poster", "xhs", "slides"}:
        raise ValueError(f"暂不支持的任务: {task}")
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[JobResult] = []
    for source in files:
        log(f"处理文件：{source}")
        job_dir: Path | None = None
        try:
            job_dir = _unique_dir(output_root / task, source.stem)
            bundle = load_source(source, job_dir / "_source_images")
            log(f"解析完成：正文 {len(bundle.text)} 字符，图片 {len(bundle.images)} 张")
            if task == "html":
                _generate_html(bundle, client, job_dir, options, log)
                results.append(JobResult(str(source), True, str(job_dir), "已生成 index.html"))
            elif task == "wechat":
                _generate_wechat(bundle, client, job_dir, options, gate, log)
                results.append(
                    JobResult(str(source), True, str(job_dir), "已生成文章、HTML 与封面")
                )
            elif task == "poster":
                _generate_poster(bundle, client, job_dir, options, log)
                results.append(
                    JobResult(str(source), True, str(job_dir), "已生成海报 HTML 与 PNG")
                )
            elif task == "xhs":
                _generate_xhs(bundle, client, job_dir, options, gate, log)
                results.append(
                    JobResult(str(source), True, str(job_dir), "已生成小红书文案、封面与配图")
                )
            else:
                _generate_slides(bundle, client, job_dir, options, log)
                results.append(
                    JobResult(str(source), True, str(job_dir), "已生成 PPTX 与幻灯片大纲")
                )
            log(f"完成：{job_dir}")
        except (SourceError, SiliconFlowError, OSError, ValueError) as exc:
            message = str(exc)
            log(f"失败：{source.name} | {message}")
            results.append(JobResult(str(source), False, str(job_dir or ""), message))
    return results
