import hashlib
import io
import subprocess
import tempfile
import zipfile
from pathlib import Path
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


def validate_file(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".xlsx", ".csv"}:
        raise ValueError("仅支持PDF、DOCX、XLSX、CSV")
    if not data or len(data) > settings().max_upload_mb * 1024 * 1024:
        raise ValueError("文件为空或超过大小限制")
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("PDF文件头无效")
    if suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 10000 or sum(i.file_size for i in z.infolist()) > 80 * 1024**2:
                    raise ValueError("压缩文档展开超过限制")
                marker = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                if marker not in z.namelist() or any(i.flag_bits & 1 for i in z.infolist()):
                    raise ValueError("文件格式或加密状态不支持")
                if any("vbaProject" in i.filename for i in z.infolist()):
                    raise ValueError("不允许包含宏的文件")
        except zipfile.BadZipFile as exc:
            raise ValueError("文档压缩结构损坏") from exc
    return suffix


def parse_file(path: Path) -> tuple[list[tuple[str, str]], str]:
    suffix = path.suffix.lower()
    parts: list[tuple[str, str]] = []
    note = ""
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
                    text = ocr_page(path, i)
            if text.strip():
                parts.append((f"第{i}页", text))
        if scanned:
            note = f"第{','.join(map(str, scanned))}页疑似扫描件。" + (
                "已OCR，识别质量未经人工核对，发布前逐页检查。" if settings().ocr_enabled
                else "OCR未开启，请配置OCR后重试。")
            if not settings().ocr_enabled:
                raise ValueError(note)
    if not parts or sum(len(t) for _, t in parts) > 2_000_000:
        raise ValueError("未提取到文本或文本超过200万字符")
    return parts, note


def ocr_page(path: Path, page: int) -> str:
    config = settings()
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "page"
        subprocess.run([config.pdftoppm_command, "-f", str(page), "-l", str(page), "-scale-to", "2200",
                        "-singlefile", "-png", str(path), str(target)], check=True,
                       capture_output=True, timeout=config.ocr_timeout_seconds)
        result = subprocess.run([config.tesseract_command, str(target) + ".png", "stdout", "-l",
                                 config.ocr_language], check=True, capture_output=True,
                                timeout=config.ocr_timeout_seconds)
        return result.stdout.decode("utf-8", errors="replace")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
