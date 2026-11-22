"""Read scholarship amounts and admission thresholds from the published clauses."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from .models import Chunk, Document
from .policy_scope import audience
from .question_planning import ALL_BRANCHES
from .retrieval import citation


AWARD_NAMES = {
    "国家励志奖学金": r"国家励志奖学金|励志奖学金|国励(?:奖学金|奖)?",
    "国家奖学金": r"国家(?:级)?奖学金|国奖",
    "攀登奖学金": r"攀登奖学金",
    "学业奖学金": r"学业奖学金|校级奖学金|校奖",
}
AMOUNT = re.compile(r"多少钱|多少元|多少(?:的)?奖金|金额|奖金|奖励标准|奖励多少|发多少|(?:有|能拿|能领|能得)多少[呀啊呢吗？?。！!]*$|奖学金是多少")
UNNAMED_AWARD = re.compile(
    r"^(?:请问|想问)?\s*(?:(?:20\d{2}\s*年(?:度)?|今年|去年)\s*)?"
    r"(?:(?:我们学校|学校|学院|本校|本院)的?\s*)?奖学金")
MONEY = re.compile(r"(?<![\d.])\d+(?:[ ,，]\d{3})*(?:\.\d+)?\s*元")
RANK = r"智育(?:成绩)?排名"
COMPREHENSIVE = r"综合(?:测评|素质评价)(?:成绩)?排名"
AWARD_CHOICES = "每种奖学金的金额不同。你想查国家奖学金、国家励志奖学金、学业奖学金，还是攀登奖学金？"


def award_choice(text):
    text = re.sub(r"^(?:那|是|我想查|我问的是)\s*", "", text.strip())
    text = text.rstrip("呀啊呢吗？?。！! ")
    return next((name for name, pattern in AWARD_NAMES.items() if re.fullmatch(pattern, text)), None)


def scholarship_request(query):
    if not re.search(r"奖学金|国奖|国励|校奖", query) or re.search(r"研究生|硕士|博士", query):
        return None
    names = [name for name, pattern in AWARD_NAMES.items() if re.search(pattern, query)]
    amount = AMOUNT.search(query)
    other_topic = re.search(r"申请材料|怎么申请|如何申请|申请流程|截止|发放时间|什么时候发|名额|多少人|"
                            r"加几分|加多少分|计多少分|评审程序|德育成绩|德育积分|另外|还有", query)
    if amount and not other_topic and not re.search(r"排名|条件|要求|以及|另外", query):
        if len(names) == 1:
            return "amount", names[0]
        if not names and UNNAMED_AWARD.search(query):
            return "amount", None
    if (names == ["国家奖学金"] and not amount and not other_topic and "排名" in query
            and "智育" in query and re.search(r"综合测评|综合素质评价", query)
            and re.search(r"要求|条件|申请|前百分之|前\d|前几|前多少", query)):
        return "ranking", names[0]
    return None


def requested_award_amounts(query, *, explicit=False):
    if not is_award_amount_question(query) or not (explicit or ALL_BRANCHES.search(query)):
        return []
    names = [name for name, pattern in AWARD_NAMES.items() if re.search(pattern, query)]
    if len(names) >= 2:
        return names
    if not names and re.search(r"(?<![\u4e00-\u9fff])奖学金|(?:每种|各种|各类|所有|本科生|学校)奖学金", query):
        return list(AWARD_NAMES)
    return []


def is_award_amount_question(query):
    return bool(re.search(r"奖学金|国奖|国励|校奖", query) and AMOUNT.search(query)
                and not re.search(r"研究生|硕士|博士|申请|材料|截止|发放时间|什么时候发|名额|排名|条件|要求|加分|评审", query))


def matching_clause(text, kind, award):
    if kind == "ranking":
        return (re.search(RANK, text) and re.search(COMPREHENSIVE, text)
                and "前10%" in text and "前30%" in text and "不及格" in text and "证明材料" in text)
    if award == "学业奖学金":
        return all(re.search(rf"{grade}等奖[：:\s]+{MONEY.pattern}", text) for grade in "一二三")
    return (award in text and MONEY.search(text)
            and re.search(r"奖励标准|奖金|每生每年|每人每年|元/学年/人", text))


def scholarship_lookup(db, request, context, *, compact=False):
    kind, award = request
    if award is None:
        return {"status": "needs_clarification", "content": AWARD_CHOICES, "citations": [],
                "clarification_kind": "scholarship_amount"}
    title = "本科生奖学金评定管理办法" if award == "学业奖学金" else award
    rows = db.execute(select(Chunk, Document).join(Document).where(
        Document.status == "published", Document.parse_status == "ready",
        or_(Document.title.contains(title), Document.title.contains("学生手册")),
    ).order_by(Document.id, Chunk.location, Chunk.id)).all()
    year = context["business_year"]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    asof = today.isoformat() if year == today.year else f"{year}-12-31"
    candidates = []
    for chunk, document in rows:
        if (audience(document.title) == "研究生"
                or document.business_year not in (None, year) and not document.effective_from
                or document.admission_year not in (None, context.get("admission_year"))
                or document.effective_from and document.effective_from > asof
                or document.effective_to and document.effective_to < asof
                or not matching_clause(chunk.text, kind, award)):
            continue
        text = re.sub(r"\s+", " ", chunk.text).strip()
        specialized = title in document.title
        if not specialized:
            clauses = [sentence.strip() for sentence in re.split(r"(?<=[。！？；])", text)
                       if matching_clause(sentence, kind, award)]
            if len(clauses) != 1:
                continue
            text = clauses[0]
        text = re.sub(r"^[（(][一二三四五六七八九十]+[）)]\s*", "", text)
        prefix = f"按{year}年版《{document.title.rsplit('.', 1)[0]}》：\n" if document.business_year else ""
        if compact and prefix:
            prefix = f"按{year}年版，"
        if compact:
            clauses = [sentence.strip() for sentence in re.split(r"(?<=[。！？])|\n", chunk.text)
                       if matching_clause(sentence, kind, award)]
            if len(clauses) == 1:
                text = clauses[0]
        candidates.append(((document.business_year == year, specialized), prefix + text, chunk, document))
    if candidates:
        best = max(row[0] for row in candidates)
        tied = {text: (chunk, doc) for exact, text, chunk, doc in candidates if exact == best}
        if len(tied) == 1:
            text, (chunk, doc) = next(iter(tied.items()))
            return {"status": "answered", "content": text, "citations": [citation(chunk, doc)]}
        if len(tied) > 1:
            if compact:
                return {"status": "document_conflict", "content": "找到不同的金额标准，需核对适用版本。",
                        "citations": [citation(chunk, doc) for chunk, doc in tied.values()]}
            return None
    if kind == "amount":
        return {"status": "missing_material", "content": "知识库暂时没有可核实的这项奖学金金额条款。",
                "citations": [], "search_question": f"{year}年 本科生{award} 奖励金额 每生每学年"}
    return None
