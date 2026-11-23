from app.db import SessionLocal
from app.models import Question
from conftest import signup, student
from test_knowledge import ask


def test_feedback_stays_with_the_reply_and_is_visible_to_admin(client):
    assert signup(client, student()).status_code < 300
    saved = ask(client, "测试问题")
    with SessionLocal() as db:
        question = db.get(Question, saved["id"])
        question.answer = {"items": [
            {"question": f"子问题{i}", "content": f"回复{i}", "status": "answered", "citations": []}
            for i in range(2)
        ]}
        db.commit()
    question_path = f"/api/questions/{saved['id']}"
    path = question_path + "/feedback/1"
    assert client.patch(path, json={"rating": "like"}).status_code == 200
    assert client.patch(path, json={"comment": "  缺少适用年份  "}).json()["rating"] == "like"
    feedback = client.patch(path, json={"rating": "dislike"}).json()
    assert feedback["comment"] == "缺少适用年份"
    assert client.patch(path, json={"comment": "  "}).status_code == 422
    assert client.patch(question_path + "/feedback/2", json={"rating": "like"}).status_code == 404
    assert client.get(question_path).json()["feedback"] == [feedback]
    assert client.get("/api/admin/questions").status_code in (401, 403)
    assert signup(client, student("演示乙", "13800000002")).status_code < 300
    assert client.patch(path, json={"rating": "like"}).status_code == 404
    assert client.post("/api/admin/login", json={"username": "test-admin", "password": "test-secret-strong"}).status_code == 200
    records = client.get("/api/admin/questions?feedback_only=true").json()
    assert len(records) == 1
    assert records[0]["feedback"] == [feedback]
    assert records[0]["answer"]["items"][feedback["item_index"]]["content"] == "回复1"
