"""Determine whether evidence applies before passing it to the answer model."""

import re
from datetime import date
from urllib.parse import urlsplit

from .config import settings

UNDERGRAD = re.compile(r"本科|高职|专科|大[一二三四](?!年)")
GRADUATE = re.compile(r"研究生|硕士|博士")
PERIOD = re.compile(r"(20\d{2})(?:[—–~-](20\d{2}))?\s*(?:学年|年度|年[春秋]季)")
NATIONAL_DOMAINS = ("moe.gov.cn", "mof.gov.cn", "mod.gov.cn", "nhsa.gov.cn", "mohrss.gov.cn")
LOCAL_DOMAINS = ("hubei.gov.cn", "wuhan.gov.cn")


def audience(text):
    undergrad, graduate = bool(UNDERGRAD.search(text)), bool(GRADUATE.search(text))
    if undergrad != graduate:
        return "本科及专科" if undergrad else "研究生"
    return None


def requested_audience(question):
    result = audience(question)
    if result is None and not GRADUATE.search(question) and re.search(r"校级奖学金|学业奖学金|综测", question):
        return "本科及专科"
    return result


def policy_day(question, year, today):
    if match := re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", question):
        try:
            return date(*(int(value) for value in match.groups())).isoformat()
        except ValueError:
            pass
    return today.isoformat() if year == today.year else f"{year}-12-31"


def scope_citations(citations, question, context, today):
    year = context.get("selected_year") or context["business_year"]
    as_of = policy_day(question, year, today)
    expected = requested_audience(question)
    admission = context.get("admission_year")
    ranked = []
    for source in citations:
        target = audience(source["title"])
        start, end = source["effective_from"], source["effective_to"]
        business_year = source["business_year"]
        if expected and target and expected != target:
            label = "other_audience"
        elif admission and source.get("admission_year") not in (None, admission):
            label = "other_cohort"
        elif start and start > as_of:
            label = "not_yet_effective"
        elif end and end < as_of:
            label = "expired"
        elif (start or end) and not (business_year and business_year > year and not start):
            label = "applicable"
        elif business_year == year:
            label = "matching_year"
        elif business_year is not None:
            label = "other_year"
        else:
            label = "undated"
        ranked.append({**source, "policy_applicability": label, "policy_audience": target})
    order = {"applicable": 0, "matching_year": 1, "undated": 2, "other_year": 3,
             "expired": 4, "not_yet_effective": 4, "other_cohort": 5, "other_audience": 5}
    return sorted(ranked, key=lambda source: (
        order[source["policy_applicability"]],
        bool(re.search(r"宣传|新闻|报道", source["title"])),
    ))


def host_matches(host, domain):
    return host == domain or host.endswith("." + domain)


def campus_web_sources(sources, question, search_query=None):
    expected = requested_audience(question)
    year = re.search(r"(20\d{2})年(?!级)", search_query or question)
    official = settings().school_official_domain
    ranked = []
    for source in sources:
        host = (urlsplit(source["url"]).hostname or "").lower().rstrip(".")
        title = source["title"]
        school = bool(official and host_matches(host, official))
        local = any(host_matches(host, domain) for domain in LOCAL_DOMAINS)
        national = host in ("gov.cn", "www.gov.cn") or any(host_matches(host, domain) for domain in NATIONAL_DOMAINS)
        if not (school or local or national):
            continue
        target = audience(title)
        if expected and target and expected != target:
            continue
        period = PERIOD.search(title)
        if year and period and year[1] not in period.groups():
            continue
        rank = 0 if school else 1 if local else 2
        if re.search(r"全国|国家规定|国家政策", question) and national:
            rank = 0
        ranked.append((rank, source))
    return [source for _, source in sorted(ranked, key=lambda item: item[0])]
