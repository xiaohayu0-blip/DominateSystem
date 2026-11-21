"""Keep policy scope and lookup context without changing the shared retriever."""
import re

from sqlalchemy import select

from .models import Chunk, Document
from .retrieval import retriever

COMPATIBLE_ALIASES = {r"国励(?:奖学金|奖)?": "国家励志奖学金", r"舍涨": "寝室长"}
SAFETY_ANCHORS = re.compile(r"刷单|出租.{0,4}账号|出借银行卡|银行卡过账")
ACTIVITY_ANCHORS = re.compile(r"晚点名|班会|早操|军训|体测|实习|迎新|缓考")
ATTENDANCE_ACTION = re.compile(r"请假|报备|缺席|参加不了|赶不上|去不了")


def policy_query(text):
    for pattern, replacement in COMPATIBLE_ALIASES.items():
        text = re.sub(pattern, replacement, text)
    return text


def clarification_for(query):
    if re.fullmatch(r"我能评上吗[，,]?帮(?:我)?看看(?:呗|吗)?[？?。]*", query):
        return "请补充具体评选事项、年级及相关条件，才能核对你是否符合要求。"
    if re.fullmatch(r"(?:那个|这个).{0,12}(?:能不能|可以).{0,6}补.{0,5}", query):
        return "请先说明具体事项、要补交或补办的内容，以及对应期限。"
    if re.search(r"找哪个班主任", query) and re.search(r"想问点事|有点事|想问事", query):
        return "请补充具体咨询事项。学业、考研就业可找专业成长班主任，日常管理事务可找日常管理班主任。"
    if (re.search(r"班主任|辅导员|老师", query) and re.search(r"谁|哪位|哪个|名字|班号|查", query)
            and not re.search(r"1\d{4}|(?:\d{2}|20\d{2})级.{0,12}[一二三四五六七八九十\d]+班", query)
            and not re.search(r"职责|负责什么|做什么|哪类|管理办法", query)):
        return "请补充专业全称、入学年级和完整班号，并说明想查班主任还是辅导员。"
    return ""


def scope_context(db, evidence):
    documents = {doc.id for _, doc in evidence if "奖学金" in doc.title}
    if not documents:
        return []
    rows = db.execute(select(Chunk, Document).join(Document).where(
        Document.id.in_(documents))).all()
    result = []
    for chunk, doc in rows:
        if not re.search(r"评定范围|评定对象|适用范围", chunk.text):
            continue
        if len(chunk.text) > 100:
            result.append((chunk, doc))
            continue
        position = re.match(r"第(\d+)段", chunk.location)
        if position:
            following = [(other, d) for other, d in rows if d.id == doc.id
                         and re.match(rf"第{int(position[1]) + 1}段(?: |$)", other.location)]
            result.extend(following)
    return result


def search_policy_evidence(db, query, context):
    query = policy_query(query)
    evidence, special = retriever.search(db, query, context)
    if not evidence and (anchor := SAFETY_ANCHORS.search(query)):
        evidence, special = retriever.search(db, anchor[0], context)
    if ATTENDANCE_ACTION.search(query) and special not in {"roster_match", "needs_clarification"}:
        # Activity instructions may name the person in charge without repeating the requested action.
        activity_hits = []
        for activity in dict.fromkeys(ACTIVITY_ANCHORS.findall(query)):
            if any(activity in chunk.text for chunk, _ in evidence):
                continue
            candidates, _ = retriever.search(db, activity, {**context, "_candidate_limit": 3})
            activity_hits.extend((chunk, doc) for chunk, doc in candidates if activity in chunk.text)
        if activity_hits:
            by_id = {chunk.id: (chunk, doc) for chunk, doc in activity_hits + evidence}
            evidence = list(by_id.values())

    identity_lookup = (
        re.search(r"班主任|辅导员|老师", query)
        and re.search(r"谁|哪位|哪个|名字|班号|查", query)
        and not re.search(r"职责|负责什么|做什么|哪类", query)
    )
    if identity_lookup and special != "roster_match":
        roster = db.scalar(select(Chunk.id).join(Document).where(
            Document.status == "published", Document.parse_status == "ready",
            Chunk.location.startswith("工作表"), Chunk.text.contains("班级"),
            Chunk.text.contains("班主任")))
        if roster:
            return [], "needs_clarification"

    extra = scope_context(db, evidence)
    if special == "roster_match" and re.search(r"考研|就业|学业|专业成长|角色", query):
        extra += db.execute(select(Chunk, Document).join(Document).where(
            Document.status == "published", Document.parse_status == "ready",
            Document.title.contains("班主任管理办法"), Chunk.text.contains("专业成长"),
            Chunk.text.contains("职业规划"))).all()[:1]
    seen = {chunk.id for chunk, _ in evidence}
    return [*evidence, *[(chunk, doc) for chunk, doc in extra if chunk.id not in seen]], special

