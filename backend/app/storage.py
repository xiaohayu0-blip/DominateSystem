import hashlib
import io
import posixpath
import re
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from .config import settings
from .db import uid


class FileStore(Protocol):
    def save(self, content: bytes, suffix: str) -> str: ...
    def path(self, key: str) -> Path: ...


class LocalFileStore:
    def path(self, key: str) -> Path:
        root = settings().storage_dir.resolve()
        candidate = (root / key).resolve()
        if candidate.parent != root:
            raise ValueError("invalid storage key")
        return candidate

    def save(self, content: bytes, suffix: str) -> str:
        key = f"{uid()}{suffix}"
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(content)
        return key


store = LocalFileStore()
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv", ".pptx"}
OFFICE_MARKERS = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml",
                  ".pptx": "ppt/presentation.xml"}
MAX_EXPANDED_MB = 160


def infer_business_year(filename: str, heading: str = "") -> int | None:
    years = set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", Path(filename).stem))
    if not years:
        years = set(re.findall(r"(?<!\d)(20\d{2})\s*(?:年(?!级|入学)|版)", heading.split("\n", 1)[0][:100]))
    return int(years.pop()) if len(years) == 1 else None


def validate_file(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in DOCUMENT_SUFFIXES:
        raise ValueError("仅支持 PDF、DOCX、XLSX、CSV、PPTX 或 ZIP 批量上传")
    if not data or len(data) > settings().max_upload_mb * 1024 * 1024:
        raise ValueError("文件为空或超过大小限制")
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("PDF文件头无效")
    if suffix in OFFICE_MARKERS:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 10000 or sum(i.file_size for i in z.infolist()) > MAX_EXPANDED_MB * 1024**2:
                    raise ValueError("压缩文档展开超过限制")
                marker = OFFICE_MARKERS[suffix]
                if marker not in z.namelist() or any(i.flag_bits & 1 for i in z.infolist()):
                    raise ValueError("文件格式或加密状态不支持")
                if any("vbaproject" in i.filename.lower() for i in z.infolist()):
                    raise ValueError("不允许包含宏的文件")
        except zipfile.BadZipFile as exc:
            raise ValueError("文档压缩结构损坏") from exc
    return suffix


def unpack_documents(data: bytes):
    file_limit = settings().max_upload_mb * 1024**2
    if not data or len(data) > file_limit:
        raise ValueError("ZIP 文件为空或超过上传大小限制")
    documents = []
    skipped = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 200 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_MB * 1024**2:
                raise ValueError(f"ZIP 展开后超过 200 个条目或 {MAX_EXPANDED_MB} MB")
            for entry in entries:
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if (path.is_absolute() or ".." in path.parts or ":" in entry.filename
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise ValueError("ZIP 包含不安全的路径、链接或加密条目")
                if entry.is_dir():
                    continue
                if path.suffix.lower() not in DOCUMENT_SUFFIXES or any(
                        part.startswith(".") or part == "__MACOSX" for part in path.parts):
                    skipped += 1
                    continue
                if len(documents) >= 20:
                    raise ValueError("每个 ZIP 最多导入 20 份资料")
                if entry.file_size > file_limit:
                    raise ValueError("ZIP 中有资料超过单份上传大小限制")
                name = path.name[:255]
                raw = archive.read(entry)
                documents.append((name, raw, validate_file(name, raw)))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise ValueError("ZIP 损坏或使用了不支持的压缩格式") from exc
    if not documents:
        raise ValueError("ZIP 中没有可导入的资料")
    return documents, skipped


def parse_presentation(path: Path, ocr_deadline: float) -> tuple[list[tuple[str, str]], str]:
    from lxml import etree

    presentation = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    relation = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    parts = []
    image_pages = []
    try:
        with zipfile.ZipFile(path) as archive:
            deck = etree.fromstring(archive.read("ppt/presentation.xml"), parser)
            relationships = etree.fromstring(archive.read("ppt/_rels/presentation.xml.rels"), parser)
            targets = {item.get("Id"): item.get("Target") for item in relationships
                       if item.get("TargetMode") != "External" and item.get("Type", "").endswith("/slide")}
            slide_ids = deck.findall(f"{presentation}sldIdLst/{presentation}sldId")
            if len(slide_ids) > 200:
                raise ValueError("PPTX 超过 200 页")
            for index, slide_id in enumerate(slide_ids, 1):
                target = targets.get(slide_id.get(relation + "id")) or ""
                member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
                if not member.startswith("ppt/slides/") or not member.endswith(".xml"):
                    raise ValueError("PPTX 幻灯片路径无效")
                slide = etree.fromstring(archive.read(member), parser)
                if slide.get("show") in {"0", "false"}:
                    continue
                paragraphs = ["".join(node.text or "" for node in paragraph.iter(drawing + "t"))
                              for paragraph in slide.iter(drawing + "p")]
                text = "\n".join(paragraph for paragraph in paragraphs if paragraph.strip())
                if text:
                    parts.append((f"第{index}页幻灯片", text))
                if next(slide.iter(drawing + "blip"), None) is not None:
                    image_pages.append(index)
    except (KeyError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise ValueError("PPTX 幻灯片结构损坏") from exc
    if not image_pages:
        return parts, ""
    if not settings().ocr_enabled:
        return parts, "PPTX 含图片，当前未启用 OCR。图片中的文字尚未解析。"
    parts.extend(presentation_ocr(path, image_pages, len(slide_ids), ocr_deadline))
    return parts, "PPTX 图片页面已执行 OCR，请核对识别文字及表格数值后发布。"


def presentation_ocr(path: Path, image_pages: list[int], slide_count: int,
                     deadline: float) -> list[tuple[str, str]]:
    from pypdf import PdfReader

    config = settings()
    try:
        # The renderer must not resolve linked content from the upload.
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.startswith("ppt/embeddings/") and not name.endswith("/"):
                    raise ValueError("PPTX 含嵌入对象，自动 OCR 前请移除该对象")
                if name.endswith(".rels"):
                    relationships = etree.fromstring(archive.read(name), parser)
                    if any(r.get("TargetMode") == "External" and not r.get("Type", "").endswith("/hyperlink")
                           for r in relationships):
                        raise ValueError("PPTX 含外部链接资源，自动 OCR 前请将资源嵌入文件")
        # A separate LibreOffice profile keeps concurrent conversions isolated.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            subprocess.run(
                [config.libreoffice_command, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to",
                 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}',
                 "--outdir", str(root), str(path.resolve())],
                check=True, capture_output=True, timeout=min(config.ocr_timeout_seconds, remaining_time(deadline)),
            )
            pdf = root / f"{path.stem}.pdf"
            if not pdf.is_file() or len(PdfReader(pdf).pages) != slide_count:
                raise ValueError("PPTX 渲染后的页数与原文件不一致，无法确认 OCR 出处")
            parts = []
            for page in image_pages:
                text = ocr_page(pdf, page, deadline=deadline).strip()
                if text:
                    parts.append((f"第{page}页幻灯片（OCR）", text))
            return parts
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("PPTX 渲染或 OCR 失败，请检查 LibreOffice、Poppler 和 Tesseract 配置或处理时限") from exc


def parse_file(path: Path, *, ocr_deadline: float | None = None) -> tuple[list[tuple[str, str]], str]:
    suffix = path.suffix.lower()
    parts: list[tuple[str, str]] = []
    note = ""
    if ocr_deadline is None:
        ocr_deadline = time.monotonic() + settings().ocr_upload_timeout_seconds
    if suffix == ".csv":
        import csv
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        for i, row in enumerate(csv.reader(io.StringIO(text)), 1):
            parts.append((f"第{i}行", " | ".join(row)))
            if i > 20000:
                raise ValueError("行数超过20000")
    elif suffix == ".docx":
        from docx import Document
        doc = Document(path)
        for i, p in enumerate(doc.paragraphs, 1):
            if p.text.strip():
                parts.append((f"第{i}段", p.text))
        for ti, table in enumerate(doc.tables, 1):
            for ri, row in enumerate(table.rows, 1):
                parts.append((f"表{ti}第{ri}行", " | ".join(c.text for c in row.cells)))
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            for sheet in wb:
                for ri, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    if ri > 20000:
                        raise ValueError("工作表超过20000行")
                    text = " | ".join(str(v) if v is not None else "" for v in row)
                    if text.strip(" |"):
                        parts.append((f"工作表 {sheet.title} 第{ri}行", text))
        finally:
            wb.close()
    elif suffix == ".pptx":
        parts, note = parse_presentation(path, ocr_deadline)
    else:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 200:
            raise ValueError("加密PDF或超过200页不支持")
        scanned = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if len(text.strip()) < 10:
                scanned.append(i)
                if settings().ocr_enabled:
                    text = ocr_page(path, i, deadline=ocr_deadline)
            if text.strip():
                parts.append((f"第{i}页", text))
        if scanned:
            note = f"第{','.join(map(str, scanned))}页疑似扫描件。" + (
                "已OCR，识别质量未经人工核对，发布前逐页检查。" if settings().ocr_enabled
                else "OCR未开启，请配置OCR后重试。")
            if not settings().ocr_enabled:
                raise ValueError(note)
    if not parts and note:
        raise ValueError(note)
    if not parts or sum(len(t) for _, t in parts) > 2_000_000:
        raise ValueError("未提取到文本或文本超过200万字符")
    return parts, note


def remaining_time(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise subprocess.TimeoutExpired("PPTX OCR", 0)
    return seconds


def ocr_page(path: Path, page: int, *, deadline: float | None = None) -> str:
    config = settings()
    def timeout():
        return (config.ocr_timeout_seconds if deadline is None else
                min(config.ocr_timeout_seconds, remaining_time(deadline)))

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "page"
        subprocess.run([config.pdftoppm_command, "-f", str(page), "-l", str(page), "-scale-to", "2200",
                        "-singlefile", "-png", str(path), str(target)], check=True,
                        capture_output=True, timeout=timeout())
        result = subprocess.run([config.tesseract_command, str(target) + ".png", "stdout", "-l",
                                 config.ocr_language], check=True, capture_output=True,
                                timeout=timeout())
        return result.stdout.decode("utf-8", errors="replace")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
