from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook

from app.db import SessionLocal
from app.models import EmergencyEvent, Issue, Question, Registration


def test_issue_export_group_dates_and_excel(admin_client):
    with SessionLocal() as db:
        student = Registration(role="student", name="演示学生", phone="13800000001")
        db.add(student)
        db.flush()

        def add_issue(key, created_at, text="演示问题", merged_into=None, state="open"):
            question = Question(
                registration_id=student.id, request_id=key, text=text,
                snapshot={"name": student.name, "phone": student.phone},
                model_mode="mock", created_at=created_at,
            )
            db.add(question)
            db.flush()
            db.add(Issue(id=key, question_id=question.id, text=text, reason="missing_material",
                         state=state, merged_into=merged_into))
            return question

        add_issue("root", datetime(2026, 11, 1, 15, 59), "=1+1", state="completed")
        db.flush()
        add_issue("first", datetime(2026, 11, 1, 16), merged_into="root")
        add_issue("last", datetime(2026, 11, 9, 15, 59, 59), merged_into="root")
        add_issue("excluded", datetime(2026, 11, 9, 16), merged_into="root")
        add_issue("single", datetime(2026, 11, 4, 9))
        emergency = add_issue("emergency", datetime(2026, 11, 4, 9))
        db.add(EmergencyEvent(question_id=emergency.id))
        db.commit()

    url = "/api/admin/weekly/export"
    response = admin_client.get(url, params={"start": "2026-11-02", "end": "2026-11-09"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"].startswith("attachment;")
    sheet = load_workbook(BytesIO(response.content)).active
    rows = list(sheet.values)
    assert rows[0] == ("问题组编号", "问题组", "原因", "处理状态", "负责人", "范围内次数", "范围内首次提问（北京时间）", "范围内最近提问（北京时间）")
    assert rows[1] == ("root", "=1+1", "待补充资料", "已解决", None, 2,
                       datetime(2026, 11, 2), datetime(2026, 11, 9, 23, 59, 59))
    assert sheet["B2"].data_type == "s"
    assert [row[0] for row in rows[1:]] == ["root", "single"]

    week = admin_client.get("/api/admin/weekly", params={"day": "2026-11-04"}).json()
    response = admin_client.get(url, params={"start": "2026-11-02", "end": "2026-11-08"})
    week_rows = list(load_workbook(BytesIO(response.content)).active.values)
    assert {row[0] for row in week_rows[1:]} == {group["id"] for group in week["new"]}

    response = admin_client.get(url, params={"start": "2026-11-11", "end": "2026-11-11"})
    assert list(load_workbook(BytesIO(response.content)).active.values) == rows[:1]
    assert admin_client.get(url, params={"start": "2026-11-09", "end": "2026-11-02"}).status_code == 422


def test_issue_export_requires_admin(client):
    response = client.get("/api/admin/weekly/export", params={"start": "2026-11-02", "end": "2026-11-09"})
    assert response.status_code == 401
