import json

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Question
from conftest import signup, student


def frames(text):
    result = []
    for frame in text.split('\n\n'):
        if frame.startswith('event: '):
            kind, data = frame.split('\ndata: ', 1)
            result.append((kind[7:], json.loads(data)))
    return result


def test_stream_sends_single_characters_then_exact_saved_answer_without_regenerating(client, monkeypatch):
    signup(client, student())
    q = client.post('/api/questions', json={'request_id': 'stream-replay-001', 'text': '寝室长加分'}).json()
    with SessionLocal() as db:
        saved = db.get(Question, q['id'])
        saved.answer = {'items': [
            {'question': '加分', 'status': 'answered', 'content': '能加 (｡•̀ᴗ-)✧\n条件要保留。', 'citations': []},
            {'question': '资料', 'status': 'missing_material', 'content': '暂无资料。', 'citations': []},
        ], 'notice': '核对原文'}
        db.commit()
    expected = client.get('/api/questions/' + q['id']).json()

    def unexpected_generation(*args, **kwargs):
        raise AssertionError('Reading the stream must not call the model again')

    monkeypatch.setattr('app.services.answer_question', unexpected_generation)
    for _ in range(2):
        response = client.get(f'/api/questions/{q["id"]}/stream')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        assert response.headers['x-accel-buffering'] == 'no'
        events = frames(response.text)
        content = ['', '']
        for kind, value in events:
            if kind == 'delta':
                assert len(value['text']) == 1
                content[value['index']] += value['text']
        assert content == [item['content'] for item in expected['answer']['items']]
        assert events[-1] == ('complete', expected)


def test_stream_only_allows_question_owner(client):
    signup(client, student())
    q = client.post('/api/questions', json={'request_id': 'stream-owner-001', 'text': '请假'}).json()
    other = TestClient(app)
    assert other.get(f'/api/questions/{q["id"]}/stream').status_code == 401
    signup(other, student(name='其他同学'))
    assert other.get(f'/api/questions/{q["id"]}/stream').status_code == 404
