"""Compare equivalent source quantities without changing the quoted original."""
import re
import unicodedata
from decimal import Decimal

CHINESE_NUMBER = r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?"
DIGITS = {char: value for value, chars in enumerate(("零〇", "一", "二两", "三", "四", "五", "六", "七", "八", "九"))
          for char in chars}
UNITS = r"%|个百分点|个工作日|工作日|个月|学年|学期|小时|分钟|块钱|元|块|次|项|字|级|楼|岁|时|分|天|日|月|年|周|人|个|cm|kg"
UNIT_ALIASES = {"个工作日": "工作日", "个月": "月", "学年": "年", "小时": "时",
                "块钱": "元", "块": "元", "个": ""}


def chinese_number(value):
    integer, dot, fraction = value.partition("点")
    if not any(char in integer for char in "十百千万亿"):
        result = int("".join(str(DIGITS[char]) for char in integer))
    else:
        total, section, digit = 0, 0, 0
        for char in integer:
            if char in DIGITS:
                digit = DIGITS[char]
            elif char in "十百千":
                section += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                digit = 0
            else:
                section += digit
                unit = {"万": 10000, "亿": 100000000}[char]
                total = (total + section) * unit if unit == 100000000 else total + section * unit
                section, digit = 0, 0
        result = total + section + digit
    return str(result) + ("." + "".join(str(DIGITS[char]) for char in fraction) if dot else "")


def normalized_numbers(value):
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    normalized = normalized.replace("第一时间", "及时").replace("一个人说了算", "独自决定")
    normalized = re.sub(r"每(?:名学生|个学生|生)", "每人", normalized)
    normalized = re.sub(rf"({CHINESE_NUMBER}|\d+)个(?=人|次|字|项)", r"\1", normalized)
    normalized = re.sub(r"不重复(?:加分|计分)", "只计1次", normalized)
    normalized = re.sub(r"每\s*(?:个)?(工作日|学年|学期|年|月|周|日|天|人)", r"每1\1", normalized)
    # Presentation bullets are not policy amounts, periods, grades or thresholds.
    normalized = re.sub(r"(?<![\d.])\(?\d{1,2}\)?[.)、]+(?=\s*[^\d\s])", "", normalized)
    normalized = re.sub(rf"第({CHINESE_NUMBER})(?=条|款|项|章)",
                        lambda match: "第" + chinese_number(match[1]), normalized)
    normalized = re.sub(rf"({CHINESE_NUMBER})(?=\s*(?:{UNITS}))",
                        lambda match: chinese_number(match[1]), normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(\d+)年级", r"\1级", normalized)
    normalized = re.sub(r"大([一二三四])", lambda match: chinese_number(match[1]) + "级", normalized)
    normalized = re.sub(r"(\d{1,2})月(\d{1,2})(?:号|(?![\d日]))", r"\1月\2日", normalized)

    def afternoon(match):
        start, end = match.groups()
        start = int(start) if start.isdigit() else int(chinese_number(start))
        result = f"{start % 12 + 12}时0分钟"
        if end:
            end = int(end) if end.isdigit() else int(chinese_number(end))
            result += f"到{end % 12 + 12}时0分钟"
        return result

    normalized = re.sub(rf"下午\s*(\d{{1,2}}|{CHINESE_NUMBER})[点时]"
                        rf"(?:\s*(?:到|至|[-—])\s*(\d{{1,2}}|{CHINESE_NUMBER})[点时])?",
                        afternoon, normalized)
    if "下午" in value and not re.search(r"上午|早上|中午|晚上", value):
        normalized = re.sub(r"(?<![\d.])(\d{1,2})点(?!\d)",
                            lambda match: f"{int(match[1]) % 12 + 12}时0分钟", normalized)
    normalized = re.sub(r"(\d{1,2}):([0-5]\d)", r"\1时\2分钟", normalized)
    return normalized


def quantities(value):
    normalized = normalized_numbers(value)
    matches = re.findall(rf"(\d+(?:\.\d+)?)(?:\s*({UNITS}))?", normalized, flags=re.IGNORECASE)
    return {(Decimal(number), UNIT_ALIASES.get(unit, unit.lower())) for number, unit in matches}


def scenario_quantities(answer, question, policy_text):
    """Allow attributed input facts, never promote a proposed number to a rule."""
    facts = quantities(question)
    attributed = re.findall(
        r"(?:你(?:们)?(?:写了?|排(?:在)?|有|是|给出|提到的?|说的?|(?=\d))|题设|按你(?:给出|描述)的?|"
        r"只晚|晚到|住到|只住到|别为了|不管|不等于|不是固定|不保证|无法确认|查不到|没法确认)"
        r"[^，。！？\n]{0,40}", answer)
    attributed += re.findall(
        r"(?:不是|并非|不按|不等于|没有)[^，。！？\n]{0,40}|"
        r"[^，。！？\n]{0,30}(?:还评不了|不能参评|不符合|不满足)", answer)
    allowed = set()
    for phrase in attributed:
        for number, unit in quantities(phrase):
            if (number, unit) in facts or unit == "元" and (number, "") in facts:
                allowed.add((number, unit))
    # A word-count shortfall is a deterministic difference of an input count and a source minimum.
    minimums = {number for number, unit, operator in comparisons(policy_text)
                if unit == "字" and operator == ">="}
    word_counts = {number for number, unit in facts if unit == "字"}
    for match in re.finditer(r"(?:差|补)(?:至少|最少)?\s*(\d+)\s*(?:个)?字", answer):
        gap = Decimal(match[1])
        if any(minimum - count == gap and gap > 0 for minimum in minimums for count in word_counts):
            allowed.add((gap, "字"))
    return allowed


def comparisons(value):
    operators = {"不少于": ">=", "不低于": ">=", "至少": ">=", "不小于": ">=", "≥": ">=", ">=": ">=",
                 "不超过": "<=", "不高于": "<=", "至多": "<=", "最多": "<=", "≤": "<=", "<=": "<=",
                 "超过": ">", "高于": ">", "大于": ">", ">": ">",
                 "少于": "<", "低于": "<", "小于": "<", "不足": "<", "<": "<"}
    pattern = "|".join(re.escape(word) for word in sorted(operators, key=len, reverse=True))
    normalized = normalized_numbers(value)
    result = set()
    for match in re.finditer(rf"({pattern})\s*(?:为|是)?\s*(\d+(?:\.\d+)?)\s*({UNITS})", normalized):
        operator, number, unit = match.groups()
        result.add((Decimal(number), UNIT_ALIASES.get(unit, unit.lower()), operators[operator]))
    for match in re.finditer(rf"(\d+(?:\.\d+)?)\s*({UNITS})\s*(以上|以下)", normalized):
        number, unit, operator = match.groups()
        result.add((Decimal(number), UNIT_ALIASES.get(unit, unit.lower()),
                    ">=" if operator == "以上" else "<="))
    return result
