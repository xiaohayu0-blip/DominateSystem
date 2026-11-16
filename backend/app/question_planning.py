"""Validate semantic groups without letting the planner rewrite a user's facts."""
import re


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
