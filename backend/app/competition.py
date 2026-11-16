import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from .models import Chunk, Document
from .retrieval import citation

BASE_LOCATION = "第163页 · 学科竞赛计分结构化 · 国家级"
TEAM_LOCATION = "第165页 · 学科竞赛计分结构化 · 团队系数"
PARTICIPATION_LOCATION = "第165页 · 学科竞赛计分结构化 · 未获奖参与"
SCORE_LOCATION_MARKER = " · 学科竞赛计分结构化 · "
ORDINALS = "一二三四五六七八九十"
SOURCE_CATEGORIES = {"A+类": "A+类学科竞赛", "其他A类": "其他A类学科竞赛",
                     "B/C类": "B/C类学科竞赛", "文体竞赛": "文体竞赛"}
TEAM_SOURCE_NOTE = "团队项目由项目负责人根据实际情况确定，工作量分配系数的总和为1.0或按照如下办法分配。"
TEAM_SIZE_REPLY = re.compile(r"(?:团队|队伍)?(?:总共|一共|共|有)?\s*([二三四五六七八]|[2-8])\s*(?:个)?人(?:团队|合作|组队)?")


def ordinal(value):
    return int(value) if value.isascii() else ORDINALS.index(value) + 1


@dataclass
class CompetitionScore:
    citations: list[dict]
    expression: str = ""
    result: str = ""
    assumptions: str = ""
    clarification: str = ""
    request_size: bool = False
    request_award: bool = False


def competition_score(db, query, context):
    query = unicodedata.normalize("NFKC", query).upper()
    category = re.search(r"(A\s*\+|A|B\s*/\s*C|B|C)\s*(?:类|(?=学科竞赛|竞赛|赛事|比赛))|文体(?:竞赛|赛事|比赛)", query)
    level = re.search(r"国家级|国赛|省市区级|省级|市级|区级|省赛|市赛|区赛|校级|校赛", query)
    participation = re.search(r"未获奖|没获奖|没拿奖|没有获奖", query)
    if not category or not (level or participation) or not re.search(r"竞赛|赛事|比赛|[国省市区校]赛", query):
        return None
    team = re.search(r"团队|队伍|队内", query)
    if not re.search(r"加分|多少分|计分|得分|加几分", query):
        return None
    award_text = query[:team.start()] if team else query
    team_text = query[team.start():] if team else ""
    award = re.search(r"[特一二三]等奖", query)
    ranked_award = re.search(r"第\s*([一二三四五六七八九十百零两]+|\d+)\s*名", award_text) if not award else None
    equivalent = dict(zip("一二三123", "一二三一二三")).get(ranked_award[1]) if ranked_award else None
    if not (award or equivalent or participation):
        return CompetitionScore([], clarification="请告诉我证书上的获奖等级（特等奖、一等奖、二等奖、三等奖或同级奖）。手册按奖项等级计分。",
                                request_award=True)
    rank = re.search(r"第\s*([一二三四五六七八九十]|\d+)\s*(?:名|位|顺位)", team_text)
    size = re.search(r"([一二三四五六七八九十]|\d+)\s*(?:(?:个)?人|位(?=合作|组队))", query)
    rows = db.execute(select(Chunk, Document).join(Document).where(
        Document.status == "published", Document.parse_status == "ready",
        Document.business_year == context["business_year"],
        Chunk.location.contains(SCORE_LOCATION_MARKER),
    )).all()
    category_code = re.sub(r"\s+", "", category[1] or "")
    series = {"A+": "A+类", "A": "其他A类"}.get(category_code, "B/C类" if category_code else "文体竞赛")
    if participation:
        if series == "文体竞赛":
            return None
        sources = [(chunk, doc) for chunk, doc in rows if chunk.location == PARTICIPATION_LOCATION]
        if len(sources) != 1:
            return None
        source = citation(*sources[0])
        prefix = "参与A+赛事" if category_code == "A+" else "参与其他类"
        line = next((line for line in source["excerpt"].splitlines() if line.startswith(prefix)), "")
        numbers = re.search(r"每人(\d+分（\d+\.\d+分）)", line)
        if not numbers:
            return None
        source["excerpt"] = line + "\n同一学生参与多个项目，不累加。"
        return CompetitionScore([source], numbers[1], numbers[1], "仅适用于团队参加学科竞赛但未获奖的情形")
    level_name = "国家级" if level[0] in {"国家级", "国赛"} else "校级" if level[0] in {"校级", "校赛"} else "省市区级"
    award_name = award[0] if award else f"{equivalent}等奖"
    category_label = rf"(?:{re.escape(SOURCE_CATEGORIES[series])}|{re.escape(series)})"
    level_label = r"(?:省（市、区）级|省市区级)" if level_name == "省市区级" else level_name
    rule = re.compile(rf"^{category_label}{level_label}{award_name}或同级奖：([0-9]+)分。$", re.MULTILINE)
    bases = [(chunk, doc, match) for chunk, doc in rows if (match := rule.search(chunk.text))]
    if len(bases) != 1:
        return None
    base_chunk, document, base_line = bases[0]
    base = citation(base_chunk, document)
    base["excerpt"] = base_line[0]
    award_condition = f"第{equivalent}名被认定为{award_name}或同级奖" if ranked_award else ""
    if not team:
        return CompetitionScore([base], f"{base_line[1]}分", f"{base_line[1]}分",
                                "此数值是表中赛事基准分" + (f"，前提是{award_condition}" if award_condition else ""))
    if not rank:
        return CompetitionScore([], clarification="请补充团队总人数和你的成员顺位。")
    team_rows = [(chunk, doc) for chunk, doc in rows if doc.id == document.id and chunk.location == TEAM_LOCATION]
    if len(team_rows) != 1:
        return None
    shares = citation(*team_rows[0])
    position = ordinal(rank[1])
    participants = ordinal(size[1]) if size else None
    candidates = []
    for line in shares["excerpt"].splitlines():
        row = re.fullmatch(r"(?:([2-8])人团队|([二三四五六七八])位合作)：([0-9.]+(?:、[0-9.]+)+)。", line)
        if not row:
            continue
        values = row[3].split("、")
        team_size = ordinal(row[1] or row[2])
        if 1 <= position <= len(values) and (participants is None or team_size == participants):
            candidates.append((line, team_size, Decimal(values[position - 1])))
    if not candidates:
        return CompetitionScore([], clarification="请核对团队总人数和成员顺位。手册的分配表列出2至8人团队。")
    if len({value for _, _, value in candidates}) != 1:
        return CompetitionScore([], clarification="这个顺位的分配系数会随团队总人数变化。请问团队一共几个人？",
                                request_size=True)
    coefficient = format(candidates[0][2].normalize(), "f")
    points = format((Decimal(base_line[1]) * Decimal(coefficient)).normalize(), "f")
    base["excerpt"] = base_line[0]
    shares["excerpt"] = "\n".join(["各行从第1位开始依序列出系数。",
                                   *(line for line, _, _ in candidates),
                                   TEAM_SOURCE_NOTE])
    assumptions = f"按手册分配表，团队人数为{'、'.join(str(n) for _, n, _ in candidates)}人，成员顺位为第{position}位"
    if award_condition:
        assumptions += "，且" + award_condition
    return CompetitionScore([base, shares], f"{base_line[1]}分 × {coefficient} = {points}分",
                            f"{points}分", assumptions)
