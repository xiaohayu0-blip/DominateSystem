"""Keep policy scope and lookup context without changing the shared retriever."""
import re

from sqlalchemy import or_, select

from .competition import competition_level_evidence
from .models import Chunk, Document
from .retrieval import CLASS_NUMBER, retriever

COMPATIBLE_ALIASES = {
    r"国励(?:奖学金|奖)?": "国家励志奖学金",
    r"国奖": "国家奖学金",
    r"舍涨": "寝室长",
    r"找人替考|让别人替考": "请他人代替自己考试",
    r"替考": "代替他人考试",
    r"书(?:弄|搞)?丢(?:了)?|丢了书": "丢失书刊",
    r"升不上级|不能升级": "不予升级",
}
SAFETY_ANCHORS = re.compile(r"刷单|出租.{0,4}账号|出借银行卡|银行卡过账")
ACTIVITY_ANCHORS = re.compile(r"晚点名|班会|早操|军训|体测|实习|迎新|缓考")
ATTENDANCE_ACTION = re.compile(r"请假|报备|缺席|参加不了|赶不上|去不了")
LEAVE_DETAIL = re.compile(r"审批|批准|签字|谁签|找谁|销假|续假")
CLASS_TEACHER = re.compile(
    rf"(?:班级|(?:我(?:们)?|本|这个|那个)班|{CLASS_NUMBER.pattern}|\d{{5,8}}班?)"
    r"(?:的)?(?:哪位|哪个|哪一个)?老师|(?:忘(?:了|记了?)|不记得|不知道)班号"
)


def roster_lookup_question(query):
    class_role = re.search(r"班主任|辅导员|(?<!教)导员", query)
    return ((class_role or "老师" in query and CLASS_TEACHER.search(query))
            and re.search(r"谁|哪位|哪个|名字|班号|查|职称", query)
            and not re.search(r"职责|负责什么|做什么|哪类|管理办法", query))


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


def published_chunks(db, *conditions):
    return db.execute(select(Chunk, Document).join(Document).where(
        Document.status == "published", Document.parse_status == "ready", *conditions,
    ).order_by(Document.id, Chunk.location)).all()


def leave_rule_evidence(db, query):
    if ACTIVITY_ANCHORS.search(query) or not LEAVE_DETAIL.search(query) or not re.search(r"请.{0,20}假|销假|续假", query):
        return []
    rows = published_chunks(db, or_(Document.title.contains("学生手册"), Document.title.contains("请假")),
                            or_(Chunk.text.contains("请假"), Chunk.text.contains("销假"), Chunk.text.contains("续假")))
    result = []
    for chunk, doc in rows:
        compact = re.sub(r"\s+", "", chunk.text)
        tier = ("请假" in compact and "批准" in compact
                and re.search(r"\d+(?:天|周)|[一二三四五六七八九十]+(?:天|周)|审批权限", compact))
        returning = (re.search(r"销假|续假|返校|回来", query)
                     and re.search(r"(?:返校|第.条|到期).{0,100}(?:销假|续假)", compact))
        if tier or returning:
            result.append((chunk, doc))
    return result


def named_policy_evidence(db, query):
    if "提干" in query and re.search(r"优先|党员", query):
        return [(chunk, doc) for chunk, doc in published_chunks(db, Chunk.text.contains("优先推荐"))
                if "提干" in doc.title + chunk.text or re.search(r"入伍|征兵", doc.title)]
    if "退役士兵" in query and "助学金" in query:
        return [(chunk, doc) for chunk, doc in published_chunks(db, Chunk.text.contains("退役士兵"))
                if re.search(r"手册|助学金", doc.title)
                and re.search(r"退役士兵.{0,40}(?:资助标准|每生每年|每人每年)", re.sub(r"\s+", "", chunk.text))]
    if re.search(r"社团指导(?:老师|教师)", query) and re.search(r"补助|奖励|津贴|绩效|发钱", query):
        return [(chunk, doc) for chunk, doc in published_chunks(db, Chunk.text.contains("社团指导教师"))
                if re.search(r"社团指导教师.{0,24}绩效", re.sub(r"\s+", "", chunk.text))]
    if "国家奖学金" in query and re.search(r"打分表|评审.{0,3}打分", query):
        scoring_rows = [(chunk, doc) for chunk, doc in published_chunks(
            db, Document.title.contains("国家奖学金"), Document.title.contains("打分表"),
        ) if re.search(r"\d+\s*分", chunk.text)]
        headings = [(chunk, doc) for chunk, doc in scoring_rows
                    if re.search(r"[（(]\s*\d+\s*分[）)]", chunk.text[:120])]
        if re.search(r"各项|每项|构成|总分|占多少", query) and headings:
            return headings
        return scoring_rows[:8]
    if "奖学金" in query and "公示" in query:
        return [(chunk, doc) for chunk, doc in published_chunks(db, Chunk.text.contains("公示"))
                if ("奖学金" in chunk.text or "奖学金" in doc.title)
                and re.search(r"(?:班级|学院|全校|校内).{0,24}公示.{0,20}(?:\d+|[一二三四五六七八九十]+)\s*(?:个工作日|天)",
                              re.sub(r"\s+", "", chunk.text))]
    if "国家奖学金" in query and re.search(r"小组.{0,8}组长|谁.{0,5}任组长", query):
        return published_chunks(db, Document.title.contains("国家奖学金"), Chunk.text.contains("组长"))
    return []


def search_policy_evidence(db, query, context):
    query = policy_query(query)
    evidence, special = retriever.search(db, query, context)
    if special in {"", "missing_material"}:
        extra = leave_rule_evidence(db, query) or named_policy_evidence(db, query)
        if extra:
            evidence = list({chunk.id: (chunk, doc) for chunk, doc in extra + evidence}.values())
            special = ""
    if not evidence and not special:
        fallback = SAFETY_ANCHORS.search(query) or re.search(r"代替自己考试|代替他人考试|丢失书刊|不予升级", query)
        if fallback:
            evidence, special = retriever.search(db, fallback[0], context)
    if not special:
        evidence = competition_level_evidence(db, query, context, evidence)
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

    if roster_lookup_question(query) and special not in {"roster_match", "roster_no_blank_title"}:
        roster = db.scalar(select(Chunk.id).join(Document).where(
            Document.status == "published", Document.parse_status == "ready",
            Chunk.location.startswith("工作表"), Chunk.text.contains("班级"),
            Chunk.text.contains("班主任")))
        if roster or not evidence:
            return [], "needs_clarification"

    extra = scope_context(db, evidence)
    if special == "roster_match" and re.search(r"考研|就业|学业|专业成长|角色", query):
        extra += db.execute(select(Chunk, Document).join(Document).where(
            Document.status == "published", Document.parse_status == "ready",
            Document.title.contains("班主任管理办法"), Chunk.text.contains("专业成长"),
            Chunk.text.contains("职业规划"))).all()[:1]
    seen = {chunk.id for chunk, _ in evidence}
    return [*evidence, *[(chunk, doc) for chunk, doc in extra if chunk.id not in seen]], special

