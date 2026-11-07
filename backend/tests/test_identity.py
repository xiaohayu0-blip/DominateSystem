from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ChildEntry, Question, Registration
from conftest import signup, parent, student


def test_account_login_requires_password_and_restores_the_same_profile(client):
    assert client.post("/api/register", json=student()).status_code == 422
    account = signup(client, student(name="演示本人"))
    assert account.status_code == 200
    profile_id = account.json()["id"]
    assert signup(client, student(name="演示本人")).status_code == 409
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"username": "演示本人", "password": "wrong"}).status_code == 401
    restored = client.post("/api/login", json={"username": "演示本人", "password": "test-secret-strong"})
    assert restored.status_code == 200 and restored.json()["id"] == profile_id


def test_student_persist_and_reenter_isolation(client):
    one = signup(client, student()).json()
    q = client.post("/api/questions", json={"request_id": "test-identity-0001", "text": "宿舍维修规定"}).json()
    other = TestClient(app)
    two = signup(other, student(name="演示乙")).json()
    assert one["id"] != two["id"]
    assert other.get("/api/questions/" + q["id"]).status_code == 404
    assert other.get("/api/admin/people").status_code == 401
    assert client.get("/api/me").json()["id"] == one["id"]
    with SessionLocal() as db:
        assert db.get(Registration, one["id"]).name == "演示甲"


def test_parent_children_no_binding_or_extra_access(client):
    signup(client, student())
    q = client.post("/api/questions", json={"request_id": "test-parent-00001", "text": "奖学金条件"}).json()
    other = TestClient(app)
    result = signup(other, parent())
    assert result.status_code == 200
    assert len(result.json()["children"]) == 2
    assert other.get("/api/questions/" + q["id"]).status_code == 404
    assert other.get("/api/admin/questions").status_code == 401
    with SessionLocal() as db:
        assert len(list(db.scalars(select(ChildEntry)))) == 2
        assert "student_id" not in ChildEntry.__table__.columns
        assert len(list(db.scalars(select(Registration)))) == 2


def test_snapshots_not_changed_by_profile_edits(client):
    signup(client, parent())
    q = client.post("/api/questions", json={"request_id": "test-snapshot-001", "text": "转专业办法"}).json()
    changed = parent()
    changed["name"] = "改名后"
    changed["children"][0]["name"] = "改名孩子"
    assert client.put("/api/me", json=changed).status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.post("/api/login", json={"username": "改名后", "password": "test-secret-strong"}).status_code == 200
    with SessionLocal() as db:
        assert db.get(Question, q["id"]).snapshot["name"] == "虚构家长"
        assert db.get(Question, q["id"]).snapshot["children"][0]["name"] == "虚构甲"


def test_admin_separate_and_logout_revokes(client):
    signup(client, student())
    assert client.get("/api/admin/people").status_code == 401
    assert client.post("/api/admin/login", json={"username": "test-admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/admin/login", json={"username": "test-admin", "password": "test-secret-strong"}).status_code == 200
    cookie = client.cookies.get("admin_session")
    assert client.get("/api/admin/people").status_code == 200
    assert client.post("/api/admin/logout").status_code == 200
    client.cookies.set("admin_session", cookie)
    assert client.get("/api/admin/people").status_code == 401


def test_validation_origin_and_no_phone_lookup(client):
    bad = student()
    bad["phone"] = "12345678901"
    response = signup(client, bad)
    assert response.status_code == 422
    assert "12345678901" not in response.text
    assert signup(client, student(), headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.get("/api/people?phone=13800000001").status_code == 404
    bad = parent()
    bad["children"][1]["class_name"] = " "
    assert signup(client, bad).status_code == 422
