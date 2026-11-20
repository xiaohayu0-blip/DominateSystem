import json
import re
from pathlib import Path

from sqlalchemy import select

from .models import Document
from .retrieval import build_chunks
from .storage import digest, store

CALENDAR_DATA = Path(__file__).with_name("verified_calendars")
CALENDAR_NOTE = "已按原校历表格整理日期、学期与周次。"


def verified_calendar_parts(content_hash):
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        return None
    path = CALENDAR_DATA / f"{content_hash}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def reindex_verified_calendars(db):
    hashes = sorted(path.stem for path in CALENDAR_DATA.glob("*.json"))
    documents = db.scalars(select(Document).where(Document.content_hash.in_(hashes)).with_for_update()).all()
    for document in documents:
        if not document.storage_key or digest(store.path(document.storage_key).read_bytes()) != document.content_hash:
            raise ValueError(f"无法核对校历原件：{document.title}")
        build_chunks(db, document, verified_calendar_parts(document.content_hash), structured=True)
        document.parse_status, document.parse_note = "ready", CALENDAR_NOTE
    return len(documents)
