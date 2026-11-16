import re
import unicodedata

from sqlalchemy import select

from .models import Chunk, Document
from .retrieval import citation

CATALOG_MARKER = " · 学科竞赛目录结构化 · "
CATALOG_ROW = re.compile(r"^(20\d{2})年学科竞赛目录：(.+)，等级：([ABC][+-]?)类?。$")
CLASSIFICATION = re.compile(
    r"^(?:请问|想问)?\s*(.+?)\s*(?:是|属于|算)?\s*"
    r"(?:哪一?类|哪一?级|什么类(?:别)?|什么(?:级别|等级)|哪个(?:等级|类别))(?:的)?(?:赛事|竞赛|比赛)?[。！!？?]*$"
)


def name_key(text):
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", text).casefold())


def competition_classification(db, query, requested_year=None):
    query = unicodedata.normalize("NFKC", query)
    explicit = re.match(r"^(20\d{2})\s*年(?:度)?\s*", query)
    if explicit:
        requested_year = int(explicit[1])
        query = query[explicit.end():]
    question = CLASSIFICATION.fullmatch(query)
    if not question:
        return None
    name = name_key(question[1].strip())
    if len(name) < 2:
        return None
    rows = db.execute(select(Chunk, Document).join(Document).where(
        Document.status == "published", Document.parse_status == "ready",
        Chunk.location.contains(CATALOG_MARKER),
    )).all()
    entries = [(chunk, doc, row) for chunk, doc in rows if (row := CATALOG_ROW.fullmatch(chunk.text))]
    if not entries:
        return None
    is_special = name.endswith("专项赛")
    is_main = name.endswith("主赛")
    if is_special or is_main:
        name = name.removesuffix("专项赛").removesuffix("主赛")
    candidates = [(chunk, doc, row) for chunk, doc, row in entries
                  if name in name_key(row[2])
                  and (not is_special or "专项赛" in row[2])
                  and (not is_main or "专项赛" not in row[2])]
    if not candidates:
        if not re.search(r"杯|赛", query):
            return None
        return [], (), "当前入库的赛事目录中没有匹配项。请提供证书上的赛事全称或当年的竞赛目录。"
    missing_year = requested_year and not any(int(row[1]) == requested_year for _, _, row in candidates)
    version = max(int(row[1]) for _, _, row in candidates) if missing_year else (
        requested_year or max(int(row[1]) for _, _, row in candidates))
    candidates = [(chunk, doc, row) for chunk, doc, row in candidates if int(row[1]) == version]
    exact = [entry for entry in candidates if name_key(entry[2][2]) == name]
    candidates = exact or candidates
    if len(candidates) > 5:
        return [], (), "这个简称对应的赛事较多，请补充赛事全称或具体赛道。"
    note = f"未找到{requested_year}年对应条目，以下展示已入库的{version}年目录依据。" if missing_year else ""
    return [citation(chunk, doc) for chunk, doc, _ in candidates], tuple(row[3] for _, _, row in candidates), note
