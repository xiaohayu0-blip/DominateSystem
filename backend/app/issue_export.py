from datetime import timedelta, timezone
from io import BytesIO

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from sqlalchemy import func, select

from .models import Issue, Question
from .services import report_date_bounds, report_issue_roots

REASONS = {
    "missing_material": "待补充资料", "needs_clarification": "待澄清", "document_conflict": "资料冲突",
    "web_unavailable": "联网缺少可靠来源", "validation_failed": "证据校验失败", "system_error": "技术故障",
}
STATES = {
    "open": "待处理", "in_progress": "处理中", "awaiting_material": "待补充资料",
    "awaiting_retest": "待复测", "completed": "已解决", "dismissed": "无需处理",
}


def text_cell(sheet, value):
    cell = WriteOnlyCell(sheet, value=ILLEGAL_CHARACTERS_RE.sub("", value))
    cell.data_type = "s"
    return cell


def issue_export(db, start_day, end_day):
    if end_day < start_day:
        raise HTTPException(422, "结束日期不能早于开始日期")
    try:
        start, end, local_start, _ = report_date_bounds(start_day, end_day + timedelta(days=1))
    except OverflowError as exc:
        raise HTTPException(422, "日期超出可导出范围") from exc

    group_id = func.coalesce(Issue.merged_into, Issue.id)
    occurrences = (
        select(group_id.label("group_id"), func.count().label("total"),
               func.min(Question.created_at).label("first"), func.max(Question.created_at).label("last"))
        .join(Question, Issue.question_id == Question.id)
        .where(Question.created_at >= start, Question.created_at < end)
        .group_by(group_id)
        .subquery()
    )
    groups = (
        report_issue_roots()
        .with_only_columns(Issue.id, Issue.text, Issue.reason, Issue.state, Issue.owner,
                           occurrences.c.total, occurrences.c.first, occurrences.c.last)
        .join(occurrences, occurrences.c.group_id == Issue.id)
        .order_by(occurrences.c.last.desc(), Issue.id)
        .execution_options(yield_per=1000)
    )

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("问题组")
    sheet.freeze_panes = "B2"
    for column, width in zip("ABCDEFGH", (35, 60, 22, 15, 20, 14, 30, 30), strict=True):
        sheet.column_dimensions[column].width = width
    sheet.append(("问题组编号", "问题组", "原因", "处理状态", "负责人", "范围内次数",
                  "范围内首次提问（北京时间）", "范围内最近提问（北京时间）"))
    for key, question, reason, state, owner, count, first, last in db.execute(groups):
        dates = [WriteOnlyCell(sheet, value=value.replace(tzinfo=timezone.utc)
                               .astimezone(local_start.tzinfo).replace(tzinfo=None))
                 for value in (first, last)]
        for cell in dates:
            cell.number_format = "yyyy-mm-dd hh:mm:ss"
        sheet.append([text_cell(sheet, value) for value in (
            key, question, REASONS.get(reason, reason), STATES.get(state, state), owner,
        )] + [count, *dates])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
