import hashlib
import io
import json
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
MAX_PDF_PAGES = 500


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
    require_ocr()
    parts.extend(presentation_ocr(path, image_pages, len(slide_ids), ocr_deadline))
    return parts, "含图幻灯片按可见页面渲染后执行 OCR。"


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
                check=True, capture_output=True, timeout=remaining_time(deadline),
            )
            pdf = root / f"{path.stem}.pdf"
            if not pdf.is_file() or len(PdfReader(pdf).pages) != slide_count:
                raise ValueError("PPTX 渲染后的页数与原文件不一致，无法确认 OCR 出处")
            prefix = root / "slide"
            subprocess.run([config.pdftoppm_command, "-f", str(min(image_pages)), "-l", str(max(image_pages)),
                            "-scale-to", "2200", "-png", str(pdf), str(prefix)],
                           check=True, capture_output=True, timeout=remaining_time(deadline))
            images = {int(p.stem.rsplit("-", 1)[1]): p for p in root.glob("slide-*.png")}
            if any(page not in images for page in image_pages):
                raise ValueError("PPTX 含图页面未完整渲染。")
            paths = [images[page] for page in image_pages]
            texts = (rapid_ocr(paths, deadline) if config.rapidocr_python else
                     [ocr_image(image, deadline=deadline) for image in paths])
            return [(f"第{page}页幻灯片（OCR）", text.strip())
                    for page, text in zip(image_pages, texts, strict=True) if text.strip()]
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("PPTX 可见页面渲染或 OCR 失败，请检查 LibreOffice、Poppler 和 OCR 配置或处理时限") from exc


def require_ocr():
    if not settings().ocr_enabled:
        raise ValueError("OCR 未开启，图片中的文字尚未解析。")


def office_image_ocr(path: Path, media_dir: str, deadline: float) -> tuple[list[tuple[str, str]], str]:
    with zipfile.ZipFile(path) as archive:
        images = [entry for entry in archive.infolist()
                  if entry.filename.startswith(media_dir) and not entry.is_dir()]
        if not images:
            return [], ""
        require_ocr()
        members = [(f"内嵌图片 {index} {PurePosixPath(image.filename).name[:120]}（OCR）", image)
                   for index, image in enumerate(images, 1)]
        return embedded_image_ocr(archive, members, deadline), "内嵌图片已执行 OCR。"


def embedded_image_ocr(archive, members, deadline: float) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as folder:
        if settings().rapidocr_python:
            unique_members = list(dict.fromkeys(member for _, member in members))
            targets = [Path(folder) / f"image-{index}" for index in range(len(unique_members))]
            for member, target in zip(unique_members, targets, strict=True):
                target.write_bytes(archive.read(member))
            recognized = dict(zip(unique_members, rapid_ocr(targets, deadline), strict=True))
        else:
            target = Path(folder) / "image"
            recognized = {}
            for _, member in members:
                if member not in recognized:
                    target.write_bytes(archive.read(member))
                    recognized[member] = ocr_image(target, deadline=deadline)
    return [(location, recognized[member].strip()) for location, member in members if recognized[member].strip()]


def roster_merged_ranges(sheet, column: int):
    from lxml import etree
    from openpyxl.utils.cell import range_boundaries

    ranges = {}
    tag = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}mergeCell"
    with sheet.parent._archive.open(sheet._worksheet_path) as xml:
        for _, element in etree.iterparse(xml, events=("end",), resolve_entities=False, no_network=True):
            if element.tag == tag:
                left, top, right, bottom = range_boundaries(element.attrib["ref"])
                if left == right == column and 1 <= top < bottom <= 20000:
                    ranges[top] = bottom
            element.clear()
            parent = element.getparent()
            while parent is not None and element.getprevious() is not None:
                del parent[0]
    return ranges


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
        images, note = office_image_ocr(path, "word/media/", ocr_deadline)
        parts.extend(images)
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            for sheet in wb:
                counselor_column = None
                counselor_ranges = {}
                counselor_value, merge_end = None, 0
                for ri, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    if ri > 20000:
                        raise ValueError("工作表超过20000行")
                    if counselor_column is None and "班级" in row and "辅导员" in row:
                        counselor_column = row.index("辅导员")
                        counselor_ranges = roster_merged_ranges(sheet, counselor_column + 1)
                    if counselor_column is not None and counselor_column < len(row):
                        if ri in counselor_ranges:
                            counselor_value, merge_end = row[counselor_column], counselor_ranges[ri]
                        elif ri <= merge_end and row[counselor_column] is None:
                            row = (*row[:counselor_column], counselor_value, *row[counselor_column + 1:])
                    text = " | ".join(str(v) if v is not None else "" for v in row)
                    if text.strip(" |"):
                        parts.append((f"工作表 {sheet.title} 第{ri}行", text))
        finally:
            wb.close()
        images, note = office_image_ocr(path, "xl/media/", ocr_deadline)
        parts.extend(images)
    elif suffix == ".pptx":
        parts, note = parse_presentation(path, ocr_deadline)
    else:
        parts, note = parse_pdf(path, ocr_deadline)
    parts = [(location, text) for location, text in parts if text.strip(" |\r\n\t")]
    if not parts or sum(len(t) for _, t in parts) > 2_000_000:
        raise ValueError(f"{note}未提取到文本或文本超过200万字符")
    return parts, note


def parse_pdf(path: Path, deadline: float) -> tuple[list[tuple[str, str]], str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"加密 PDF 或超过 {MAX_PDF_PAGES} 页不支持")
        pages = [(page.extract_text() or "", page.images) for page in reader.pages]
    except PdfReadError:
        pages = [(text, None) for text in recover_pdf_text(path, deadline)]

    parts = []
    recognized = False
    seen_images = set()
    for index, (text, images) in enumerate(pages, 1):
        if text.strip():
            parts.append((f"第{index}页", text))
        if images is None or not text.strip():
            require_ocr()
            recognized = True
            ocr_text = ocr_page(path, index, deadline=deadline).strip()
            if ocr_text and " ".join(ocr_text.split()) != " ".join(text.split()):
                parts.append((f"第{index}页（OCR）", ocr_text))
        elif images:
            require_ocr()
            recognized = True
            for number, image in enumerate(images, 1):
                image_hash = digest(image.data)
                if image_hash in seen_images:
                    continue
                with tempfile.TemporaryDirectory() as folder:
                    target = Path(folder) / "image"
                    target.write_bytes(image.data)
                    ocr_text = ocr_image(target, deadline=deadline).strip()
                seen_images.add(image_hash)
                if ocr_text and " ".join(ocr_text.split()) != " ".join(text.split()):
                    parts.append((f"第{index}页 图片{number}（OCR）", ocr_text))
    return parts, "PDF 图片页面已执行 OCR。" if recognized else ""


def recover_pdf_text(path: Path, deadline: float) -> list[str]:
    poppler = Path(settings().pdftoppm_command)
    suffix = poppler.suffix
    try:
        info = subprocess.run([str(poppler.with_name("pdfinfo" + suffix)), str(path)], check=True,
                              capture_output=True, timeout=ocr_timeout(deadline)).stdout.decode("utf-8", "replace")
        count = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
        if not count or not 1 <= int(count[1]) <= MAX_PDF_PAGES or not re.search(r"^Encrypted:\s+no\b", info, re.MULTILINE):
            raise ValueError(f"加密 PDF、无有效页面或超过 {MAX_PDF_PAGES} 页不支持")
        try:
            text = subprocess.run([str(poppler.with_name("pdftotext" + suffix)), "-enc", "UTF-8", str(path), "-"],
                                  check=True, capture_output=True,
                                  timeout=ocr_timeout(deadline)).stdout.decode("utf-8")
        except FileNotFoundError:
            # Some bundled Poppler distributions only include page rendering tools.
            text = ""
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("PDF 索引损坏，Poppler 无法恢复页面。") from exc
    pages = text.split("\f")
    return (pages + [""] * int(count[1]))[:int(count[1])]


def remaining_time(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise subprocess.TimeoutExpired("OCR", 0)
    return seconds


def ocr_page(path: Path, page: int, *, deadline: float | None = None) -> str:
    config = settings()
    try:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "page"
            subprocess.run([config.pdftoppm_command, "-f", str(page), "-l", str(page), "-scale-to", "2200",
                            "-singlefile", "-png", str(path), str(target)], check=True,
                           capture_output=True, timeout=ocr_timeout(deadline))
            return ocr_image(target.with_suffix(".png"), deadline=deadline)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"第 {page} 页 OCR 渲染失败，请检查 Poppler 或处理时限。") from exc


def ocr_timeout(deadline: float | None) -> float:
    seconds = settings().ocr_timeout_seconds
    return seconds if deadline is None else min(seconds, remaining_time(deadline))


def ocr_image(path: Path, *, deadline: float | None) -> str:
    config = settings()
    if config.rapidocr_python:
        return rapid_ocr([path], deadline)[0]
    command = [config.tesseract_command, str(path), "stdout", "-l", config.ocr_language]
    image_deadline = time.monotonic() + config.ocr_timeout_seconds
    if deadline is not None:
        image_deadline = min(image_deadline, deadline)
    try:
        result = subprocess.run(command, check=True, capture_output=True, timeout=remaining_time(image_deadline))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("图片 OCR 失败，请检查 Tesseract 配置、图片格式或处理时限。") from exc
    text = result.stdout.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fragmented = len(lines) >= 6 and sum(len(line) <= 4 for line in lines) * 2 > len(lines)
    text_size = sum(character.isalnum() for character in text)
    chinese_expected = {"chi_sim", "chi_tra"} & set(config.ocr_language.split("+"))
    chinese_count = len(re.findall(r"[\u3400-\u9fff]", text))
    sparse_chinese = chinese_expected and (chinese_count < 4 or chinese_count * 3 < text_size)
    if not fragmented and not sparse_chinese:
        return text
    # Automatic page segmentation can split a text block into vertical fragments.
    try:
        retry = subprocess.run(
            [*command, "--psm", "6"], check=True, capture_output=True, timeout=remaining_time(image_deadline),
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return text
    retry_size = sum(character.isalnum() for character in retry)
    retry_chinese = len(re.findall(r"[\u3400-\u9fff]", retry))
    if sparse_chinese and retry_chinese >= 4 and retry_chinese * 2 >= retry_size:
        return retry
    retry_lines = sum(bool(line.strip()) for line in retry.splitlines())
    if fragmented and retry_size > 0 and retry_size * 4 >= text_size * 3 and retry_lines * 2 <= len(lines):
        return retry
    return text


def rapid_ocr(paths: list[Path], deadline: float | None) -> list[str]:
    config = settings()
    if config.rapidocr_site_packages and not Path(config.rapidocr_site_packages).is_dir():
        raise ValueError("RapidOCR 依赖目录不存在，请检查本机 OCR 环境配置。")
    command = [config.rapidocr_python, "-I", str(Path(__file__).with_name("rapid_ocr.py").resolve()),
               config.rapidocr_site_packages]
    try:
        timeout = (remaining_time(deadline) if len(paths) > 1 and deadline is not None
                   else ocr_timeout(deadline))
        result = subprocess.run(command, input=json.dumps([str(path) for path in paths]).encode("ascii"),
                                check=True, capture_output=True, timeout=timeout)
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("图片 OCR 超过处理时限，请拆分文件后重试。") from exc
    except FileNotFoundError as exc:
        raise ValueError("RapidOCR 的 Python 解释器不存在，请检查本机 OCR 环境配置。") from exc
    except subprocess.CalledProcessError as exc:
        if b"ModuleNotFoundError" in (exc.stderr or b""):
            raise ValueError("RapidOCR 的 Python 环境缺少依赖，请修复本机 OCR 环境。") from exc
        raise ValueError("图片 OCR 失败，请检查 RapidOCR 配置或图片格式。") from exc
    except (OSError, ValueError) as exc:
        raise ValueError("图片 OCR 失败，请检查 RapidOCR 配置或图片格式。") from exc


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
