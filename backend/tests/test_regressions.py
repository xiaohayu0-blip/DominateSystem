from conftest import signup, student
from app.db import SessionLocal
from test_knowledge import ask, publish


def test_demo_labels_are_not_policy_evidence(client):
    signup(client, student())
    publish(title="【合成演示】奖学金申请办法")
    answer = ask(client, "演示研究工坊需要哪些材料")["answer"]["items"][0]
    assert answer["status"] == "missing_material" and not answer["citations"]


def test_internship_material_cannot_answer_internship_subsidy(client):
    signup(client, student())
    publish(title="实习申请办法", content="实习申请材料须提交学院，人工审核。")
    assert ask(client, "校外实习补贴如何申请")["answer"]["items"][0]["status"] == "missing_material"


def test_ocr_failure_never_published(admin_client):
    from pypdf import PdfWriter
    import io
    data = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(data)
    doc = admin_client.post("/api/admin/documents/upload", files={"file": ("scan.pdf", data.getvalue())}).json()
    assert doc["parse_status"] == "failed" and "OCR" in doc["parse_note"]
    assert admin_client.post('/api/admin/documents/' + doc["id"] + '/publish').status_code == 422


def test_health_schema_and_upload_size(admin_client):
    from app.config import settings
    assert admin_client.get("/api/health").json()["schema_ready"]
    response = admin_client.post("/api/admin/documents/upload", files={"file": ("huge.csv", b"x" * (settings().max_upload_mb * 1024**2 + 1))})
    assert response.status_code in {413, 422}


def test_api_restart_marks_interrupted_answer_as_technical_failure():
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.main import app
    from app.models import Issue, Question, Registration
    with SessionLocal() as db:
        p = Registration(role="student", name="虚构恢复测试", phone="13800000001", major="会计", class_name="一班")
        db.add(p)
        db.flush()
        q = Question(registration_id=p.id, request_id="interrupted-00001", text="奖学金申请",
                     snapshot={}, context={}, model_mode="mock", status="pending")
        db.add(q)
        db.commit()
        question_id = q.id
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
    with SessionLocal() as db:
        assert db.get(Question, question_id).answer["items"][0]["status"] == "system_error"
        assert db.scalar(select(Issue).where(Issue.question_id == question_id)).reason == "system_error"
