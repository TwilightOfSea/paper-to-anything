from __future__ import annotations

import io
import ipaddress
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from PIL import Image
import requests


SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}
SCANNED_PAGE_MIN_CHARS = 20
SCANNED_PAGE_RATIO = 0.8
MAX_REMOTE_IMAGE_BYTES = 25 * 1024 * 1024
VECTOR_MERGE_GAP = 36.0
VECTOR_RENDER_SCALE = 2.5
_REMOTE_SESSION = requests.Session()
_REMOTE_SESSION.trust_env = False


class SourceError(ValueError):
    pass


@dataclass
class SourceImage:
    path: Path
    caption: str = ""
    page: int | None = None
    width: int = 0
    height: int = 0

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass
class SourceBundle:
    source_path: Path
    text: str
    images: list[SourceImage] = field(default_factory=list)
    title: str = ""
    kind: str = ""


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise SourceError(f"无法识别文件编码: {path.name}")


def _meaningful_chars(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u3400-\u9fff]", text or ""))


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return 0, 0


def _copy_source_image(src: Path, image_dir: Path, stem: str) -> SourceImage | None:
    try:
        if not src.is_file():
            return None
        image_dir.mkdir(parents=True, exist_ok=True)
        dest = image_dir / f"{stem}{src.suffix.lower() or '.png'}"
        shutil.copy2(src, dest)
        width, height = _image_size(dest)
        if width < 120 or height < 80:
            dest.unlink(missing_ok=True)
            return None
        return SourceImage(dest, width=width, height=height)
    except OSError:
        return None


def _remote_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local:
            return False
    except ValueError:
        pass
    return True


def _download_remote_image(
    url: str, image_dir: Path, stem: str, caption: str = "", page: int | None = None
) -> SourceImage | None:
    if not _remote_url_allowed(url):
        return None
    try:
        response = _REMOTE_SESSION.get(url, timeout=30)
        response.raise_for_status()
        if len(response.content) > MAX_REMOTE_IMAGE_BYTES:
            return None
        with Image.open(io.BytesIO(response.content)) as image:
            width, height = image.size
            if width < 240 or height < 120:
                return None
            image_dir.mkdir(parents=True, exist_ok=True)
            dest = image_dir / f"{stem}.png"
            image.convert("RGB").save(dest, "PNG")
        return SourceImage(dest, caption=caption, page=page, width=width, height=height)
    except (requests.RequestException, OSError, ValueError):
        return None


def _extract_markdown_images(text: str, base_dir: Path, image_dir: Path) -> list[SourceImage]:
    found: list[SourceImage] = []
    for index, match in enumerate(re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text), 1):
        caption, raw = match.group(1).strip(), match.group(2).strip()
        if raw.startswith(("http://", "https://")):
            image = _download_remote_image(raw, image_dir, f"md_fig_{index}", caption)
            if image:
                found.append(image)
            continue
        target = Path(unquote(raw.split("#", 1)[0]))
        if not target.is_absolute():
            target = base_dir / target
        image = _copy_source_image(target, image_dir, f"md_fig_{index}")
        if image:
            image.caption = caption
            found.append(image)
    return found


def _extract_pdf_vector_images(
    page, page_index: int, image_dir: Path, images: list[SourceImage]
) -> None:
    import pymupdf

    boxes: list[pymupdf.Rect] = []
    for drawing in page.get_drawings():
        raw = drawing.get("rect")
        if raw is None:
            continue
        box = pymupdf.Rect(
            max(page.rect.x0, raw.x0),
            max(page.rect.y0, raw.y0),
            min(page.rect.x1, raw.x1),
            min(page.rect.y1, raw.y1),
        )
        if box.width >= 2 or box.height >= 2:
            boxes.append(box)
    if len(boxes) < 8:
        return

    # shortcut: vector bands may merge adjacent panels; upgrade to caption-based segmentation.
    boxes.sort(key=lambda box: (box.y0, box.y1))
    bands: list[list] = []
    for box in boxes:
        if bands and box.y0 <= bands[-1][0].y1 + VECTOR_MERGE_GAP:
            bands[-1][0] |= box
            bands[-1][1] += 1
        else:
            bands.append([box, 1])

    vector_index = 0
    for band, drawing_count in bands:
        if drawing_count < 12 or band.width < 120 or band.height < 120:
            continue
        crop = pymupdf.Rect(band)
        crop = (crop + (-6, -6, 6, 6)) & page.rect
        vector_index += 1
        dest = image_dir / f"pdf_p{page_index}_vector{vector_index}.png"
        page.get_pixmap(
            matrix=pymupdf.Matrix(VECTOR_RENDER_SCALE, VECTOR_RENDER_SCALE),
            clip=crop,
            alpha=False,
        ).save(dest)
        width, height = _image_size(dest)
        if width < 240 or height < 160:
            dest.unlink(missing_ok=True)
            continue
        images.append(SourceImage(dest, page=page_index, width=width, height=height))


def _pdf_page_text(page) -> str:
    regular = page.get_text("text") or ""
    try:
        # Some PDFs leave body text in the content stream that get_text() drops.
        lines: list[str] = []
        line_y: float | None = None
        parts: list[str] = []
        for span in sorted(page.get_texttrace(), key=lambda item: item["seqno"]):
            text = "".join(chr(char[0]) for char in span.get("chars", []))
            if not text:
                continue
            y0 = span["bbox"][1]
            if line_y is not None and abs(y0 - line_y) > 2.5:
                line = "".join(parts).strip()
                if line:
                    lines.append(line)
                parts = []
            line_y = y0
            parts.append(text)
        line = "".join(parts).strip()
        if line:
            lines.append(line)
        traced = "\n".join(lines)
        if _meaningful_chars(traced) > _meaningful_chars(regular):
            return traced
    except Exception:
        pass
    return regular


def _load_pdf(path: Path, image_dir: Path) -> SourceBundle:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:
            raise SourceError("缺少 PyMuPDF，请先安装: pip install pymupdf") from exc

    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise SourceError(f"PDF 无法打开: {exc}") from exc

    if doc.needs_pass:
        doc.close()
        raise SourceError("PDF 已加密，当前版本无法读取")
    if doc.page_count <= 0:
        doc.close()
        raise SourceError("PDF 没有可用页面")

    page_texts: list[str] = []
    images: list[SourceImage] = []
    image_dir.mkdir(parents=True, exist_ok=True)
    seen_xrefs: set[int] = set()
    title = str((doc.metadata or {}).get("title") or "").strip()

    try:
        for page_index, page in enumerate(doc, 1):
            text = _pdf_page_text(page)
            page_texts.append(text)
            for image_index, item in enumerate(page.get_images(full=True), 1):
                xref = int(item[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    info = doc.extract_image(xref)
                    raw = info.get("image")
                    ext = (info.get("ext") or "png").lower()
                    if not raw:
                        continue
                    dest = image_dir / f"pdf_p{page_index}_{image_index}.{ext}"
                    dest.write_bytes(raw)
                    width, height = _image_size(dest)
                    if width < 240 or height < 160:
                        dest.unlink(missing_ok=True)
                        continue
                    images.append(SourceImage(dest, page=page_index, width=width, height=height))
                except Exception:
                    continue
            try:
                _extract_pdf_vector_images(page, page_index, image_dir, images)
            except Exception:
                continue
    finally:
        doc.close()

    low_text_pages = sum(_meaningful_chars(text) < SCANNED_PAGE_MIN_CHARS for text in page_texts)
    if page_texts and low_text_pages / len(page_texts) >= SCANNED_PAGE_RATIO:
        raise SourceError(
            "检测到该 PDF 可能是扫描版或图片型 PDF。当前版本未集成 OCR/MinerU，"
            "无法可靠提取正文、图表与公式。请改用带文字层的 PDF、TXT、MD 或 HTML。"
        )

    text = "\n\n".join(f"# Page {index}\n\n{value}".strip() for index, value in enumerate(page_texts, 1))
    if _meaningful_chars(text) < 200:
        raise SourceError("PDF 有效文字过少，可能是扫描版或解析失败")
    return SourceBundle(path, text=text, images=images, title=title, kind="pdf")


def _load_html(path: Path, image_dir: Path) -> SourceBundle:
    html = _read_text(path)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = ""
    heading = soup.find("h1")
    if heading:
        title = heading.get_text(" ", strip=True)
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = soup.get_text("\n", strip=True)
    images: list[SourceImage] = []
    seen_remote: set[str] = set()
    for index, tag in enumerate(soup.find_all("img"), 1):
        raw = (tag.get("src") or "").strip()
        if not raw or raw.startswith(("data:", "http://", "https://", "//")):
            continue
        target = Path(unquote(urlparse(raw).path))
        if not target.is_absolute():
            target = path.parent / target
        image = _copy_source_image(target, image_dir, f"html_fig_{index}")
        if image:
            image.caption = (tag.get("alt") or "").strip()
            images.append(image)

    figure_tags = soup.find_all("figure")
    for index, figure in enumerate(figure_tags, 1):
        tag = figure.find("img")
        if not tag:
            continue
        raw = (tag.get("src") or "").strip()
        if not raw.startswith(("http://", "https://")) or raw in seen_remote:
            continue
        seen_remote.add(raw)
        caption_tag = figure.find("figcaption")
        caption = caption_tag.get_text(" ", strip=True) if caption_tag else (tag.get("alt") or "").strip()
        image = _download_remote_image(raw, image_dir, f"html_figure_{index}", caption)
        if image:
            images.append(image)
    if _meaningful_chars(text) < 100:
        raise SourceError("HTML 有效正文过少，无法生成内容")
    return SourceBundle(path, text=text, images=images, title=title, kind="html")


def load_source(path: Path, image_dir: Path) -> SourceBundle:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SourceError(f"文件不存在: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SourceError(f"不支持的文件类型: {suffix}")
    if suffix == ".pdf":
        return _load_pdf(path, image_dir)
    if suffix in {".html", ".htm"}:
        return _load_html(path, image_dir)

    text = _read_text(path)
    images = _extract_markdown_images(text, path.parent, image_dir) if suffix in {".md", ".markdown"} else []
    if _meaningful_chars(text) < 100:
        raise SourceError("文件有效文字过少，无法生成内容")
    first_line = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
    return SourceBundle(path, text=text, images=images, title=first_line[:200], kind=suffix.lstrip("."))
