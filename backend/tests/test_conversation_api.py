from sqlalchemy import select

from app import conversation as chat
from app.config import settings
from app.db import SessionLocal
from app.models import Issue
from app.qa import DeepSeekModel
from conftest import signup, student
from test_knowledge import ask


def test_chat_does_not_create_missing_material_issue(client, monkeypatch):
    signup(client, student())
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', lambda *a: [{'question': '你好', 'kind': 'general'}])
    monkeypatch.setattr(DeepSeekModel, 'request_json', lambda *a: {'answer': '你好呀 😊'})
    result = ask(client, '你好')
    assert result['answer']['items'][0]['status'] == 'general_answer'
    with SessionLocal() as db:
        assert not list(db.scalars(select(Issue)))


def test_search_failure_records_gap_in_existing_summary(client, monkeypatch):
    signup(client, student())
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', lambda *a: [{'question': '2028年星际交换生截止日期', 'kind': 'campus'}])
    result = ask(client, '2028年星际交换生截止日期')
    assert result['answer']['items'][0]['status'] == 'missing_material'
    with SessionLocal() as db:
        issues = list(db.scalars(select(Issue)))
        assert len(issues) == 1 and issues[0].question_id == result['id']


def test_general_model_failure_is_a_technical_issue(client, monkeypatch):
    from app.qa import ModelFailure
    signup(client, student())
    monkeypatch.setattr(settings(), 'model_mode', 'deepseek')
    monkeypatch.setattr(chat, 'plan_turn', lambda *a: [{'question': '你好', 'kind': 'general'}])
    def unavailable(*a):
        raise ModelFailure('unavailable')
    monkeypatch.setattr(DeepSeekModel, 'request_json', unavailable)
    result = ask(client, '你好')
    assert result['answer']['items'][0]['status'] == 'system_error'
