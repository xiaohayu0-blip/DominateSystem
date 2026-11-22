from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app, question_response
from app.models import Issue, Question, Registration
from conftest import signup, student, wait_for_answer


def test_saved_queue_state_is_visible_to_question_owner():
    with SessionLocal() as db:
        person = Registration(**student())
        db.add(person)
        db.flush()
        question = Question(registration_id=person.id, request_id="saved-queue-001",
                            text="问题", snapshot={}, context={}, model_mode="mock")
        question.processing_stage = "queued"
        db.add(question)
        db.flush()
        assert question_response(db, question)["progress"] == "queued"


def test_questions_wait_in_order_and_duplicate_submission_uses_same_job(monkeypatch):
    entered = Event()
    unblock = Event()
    answered = []

    def answer(db, question, context):
        answered.append(question)
        if question == "第一题":
            entered.set()
            assert unblock.wait(5)
        return {"items": [{"status": "answered", "question": question,
                          "content": question, "citations": []}]}

    monkeypatch.setattr("app.services.answer_question", answer)
    with TestClient(app) as client:
        assert signup(client, student()).status_code == 200
        try:
            first = client.post("/api/questions", json={"request_id": "queue-test-first-01", "text": "第一题"}).json()
            assert entered.wait(2)
            assert first["progress"] == "queued"
            payload = {"request_id": "queue-test-second-01", "text": "第二题"}
            second = client.post("/api/questions", json=payload).json()
            assert second["progress"] == "queued"
            assert client.post("/api/questions", json=payload).json()["id"] == second["id"]
            assert answered == ["第一题"]
        finally:
            unblock.set()
        assert wait_for_answer(client, first["id"])["answer"]["items"][0]["content"] == "第一题"
        assert wait_for_answer(client, second["id"])["answer"]["items"][0]["content"] == "第二题"
    assert answered == ["第一题", "第二题"]


def test_restart_resumes_safe_questions_but_never_repeats_a_started_model_call(monkeypatch, caplog):
    from app.progress import mark_model_request

    def answer(db, question, context):
        mark_model_request()
        with SessionLocal() as other:
            assert other.scalar(select(Question.processing_stage).where(Question.text == question)) == "model_started"
        return {"items": [{"status": "answered", "question": question,
                          "content": "成功", "citations": []}]}

    monkeypatch.setattr("app.services.answer_question", answer)
    with TestClient(app) as client:
        person = signup(client, student()).json()
    saved_ids = {}
    with SessionLocal() as db:
        for stage in ("queued", "running", "model_started"):
            q = Question(registration_id=person["id"], request_id=f"recovery-{stage}-001",
                         text=stage, snapshot={}, context={}, model_mode="mock", processing_stage=stage)
            db.add(q)
            db.flush()
            saved_ids[stage] = q.id
        db.commit()
    with TestClient(app) as restarted:
        assert restarted.post("/api/login", json={"username": student()["name"],
                              "password": "test-secret-strong1"}).status_code == 200
        for stage in ("queued", "running"):
            assert wait_for_answer(restarted, saved_ids[stage])["answer"]["items"][0]["status"] == "answered"
        item = wait_for_answer(restarted, saved_ids["model_started"])["answer"]["items"][0]
        assert item["status"] == "system_error" and "重新提问" in item["content"]
    with SessionLocal() as db:
        assert db.scalar(select(Issue).where(Issue.question_id == saved_ids["model_started"])).reason == "system_error"
    assert any(f"question_id={saved_ids['model_started']} event=interrupted" in record.message
               for record in caplog.records)


def test_model_timeout_records_reason_without_retry_or_question_text(monkeypatch, caplog):
    from app.progress import track_progress
    from app.qa import DeepSeekModel, ModelFailure

    client_class = httpx.Client
    events = []

    def transport(request):
        events.append("request")
        raise httpx.ReadTimeout("private error detail", request=request)

    monkeypatch.setattr("app.qa.httpx.Client",
                        lambda **kwargs: client_class(transport=httpx.MockTransport(transport), **kwargs))
    with track_progress("local-test-id", lambda: events.append("marked")):
        with pytest.raises(ModelFailure):
            DeepSeekModel().request_json("instruction", {"question": "private question"})

    assert events == ["marked", "request"]
    assert "question_id=local-test-id event=model_failure" in caplog.text
    assert "reason=upstream_timeout" in caplog.text
    assert "private" not in caplog.text
