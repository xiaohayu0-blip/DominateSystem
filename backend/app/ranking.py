"""Read ranking scores from published scholarship rules, keeping scoring metrics distinct."""
import re
import unicodedata
from decimal import Decimal

from sqlalchemy import select

from .models import Chunk, Document
from .retrieval import citation


def rank_number(value):
    if value.isdigit():
        return int(value)
    digits = "零一二三四五六七八九"
    if "十" in value:
        tens, ones = value.split("十", 1)
        return (digits.index(tens) if tens else 1) * 10 + (digits.index(ones) if ones else 0)
    return digits.index(value)


def ranking_answer(db, query, context):
    query = unicodedata.normalize("NFKC", query)
    rank = re.search(r"(?:专业|成绩|智育|综测|排名|排).{0,8}?第([零一二三四五六七八九十]+|\d+)"
                     r"(?![零一二三四五六七八九十百千万\d.])(?:名)?", query)
    if not rank or not re.search(r"分|得分|计分", query):
        return None
    # Competition standings belong to another scoring system.
    if re.search(r"竞赛|比赛|赛事|团队|队内|国家励志|励志奖|校级奖学金|校奖|攀登", query):
        return None
    rows = db.execute(select(Chunk, Document).join(Document).where(
        Document.status == "published", Document.parse_status == "ready",
        Document.title.contains("国家奖学金"), Document.title.contains("打分表"),
    )).all()
    rules = []
    for chunk, doc in rows:
        label = re.search(r"(智育|综测)排名[（(](\d+(?:\.\d+)?)分[）)]", chunk.text)
        rule = re.search(r"专业排名第一加(\d+(?:\.\d+)?)分[，,]\s*第二名(\d+(?:\.\d+)?)分[，,]"
                         r"\s*按名次依次递减(\d+(?:\.\d+)?)分", chunk.text)
        if label and rule:
            first, second, step = map(Decimal, rule.groups())
            # An inconsistent row cannot be safely extrapolated.
            if first == Decimal(label[2]) and first - step == second and step > 0:
                rules.append((label[1], first, second, step, citation(chunk, doc)))
    if not rules:
        return None
    requested = set(re.findall(r"智育|综测", query))
    moral = "德育" in query
    applicable = [row for row in rules if not requested or row[0] in requested]
    if not applicable:
        return None
    sources = [row[4] for row in applicable]
    if len({source["document_id"] for source in sources}) != 1:
        return {"status": "needs_clarification", "content": "找到多个适用的国奖评审打分表版本，请确认使用哪份表。",
                "citations": sources}
    position = rank_number(rank[1])
    if position < 1:
        return {"status": "needs_clarification", "content": "请提供从第1名开始的有效专业名次。", "citations": sources}
    title = sources[0]["title"]
    scope = "原表未注明适用年度，请结合学院当年通知确认。" if sources[0]["business_year"] is None else ""
    if sources[0]["business_year"] is not None:
        scope += f"所引表对应{sources[0]['business_year']}年度。"
    if sources[0]["admission_year"]:
        scope += f"适用{sources[0]['admission_year']}级。"
    if sources[0]["effective_from"] or sources[0]["effective_to"]:
        scope += f"原表有效期：{sources[0]['effective_from'] or '未注明起日'}至{sources[0]['effective_to'] or '未注明止日'}。"
    options = "；".join(f"{label}排名：专业第1名{first:g}分，第2名{second:g}分，之后每名递减{step:g}分"
                       for label, first, second, step, _ in applicable)
    if moral or len(requested) != 1:
        return {"status": "needs_clarification", "clarification_kind": "ranking_metric",
                "content": f"《{title}》规定，{options}。这些是国家奖学金评审的评分项目。"
                           + ("表中这两项没有写作“德育分”。" if moral else "")
                           + "请确认你问的是智育排名分、综测排名分，还是德育素质分？" + scope,
                "citations": sources}
    label, first, second, step, source = applicable[0]
    points = first - (position - 1) * step
    if points < 0:
        return {"status": "needs_clarification",
                "content": f"《{title}》规定，{options}。按递减规则计算会得到负分，但原表未明确最低分及后续名次处理，"
                           "需向学院确认，暂不推定为0分。" + scope, "citations": [source]}
    explanation = (f"按原表递减规则计算：{first:g}－（{position}－1）×{step:g}＝{points:g}分。"
                   if position > 2 else "")
    return {"status": "answered", "content": f"按《{title}》，专业第{position}名对应的{label}排名分为{points:g}分，"
            "计入国家奖学金评审评分。" + explanation + scope, "citations": [source]}
