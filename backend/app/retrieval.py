import hashlib
import math
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .embeddings import BGE_DIMENSIONS, embed_passages, embed_query
from .models import Chunk, Document

STOP = set("的了是在有与和及或请问可以如何怎么什么哪些多少时候需要我们我要了解一下")
SYNONYMS = {"综测": "综合测评", "奖助": "奖学金", "缓考": "延期考试", "宿舍": "寝室", "舍长": "寝室长"}
ORAL_TOPICS = {
    r"国奖|国讲": "国家奖学金",
    r"国家励志(?:奖学金)?|国力志|励志奖(?:学金)?|励志": "国家励志奖学金",
    r"攀登奖学金|(?:攀登|攀灯|潘登)(?:奖学金|奖)?": "攀登奖学金",
    r"校奖|校[一二三]奖|校[一二三]等": "校级奖学金",
    r"心理资询": "心理咨询",
    r"校立": "校历",
    r"寝事长": "寝室长",
    r"综合厕评": "综合测评",
    r"工成造价": "工程造价",
    r"请架": "请假",
    r"重休": "重修",
    r"当冰": "当兵",
    r"刷丹": "刷单",
}
AWARD_TOPICS = ("国家奖学金", "国家励志奖学金", "攀登奖学金", "校级奖学金")
DATED_BATCH_DEADLINE = re.compile(
    r"20\d{2}\s*年(?:春|秋)季.{0,24}(?:截止|截至)"
)
COUNSELING_RECORD = re.compile(r"咨询.{0,16}(?:留|记|写|进|入).{0,12}(?:学籍)?档案")
GENERIC = {"演示", "合成", "申请", "请材", "材料", "包括", "需要", "条件", "流程", "提交", "时间", "办理",
           "如何", "什么", "哪些", "办法", "规定", "说明", "学生", "学院", "制度", "要求", "咨询"}
ENLISTMENT_ELIGIBILITY = re.compile(
    r"(?:请问\s*)?(?:报名)?(?:应征入伍|征兵入伍|入伍|参军|当兵|征兵)"
    r"(?:的)?(?:要|需要|有|有什么|有哪些)?(?:满足)?(?:哪些|什么|啥)?"
    r"(?:基本)?(?:条件|要求|门槛|资格)(?:是(?:什么|啥)|有(?:哪些|什么))?[。！？?]?"
)
ENLISTMENT_TOPIC = "应征入伍基本条件"
CLASS_CODE = re.compile(r"([a-z\u4e00-\u9fff]{2,8})(1\d{2})(\d{2})", re.IGNORECASE)
CLASS_GROUP = re.compile(r"(?:20)?(\d{2})级")
CLASS_NUMBER = re.compile(r"(\d{1,2}|[一二三四五六七八九十])班")
CLASS_NUMBERS = "一二三四五六七八九十"
EMPTY_TITLE = re.compile(
    r"(?:班主任|老师).{0,12}(?:没写|没填|没有填|未填|未登记|空白).{0,4}职称|"
    r"职称.{0,6}(?:没写|没填|没有填|未填|未登记|空白)"
)


def roster_fields(query):
    fields = []
    if "班主任" in query and ("职称" not in query or re.search(r"是谁|哪位|哪个|姓名|名字", query)):
        fields.append("班主任")
    if "辅导员" in query:
        fields.append("辅导员")
    if "职称" in query:
        fields.append("班主任职称")
    return fields or ["班主任"]


def roster_columns(rows):
    aliases = {"职称": "班主任职称", "班主任姓名": "班主任", "辅导员姓名": "辅导员"}
    headers = {}
    for chunk, _ in rows:
        if not chunk.location.startswith("工作表") or "班级" not in chunk.text:
            continue
        fields = [field.strip() for field in chunk.text.split("|")]
        columns = {aliases.get(field, field): index for index, field in enumerate(fields) if field}
        if "班级" in columns and ("班主任" in columns or "辅导员" in columns):
            headers.setdefault(sheet_key(chunk), columns)
    return headers


def roster_cell(row, columns, field):
    index = columns.get(field)
    return row[index] if index is not None and index < len(row) else None


def scoped_roster_matches(rows, headers, query):
    fields = roster_fields(query)
    blank_title = bool(EMPTY_TITLE.search(query))
    grade = CLASS_GROUP.search(query)
    if not blank_title and (fields != ["辅导员"] or not grade or CLASS_NUMBER.search(query)):
        return None
    matches, people, scoped_rows = [], set(), []
    for chunk, doc in rows:
        columns = headers.get(sheet_key(chunk))
        if not columns:
            continue
        row = [value.strip() for value in chunk.text.split("|")]
        code = CLASS_CODE.fullmatch(roster_cell(row, columns, "班级") or "")
        if not code or grade and grade[1] != code[2][1:]:
            continue
        major = code[1].casefold()
        if not blank_title and major not in query.casefold():
            continue
        if blank_title:
            title = roster_cell(row, columns, "班主任职称")
            if roster_cell(row, columns, "班主任") and title is not None:
                scoped_rows.append((chunk, doc, major, matches_roster(code[0], query), title))
            continue
        people.add(roster_cell(row, columns, "辅导员"))
        matches.append((chunk, doc))
    if blank_title:
        majors = {major for _, _, major, _, _ in scoped_rows if major in query.casefold()}
        if any(exact for _, _, _, exact, _ in scoped_rows):
            scoped_rows = [row for row in scoped_rows if row[3]]
        elif re.search(r"1\d{4}(?!\d)", query):
            return None
        elif majors:
            scoped_rows = [row for row in scoped_rows if row[2] in majors]
        matches = [(chunk, doc) for chunk, doc, _, _, title in scoped_rows if not title]
        if matches:
            return matches, "roster_match"
        if scoped_rows:
            return [(chunk, doc) for chunk, doc, _, _, _ in scoped_rows[:8]], "roster_no_blank_title"
    if matches:
        return (matches[:8], "roster_match") if len(people) == 1 and all(people) else ([], "needs_clarification")
    return None


def terms(text: str) -> set[str]:
    text = normalize_terms(text)
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    result = {w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text)}
    for run in chinese:
        result.update(run[i:i + 2] for i in range(len(run) - 1)
                      if not all(c in STOP for c in run[i:i + 2]))
    return result


def normalize_terms(text: str) -> str:
    for key, value in SYNONYMS.items():
        text = text.replace(key, value)
    return text


def sheet_key(chunk):
    return chunk.document_id, chunk.location.split(" 第", 1)[0]


def normalize_roster_query(query: str) -> str:
    query = re.sub(r"(?<![辅教])导员", "辅导员", query)
    return re.sub(r"数管(?=\s*(?:1\d{4}|[一二三四五六七八九十\d]{1,2}班))", "数据管理", query)


def inferred_roster_match(class_value: str, query: str) -> bool:
    code = CLASS_CODE.fullmatch(class_value)
    if not code:
        return False
    major, year, number = code.groups()
    query = re.sub(r"请问|查一下|帮我查|我们是|我是|我们|我们班", "", query, flags=re.IGNORECASE)
    short = re.search(rf"([\u4e00-\u9fff]{{2,5}})\s*{year}{number}(?!\d)", query)
    if not short or len(short[1]) >= len(major) or short[1][0] != major[0]:
        return False
    return re.search(".*".join(map(re.escape, short[1])), major) is not None


def matches_roster(class_value: str, query: str) -> bool:
    code = CLASS_CODE.fullmatch(class_value)
    if not code:
        return False
    query = query.lower()
    if re.search(rf"{re.escape(class_value.lower())}(?![0-9])", query):
        return True
    group, number = CLASS_GROUP.search(query), CLASS_NUMBER.search(query)
    if not group or not number or code[1].lower() not in query:
        return False
    class_number = int(number[1]) if number[1].isdigit() else CLASS_NUMBERS.index(number[1]) + 1
    return group[1] == code[2][1:] and class_number == int(code[3])


def vector(text: str, dimensions=128) -> list[float]:
    values = [0.0] * dimensions
    for term in terms(text):
        raw = hashlib.blake2b(term.encode(), digest_size=8).digest()
        values[int.from_bytes(raw[:4]) % dimensions] += 1 if raw[4] % 2 else -1
    norm = math.sqrt(sum(x * x for x in values)) or 1
    return [round(x / norm, 6) for x in values]


def build_chunks(db: Session, document: Document, parts: list[tuple[str, str]], *, structured=False):
    from sqlalchemy import delete
    chunks = []
    for location, raw in parts:
        if structured:
            chunks.append(Chunk(document_id=document.id, location=location, text=raw, vector=vector(raw)))
            continue
        # Keep source location; overlap prevents arbitrary chunk cuts hiding nearby conditions.
        for i in range(0, len(raw), 650):
            text = raw[i:i + 800].strip()
            if text:
                chunks.append(Chunk(document_id=document.id, location=f"{location} · 字符{i + 1}",
                                    text=text, vector=vector(text)))
    if settings().embedding_mode == "bge":
        index_bge([(chunk, document) for chunk in chunks])
    db.execute(delete(Chunk).where(Chunk.document_id == document.id))
    db.add_all(chunks)
    db.flush()


def index_bge(rows: list[tuple[Chunk, Document]], *, allow_download: bool = False) -> None:
    for start in range(0, len(rows), 32):
        batch = rows[start:start + 32]
        texts = [f"{doc.title}\n{chunk.text}" for chunk, doc in batch]
        for (chunk, _), embedding in zip(batch, embed_passages(texts, allow_download=allow_download), strict=True):
            if len(embedding) != BGE_DIMENSIONS:
                raise RuntimeError("BGE 向量维度异常")
            chunk.bge_vector = embedding


class Retriever(Protocol):
    def search(self, db: Session, text: str, context: dict) -> tuple[list[tuple[Chunk, Document]], str]: ...


def procedure_block(rows, query):
    if not re.search(r"程序|流程|步骤", query):
        return None
    topics = [topic for topic in AWARD_TOPICS if topic in query]
    blocks = []
    for heading, doc in rows:
        if (len(heading.text) > 80 or not re.search(r"(?:程序|流程)[：:]?\s*$", heading.text)
                or not (position := re.fullmatch(r"第(\d+)段 · 字符1", heading.location))
                or any(topic not in doc.title + heading.text for topic in topics)):
            continue
        if len((terms(query) - GENERIC) & terms(heading.text)) < 2:
            continue
        following = []
        for chunk, other_doc in rows:
            location = re.fullmatch(r"第(\d+)段 · 字符(\d+)", chunk.location)
            if other_doc.id == doc.id and location and int(location[1]) > int(position[1]):
                following.append((int(location[1]), int(location[2]), chunk))
        following.sort(key=lambda row: row[:2])
        block, expected, paragraph = [], 1, None
        for index, offset, chunk in following:
            if offset != 1 and index == paragraph:
                block.append((chunk, doc))
                continue
            number = re.match(r"\s*\(?(\d+)\)?[.)、]+", unicodedata.normalize("NFKC", chunk.text))
            if not number or int(number[1]) != expected:
                break
            block.append((chunk, doc))
            expected, paragraph = expected + 1, index
        if expected > 2:
            blocks.append(block)
    return blocks[0] if len(blocks) == 1 else None


class HybridRetriever:
    def search(self, db: Session, text: str, context: dict):
        from .vocabulary import expand, published_rules
        original = text
        expansion = expand(text, context['_vocabulary_rules'] if '_vocabulary_rules' in context else published_rules(db))
        if expansion['ambiguous']:
            return [], 'needs_clarification'
        text = expansion['queries'][-1]
        text = normalize_roster_query(text)
        # One pass with canonical names first prevents suffix duplication and cascading rules.
        patterns = [re.escape(t) for t in AWARD_TOPICS] + list(ORAL_TOPICS)
        oral = re.compile('|'.join(f'(?P<r{i}>{pattern})' for i, pattern in enumerate(patterns)))
        replacements = list(AWARD_TOPICS) + list(ORAL_TOPICS.values())
        text = oral.sub(lambda match: replacements[int(match.lastgroup[1:])], text)
        text = normalize_terms(text)
        is_enlistment_eligibility = bool(ENLISTMENT_ELIGIBILITY.fullmatch(text.strip()))
        if is_enlistment_eligibility:
            text = ENLISTMENT_TOPIC
        use_bge = settings().embedding_mode == "bge"
        qterms = terms(text) | terms(original)
        embedding_input = original if original == text else original + '\n' + text
        qvector = embed_query(embedding_input) if use_bge else vector(embedding_input)
        year = context["business_year"]
        # Use an explicitly chosen year for cohort-scoped evidence when no cohort was specified.
        admission = context.get("admission_year") or context.get("selected_year")
        awards = re.findall(r"(?:国家|省|校|院)级[一二三]等奖", text)
        points = bool(re.search(r"加[几多少]*分|加多少|综测.{0,8}分|综合.{0,8}测评.{0,8}分", text))
        numeric_points = points and bool(re.search(r"(?:几|多少)分", text))
        credits = "学分" in text and not points
        topics = re.findall(r"奖学金|请假|转专业|补贴|补助|费用|保研|考试|寝室|处分", text)
        award_topics = [topic for topic in AWARD_TOPICS if topic in text]
        class_codes = set(re.findall(r"(?<![a-z0-9])[a-z]{2,6}\d{4,8}(?![a-z0-9])", text.lower()))
        calendar_query = bool(re.search(r"校历|寒假|暑假|元旦|开学|军训|教学周|放假|新生报到", text))
        counseling_record = COUNSELING_RECORD.search(text)
        # Historical scope is evaluated at year end; current scope uses today's effective date.
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        asof = today.isoformat() if year == today.year else f"{year}-12-31"
        rows = db.execute(select(Chunk, Document).join(Document).where(
            Document.status == "published", Document.parse_status == "ready",
        ).order_by(Document.id, Chunk.location, Chunk.id)).all()
        if block := procedure_block(rows, text):
            return block, ""
        scored, cohort_options = [], {}
        roster_matches, inferred_roster_matches = [], []
        roster_role = "辅导员" if "辅导员" in text and "班主任" not in text else "班主任"
        roster_query = any(role in text for role in ("班主任", "辅导员", "职称")) or bool(class_codes)
        roster_headers = roster_columns(rows) if roster_query else {}
        if scoped := scoped_roster_matches(rows, roster_headers, text):
            return scoped
        for chunk, doc in rows:
            if counseling_record and not (
                    "心理咨询" in chunk.text and re.search(r"档案|保密|记录", chunk.text)):
                continue
            if credits and "学分" not in chunk.text:
                continue
            if numeric_points and not re.search(r"\d+(?:\.\d+)?\s*(?:分|\|)", chunk.text):
                continue
            if any(award not in chunk.text for award in awards):
                continue
            tabular = doc.original_name.lower().endswith((".xlsx", ".csv"))
            exact_class = bool(tabular and class_codes & terms(chunk.text))
            columns = roster_headers.get(sheet_key(chunk)) if tabular and roster_query else None
            roster_row = inferred_row = False
            if columns:
                fields = [field.strip() for field in chunk.text.split("|")]
                class_value = roster_cell(fields, columns, "班级")
                if not class_value or roster_role not in columns:
                    continue
                roster_row = matches_roster(class_value, text)
                inferred_row = not roster_row and inferred_roster_match(class_value, text)
                if not (roster_row or inferred_row):
                    continue
            elif tabular:
                row_terms = {term for term in qterms & terms(chunk.text)
                             if term not in GENERIC and not term.isdigit()}
                if not row_terms:
                    continue
            combined = normalize_terms(doc.title + " " + chunk.text)
            doc_terms = terms(combined)
            if any(topic not in combined for topic in topics):
                continue
            campus_policy = ("校级奖学金" in award_topics and "本科生奖学金评定管理办法" in doc.title)
            if award_topics and not campus_policy and not any(topic in combined for topic in award_topics):
                continue
            common = qterms & doc_terms
            specific = {t for t in common if t not in GENERIC and not t.isdigit()}
            # Vector cannot override absence of lexical evidence.
            lexical = len(common) / max(len(qterms), 1)
            # Imported tables can specify different cohorts for individual rows.
            scope = re.search(r" · 适用(20\d{2}(?:、20\d{2})*)级(及以后)? · 字符\d+$", chunk.location)
            cohorts = [int(value) for value in scope[1].split("、")] if scope else []
            embedding = chunk.bge_vector if use_bge else chunk.vector
            if use_bge and (not embedding or len(embedding) != BGE_DIMENSIONS):
                raise RuntimeError("BGE 索引不完整，请重新生成索引")
            semantic = sum(a * b for a, b in zip(qvector, embedding))
            passage_common = specific & terms(chunk.text)
            semantic_candidate = (use_bge and not tabular and bool(passage_common)
                                  and semantic >= 0.54 and lexical >= 0.06)
            if not (exact_class or roster_row or inferred_row or semantic_candidate) and (not specific or lexical < 0.12):
                continue
            bge_weight = 0.6 if use_bge else 0.2
            score = lexical * (1 - bge_weight) + max(semantic, 0) * bge_weight
            # Scope affects ordering and explanation, never whether a published source is readable.
            score += 0.15 if doc.business_year == year else 0.05 if doc.business_year is None else 0
            if admission and (doc.admission_year == admission or admission in cohorts):
                score += 0.15
            if not (doc.effective_from and doc.effective_from > asof
                    or doc.effective_to and doc.effective_to < asof):
                score += 0.05
            if use_bge:
                score += 0.10 * len(passage_common) / max(len(qterms - GENERIC), 1)
            if campus_policy or any(topic in doc.title for topic in award_topics):
                score += 0.20
            if exact_class or roster_row:
                score += 0.35
            if calendar_query and "校历" in doc.title:
                score += 0.25
            if is_enlistment_eligibility and ENLISTMENT_TOPIC in chunk.text:
                score += 0.25
            if (doc.admission_year or cohorts) and admission is None:
                if points:
                    label = f"{doc.admission_year}级" if doc.admission_year else scope[0].split(" · ")[1].removeprefix("适用")
                    cohort_options.setdefault(label, []).append((score, chunk, doc))
            if roster_row:
                roster_matches.append((chunk, doc))
            elif inferred_row:
                inferred_roster_matches.append((chunk, doc))
            scored.append((score, chunk, doc))
        if roster_matches:
            return roster_matches[:8], "roster_match"
        if inferred_roster_matches:
            if len(inferred_roster_matches) == 1:
                return inferred_roster_matches, "roster_match"
            return [], "needs_clarification"
        if roster_headers and re.search(r"(?:[a-z\u4e00-\u9fff])1\d{4}(?!\d)", text, re.IGNORECASE):
            return [], "needs_clarification"
        if cohort_options:
            options = []
            for matches in cohort_options.values():
                _, chunk, doc = max(matches, key=lambda match: match[0])
                options.append((chunk, doc))
            return options, "cohort_options"
        scored.sort(key=lambda x: x[0], reverse=True)
        if use_bge and len(scored) > 3:
            head, rest, documents = [], [], set()
            for row in scored:
                score, _, doc = row
                if len(head) < 3 and score >= scored[0][0] - 0.08 and doc.id not in documents:
                    head.append(row)
                    documents.add(doc.id)
                else:
                    rest.append(row)
            scored = head + rest
        return [(c, d) for _, c, d in scored[:context.get("_candidate_limit", 8)]], ""


retriever = HybridRetriever()


def citation(chunk: Chunk, doc: Document):
    return {"chunk_id": chunk.id, "document_id": doc.id, "title": doc.title,
            "publisher": doc.publisher, "version": doc.version, "location": chunk.location,
            "excerpt": chunk.text, "business_year": doc.business_year,
            "admission_year": doc.admission_year, "effective_from": doc.effective_from,
            "effective_to": doc.effective_to, "material_mode": doc.material_mode,
            "url": f"/api/sources/{chunk.id}"}
