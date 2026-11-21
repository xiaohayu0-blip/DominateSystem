"""Validate semantic groups without letting the planner rewrite a user's facts."""
import re


ALL_BRANCHES = re.compile(
    r"(?:全部|全都|都|(?:每|各)(?:个|种|类)(?:都)?|两种都|三种都|四种都)"
    r"(?:讲|说|介绍|解释|列|看看|查|(?:告诉|给)我|(?:想|要)(?:看|听|了解|知道)|可以|行)"
    r"|(?:每|各)(?:个|种|类)(?:都)?(?:是|有)?(?:多少|怎么|如何|需要)"
    r"|(?:分别|各自)(?:是|有)?(?:多少|怎么|如何|需要|要|讲|说|列|介绍)"
    r"|^(?:那|请|我想看)?\s*(?:全部|全都|都要|都想知道|每种都要|每个都要)[？?。！!\s]*$"
)
ALL_BRANCH_REPLY = re.compile(
    r"(?:那就|那|请|麻烦)?(?:帮我)?(?:全部|全都|都|两种都|三种都|四种都|(?:每|各)(?:种|个|类)(?:都)?)"
    r"(?:(?:讲|说|介绍|查|列)(?:一[下遍]|[讲说查])?|要|想知道|给我看看|可以|行)?"
    r"[吧呢呀啊。！!？?\s]*"
)


def named_branch_reply(text: str, options: list[str]) -> str | None:
    """Recognize a short reply naming exactly one of the offered branches."""
    reply = text.strip().rstrip("？?。！!")
    reply = re.sub(r"^(?:那就|那么|我选|我想问|我想了解|就是|那)\s*", "", reply)
    if reply in options:
        return reply
    reply = re.sub(r"(?:这一种|这种|这一类|这类|那种|吧|呢|呀|啊)$", "", reply).strip()
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,10}", reply):
        return None
    aliases = {"病假": "因病", "事假": "因事"}
    term = aliases.get(reply, reply)
    matches = [option for option in options if option == reply or option.startswith(term)
               or aliases.get(option) == term]
    return matches[0] if len(matches) == 1 else None


def question_clauses(text: str) -> list[str]:
    # These boundaries only number pieces of input; the model decides their relationship.
    return [part.strip(" \t\r") for part in re.split(
        r"(?<=[？?；;，。\n])|(?<=,)(?!\d)|(?=另外|以及|同时还想问)", text)
        if re.search(r"\w", part)]


def grouped_questions(clauses: list[str], plan: dict) -> list[str]:
    groups = plan.get("groups") if isinstance(plan, dict) else None
    if not isinstance(groups, list) or not 1 <= len(groups) <= 8:
        raise ValueError("invalid_groups")
    seen, first_indices = [], []
    questions = []
    for group in groups:
        if (not isinstance(group, list) or not group
                or any(type(index) is not int or not 0 <= index < len(clauses) for index in group)
                or group != sorted(set(group))):
            raise ValueError("invalid_group_indices")
        seen.extend(group)
        first_indices.append(group[0])
        questions.append("".join(clauses[index] for index in group))
    if sorted(seen) != list(range(len(clauses))) or first_indices != sorted(first_indices):
        raise ValueError("incomplete_or_reordered_groups")
    return questions
