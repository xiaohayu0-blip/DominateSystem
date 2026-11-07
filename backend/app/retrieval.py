import hashlib
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Chunk, Document

STOP = set("的了是在有与和及或请问可以如何怎么什么哪些多少时候需要我们我要了解一下")
SYNONYMS = {"综测": "综合测评", "奖助": "奖学金", "缓考": "延期考试"}
GENERIC = {"演示", "合成", "申请", "请材", "材料", "包括", "需要", "条件", "流程", "提交", "时间", "办理",
           "如何", "什么", "哪些", "办法", "规定", "说明", "学生", "学院", "制度", "要求", "咨询"}


def terms(text: str) -> set[str]:
    for key, value in SYNONYMS.items():
        text = text.replace(key, value)
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    result = {w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text)}
    for run in chinese:
        result.update(run[i:i + 2] for i in range(len(run) - 1)
                      if not all(c in STOP for c in run[i:i + 2]))
    return result


def vector(text: str, dimensions=128) -> list[float]:
    values = [0.0] * dimensions
    for term in terms(text):
        raw = hashlib.blake2b(term.encode(), digest_size=8).digest()
        values[int.from_bytes(raw[:4]) % dimensions] += 1 if raw[4] % 2 else -1
    norm = math.sqrt(sum(x * x for x in values)) or 1
    return [round(x / norm, 6) for x in values]


def build_chunks(db: Session, document: Document, parts: list[tuple[str, str]]):
    from sqlalchemy import delete
    db.execute(delete(Chunk).where(Chunk.document_id == document.id))
    for location, raw in parts:
        # Keep source location; overlap prevents arbitrary chunk cuts hiding nearby conditions.
        for i in range(0, len(raw), 650):
            text = raw[i:i + 800].strip()
            if text:
                db.add(Chunk(document_id=document.id, location=f"{location} · 字符{i + 1}",
                             text=text, vector=vector(text)))
    db.flush()


class Retriever(Protocol):
    def search(self, db: Session, text: str, context: dict) -> tuple[list[tuple[Chunk, Document]], str]: ...


class HybridRetriever:
    def search(self, db: Session, text: str, context: dict):
        qterms, qvector = terms(text), vector(text)
        year = context["business_year"]
        # Use an explicitly chosen year for cohort-scoped evidence when no cohort was specified.
        admission = context.get("admission_year") or context.get("selected_year")
        awards = re.findall(r"(?:国家|省|校|院)级[一二三]等奖", text)
        points = bool(re.search(r"加[几多少]*分|加多少|综测.{0,8}分|综合.{0,8}测评.{0,8}分", text))
        credits = "学分" in text and not points
        topics = re.findall(r"奖学金|请假|转专业|补贴|补助|费用|保研|考试|宿舍|处分", text)
        # Historical scope is evaluated at year end; current scope uses today's effective date.
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        asof = today.isoformat() if year == today.year else f"{year}-12-31"
        rows = db.execute(select(Chunk, Document).join(Document).where(
            Document.status == "published", Document.parse_status == "ready")).all()
        scored, grade_missing, cohort_options = [], False, {}
        for chunk, doc in rows:
            if credits and "学分" not in chunk.text:
                continue
            if points and not re.search(r"\d+(?:\.\d+)?\s*(?:分|\|)", chunk.text):
                continue
            if any(award not in chunk.text for award in awards):
                continue
            if doc.original_name.lower().endswith((".xlsx", ".csv")):
                row_terms = {term for term in qterms & terms(chunk.text)
                             if term not in GENERIC and not term.isdigit()}
                if not row_terms:
                    continue
            if doc.business_year is not None and doc.business_year != year:
                continue
            if doc.effective_from and doc.effective_from > asof:
                continue
            if doc.effective_to and doc.effective_to < asof:
                continue
            doc_terms = terms(doc.title + " " + chunk.text)
            if any(topic not in doc.title + chunk.text for topic in topics):
                continue
            common = qterms & doc_terms
            specific = {t for t in common if t not in GENERIC and not t.isdigit()}
            # Vector cannot override absence of lexical evidence.
            lexical = len(common) / max(len(qterms), 1)
            if not specific or lexical < 0.12:
                continue
            # Imported tables can specify different cohorts for individual rows.
            scope = re.search(r" · 适用(20\d{2}(?:、20\d{2})*)级(及以后)? · 字符\d+$", chunk.location)
            cohorts = [int(value) for value in scope[1].split("、")] if scope else []
            semantic = sum(a * b for a, b in zip(qvector, chunk.vector))
            score = lexical * 0.8 + max(semantic, 0) * 0.2
            if (doc.admission_year or cohorts) and admission is None:
                grade_missing = True
                if points:
                    label = f"{doc.admission_year}级" if doc.admission_year else scope[0].split(" · ")[1].removeprefix("适用")
                    cohort_options.setdefault(label, []).append((score, chunk, doc))
                continue
            if doc.admission_year and doc.admission_year != admission:
                continue
            if scope and (admission < cohorts[0] if scope[2] else admission not in cohorts):
                continue
            scored.append((score, chunk, doc))
        if cohort_options:
            options = []
            for matches in cohort_options.values():
                _, chunk, doc = max(matches, key=lambda match: match[0])
                options.append((chunk, doc))
            return options, "cohort_options"
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(c, d) for _, c, d in scored[:8]], "needs_clarification" if grade_missing and not scored else ""


retriever = HybridRetriever()


def citation(chunk: Chunk, doc: Document):
    return {"chunk_id": chunk.id, "document_id": doc.id, "title": doc.title,
            "publisher": doc.publisher, "version": doc.version, "location": chunk.location,
            "excerpt": chunk.text, "business_year": doc.business_year,
            "admission_year": doc.admission_year, "effective_from": doc.effective_from,
            "effective_to": doc.effective_to, "material_mode": doc.material_mode,
            "url": f"/api/sources/{chunk.id}"}
