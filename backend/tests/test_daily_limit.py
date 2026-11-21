from app.config import settings
from app.conversation import answer_turn
from app.db import SessionLocal


def test_daily_limit_admin_override_and_question_response(admin_client, monkeypatch):
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(settings(), "model_daily_limit", 500)
    path = "/api/admin/settings/daily-model-limit"
    assert admin_client.get("/api/admin/settings").json()["daily_model_limit"] == 500
    assert admin_client.put(path, json={"daily_model_limit": -1}).status_code == 422
    assert admin_client.put(path, json={"daily_model_limit": 0}).json() == {"daily_model_limit": 0}
    assert admin_client.get("/api/admin/settings").json()["daily_model_limit"] == 0

    with SessionLocal() as db:
        for question in ("你好", "2027年寒假起止日期是什么？", "帮我写一首诗"):
            item, = answer_turn(db, question, {})["items"]
            assert item["status"] == "daily_limit"
            assert item["content"] == "今日应用提问次数已达上限"

    assert admin_client.put(path, json={"daily_model_limit": 500}).status_code == 200
    with SessionLocal() as db:
        item, = answer_turn(db, "你好", {})["items"]
        assert item["status"] == "general_answer"

    admin_client.post("/api/admin/logout")
    assert admin_client.put(path, json={"daily_model_limit": 0}).status_code == 401
