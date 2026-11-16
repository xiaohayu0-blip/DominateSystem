"""Compare equivalent source quantities without changing the quoted original."""
import re
import unicodedata
from decimal import Decimal

CHINESE_NUMBER = r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?"
DIGITS = {char: value for value, chars in enumerate(("零〇", "一", "二两", "三", "四", "五", "六", "七", "八", "九"))
          for char in chars}
UNITS = r"%|个百分点|个工作日|工作日|元|分|天|日|月|年|周|人|cm|kg"


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
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"每\s*(?:个)?(工作日|年|月|周|日|天|人)", r"每1\1", normalized)
    # Presentation bullets are not policy amounts, periods, grades or thresholds.
    normalized = re.sub(r"(?<![\d.])\(?\d{1,2}\)?[.)、]+(?=\s*[^\d\s])", "", normalized)
    normalized = re.sub(rf"第({CHINESE_NUMBER})(?=条|款|项|章)",
                        lambda match: "第" + chinese_number(match[1]), normalized)
    normalized = re.sub(rf"({CHINESE_NUMBER})(?=\s*(?:{UNITS}))",
                        lambda match: chinese_number(match[1]), normalized, flags=re.IGNORECASE)
    return normalized


def quantities(value):
    normalized = normalized_numbers(value)
    matches = re.findall(rf"(\d+(?:\.\d+)?)(?:\s*({UNITS}))?", normalized, flags=re.IGNORECASE)
    return {(Decimal(number), "工作日" if unit == "个工作日" else unit.lower()) for number, unit in matches}


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
        result.add((Decimal(number), "工作日" if unit == "个工作日" else unit.lower(), operators[operator]))
    for match in re.finditer(rf"(\d+(?:\.\d+)?)\s*({UNITS})\s*(以上|以下)", normalized):
        number, unit, operator = match.groups()
        result.add((Decimal(number), "工作日" if unit == "个工作日" else unit.lower(),
                    ">=" if operator == "以上" else "<="))
    return result
