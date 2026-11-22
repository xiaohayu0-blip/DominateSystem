import pytest

from app.db import SessionLocal
from app.cli import create_admin
from app.models import UserAccount
from app.security import hash_password
from conftest import signup, student, wait_for_answer


@pytest.mark.parametrize("password", ["12345678", "abcdefgh", "abc1234", "中文密码12345678", "abcdefg１", "        "])
def test_signup_rejects_password_without_required_ascii_letter_digit_or_length(client, password):
    result = client.post("/api/register", json={**student(), "password": password})
    assert result.status_code == 422
    assert any(error["field"] == "password" for error in result.json()["errors"])
    assert password not in result.text


@pytest.mark.parametrize("password", ["abcdefg1", "1234567A", "A1中文密码测试", "valid-password-123"])
def test_signup_accepts_letter_digit_combinations(client, password):
    assert client.post("/api/register", json={**student(), "password": password}).status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"username": student()["name"], "password": password}).status_code == 200


def test_old_password_remains_valid_for_login(client):
    person = signup(client, student()).json()
    with SessionLocal() as db:
        account = db.get(UserAccount, person["id"])
        account.password_hash = hash_password("old-letter-only")
        db.commit()
    client.post("/api/logout")
    assert client.post("/api/login", json={"username": student()["name"], "password": "old-letter-only"}).status_code == 200


@pytest.mark.parametrize("password", ["abcdefghijkl", "123456789012", "abcdefghi1"])
def test_admin_password_preserves_twelve_character_minimum_and_requires_both(password):
    with pytest.raises(ValueError, match="密码至少12位"):
        create_admin("new-admin", password)


def test_worker_reports_real_stages_without_saving_them_in_answer(client, monkeypatch):
    from app.progress import question_progress, report_progress
    from app.models import Question

    person = signup(client, student()).json()
    seen = []
    def answer(db, text, context):
        question_id = db.query(Question).filter_by(registration_id=person['id']).one().id
        assert question_progress(question_id) == 'received'
        for stage in ('searching', 'checking', 'composing'):
            report_progress(stage)
            snapshot = client.get('/api/questions/' + question_id).json()
            assert snapshot['status'] == 'pending'
            seen.append(snapshot['progress'])
        return {'items': [{'question': text, 'status': 'answered', 'content': '回答正文', 'citations': []}]}
    monkeypatch.setattr('app.services.answer_question', answer)
    q = client.post('/api/questions', json={'request_id': 'progress-stage-001', 'text': '问题'}).json()
    result = wait_for_answer(client, q['id'])
    assert seen == ['searching', 'checking', 'composing']
    assert result['status'] == 'complete' and result['progress'] is None
    assert result['answer']['items'][0]['content'] == '回答正文'
    assert question_progress(q['id']) == 'received'  # transient worker state was cleaned


def test_progress_isolation_and_cleanup_after_failure():
    from app.progress import question_progress, report_progress, track_progress

    with pytest.raises(RuntimeError):
        with track_progress('one'):
            report_progress('searching')
            with track_progress('two'):
                report_progress('checking')
                assert question_progress('one') == 'searching'
                assert question_progress('two') == 'checking'
            report_progress('composing')
            assert question_progress('one') == 'composing'
            raise RuntimeError('worker failure')
    assert question_progress('one') == 'received'
    assert question_progress('two') == 'received'
