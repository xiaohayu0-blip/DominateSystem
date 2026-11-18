from sqlalchemy import select

from app.db import SessionLocal
from app.models import Admin
from app.security import hash_password
from app.retrieval import retriever
from test_knowledge import publish
from conftest import signup, student


ROOT = '/api/admin/vocabulary'


def create(c, source='国励奖', target='国家励志奖学金', kind='简称'):
    r = c.post(ROOT, json={'source': source, 'target': target, 'kind': kind})
    assert r.status_code == 200, r.text
    return r.json()


def act(c, item, action, **extra):
    return c.post(f'{ROOT}/{item["id"]}/actions', json={
        'revision': item['revision'], 'action': action, **extra})


def live(c, item):
    r = act(c, item, 'submit')
    assert r.status_code == 200, r.text
    r = act(c, r.json(), 'publish')
    assert r.status_code == 200, r.text
    return r.json()


def test_lifecycle_preview_and_cache(admin_client):
    c = admin_client
    doc = publish('国家励志奖学金办法', '国家励志奖学金需提交申请表，2026年不少于80分。')
    item = create(c, source='励学金')
    question = '2026年2024级励学金不少于80分和2万元？'
    def preview(**extra):
        r = c.post(ROOT + '/preview', json={'question': question, **extra})
        assert r.status_code == 200, r.text
        return r.json()
    assert not preview()['applied']
    draft = preview(draft_id=item['id'], draft_revision=item['revision'])
    assert draft['original'] == question
    assert draft['applied'][0]['target'] == '国家励志奖学金'
    assert doc in [x['document_id'] for x in draft['hits']]
    item = live(c, item)
    assert preview()['applied']
    r = c.put(f'{ROOT}/{item["id"]}', json={**item['draft'], 'target': '励志助学项目', 'revision': item['revision']})
    assert r.status_code == 200, r.text
    edited = r.json()
    assert preview()['applied'][0]['target'] == '国家励志奖学金'
    item = live(c, edited)
    assert preview()['applied'][0]['target'] == '励志助学项目'
    item = act(c, item, 'rollback', version=1).json()
    assert preview()['applied'][0]['target'] == '国家励志奖学金'
    item = act(c, item, 'disable').json()
    assert not preview()['applied']
    history = c.get(f'{ROOT}/{item["id"]}/history').json()
    assert {x['action'] for x in history} >= {'create', 'edit', 'publish', 'disable', 'rollback'}
    assert all('before' in x and 'after' in x and x['actor'] for x in history)


def test_conflicts_cycles_protected_tokens_and_concurrency(admin_client):
    c = admin_client
    a = live(c, create(c, '学业评估', '学业考评', '同义表达'))
    assert c.post(ROOT, json={'source': '学业评估', 'target': '其他项目', 'kind': '简称'}).status_code == 409
    b = create(c, '学业考评', '学业评估', '同义表达')
    assert act(c, act(c, b, 'submit').json(), 'publish').status_code == 409
    edit = {**a['draft'], 'revision': a['revision'], 'target': '学业测定'}
    assert c.put(f'{ROOT}/{a["id"]}', json=edit).status_code == 200
    assert c.put(f'{ROOT}/{a["id"]}', json=edit).status_code == 409
    for source, target in [('奖', '奖学金'), ('不少于80分', '低于60分'), ('国奖', '国家励志奖学金'), ('综.*测', '综合测评')]:
        assert c.post(ROOT, json={'source': source, 'target': target, 'kind': '简称'}).status_code in (409, 422)


def test_literal_expansion_awards_boundaries_and_bge(admin_client, monkeypatch):
    from app import vocabulary, retrieval
    from app.config import settings
    c = admin_client
    for source, target, kind in [('国励奖', '国家励志奖学金', '简称'), ('请架', '请假', '错别字'), ('国奖', '国家奖学金', '简称')]:
        live(c, create(c, source, target, kind))
    with SessionLocal() as db:
        rules = vocabulary.published_rules(db)
        for text in ['2026年2024级请架不超过2天且不少于80分', '国奖比国励奖多2000元吗']:
            result = vocabulary.expand(text, rules)
            assert result['original'] == text
            assert result['queries'][0] == text
            assert len(result['queries']) <= 9
            for token in ['2026', '2024', '不超过2天', '不少于80分', '比', '多2000元']:
                if token in text:
                    assert all(token in q for q in result['queries'])
        assert not vocabulary.expand('外国奖项', rules)['applied']
    national = publish('国家奖学金', '国家奖学金申请材料为成绩单。')
    incentive = publish('国家励志奖学金', '国家励志奖学金申请材料为认定表。')
    with SessionLocal() as db:
        rows, _ = retriever.search(db, '国励奖申请材料', {'business_year': 2026})
        assert incentive in [d.id for _, d in rows]
        assert national not in [d.id for _, d in rows]
    captured = []
    monkeypatch.setattr(settings(), 'embedding_mode', 'bge')
    monkeypatch.setattr(retrieval, 'embed_query', lambda text: captured.append(text) or [1.] * 512)
    # No documents: exercise the real query adapter without downloading a BGE model.
    with SessionLocal() as db:
        for d in db.scalars(select(retrieval.Document)):
            d.status = 'draft'
        db.commit()
        retriever.search(db, '国励奖申请材料', {'business_year': 2026})
    assert '国励奖' in captured[0] and '国家励志奖学金' in captured[0]


def test_admin_preview_privacy_and_student_denial(admin_client):
    c = admin_client
    publish('寝室长名单', '寝室长张三 手机13800000001 工商管理班')
    r = c.post(ROOT + '/preview', json={'question': '寝室长'}).json()
    assert not r['hits']
    assert '13800000001' not in str(r)
    c.post('/api/admin/logout')
    signup(c, student())
    for path in [ROOT, '/api/admin/people', '/api/admin/questions', '/api/admin/documents', '/api/admin/events']:
        assert c.get(path).status_code == 401


def test_review_return_and_separate_actor(admin_client):
    c = admin_client
    item = create(c, '学业考评', '综合测评', '同义表达')
    assert act(c, item, 'publish').status_code == 409
    item = act(c, item, 'submit').json()
    item = act(c, item, 'return', note='请核对名称').json()
    assert item['state'] == 'returned'
    item = act(c, item, 'submit').json()
    with SessionLocal() as db:
        db.add(Admin(username='reviewer', password_hash=hash_password('review-pass1')))
        db.commit()
    c.post('/api/admin/login', json={'username': 'reviewer', 'password': 'review-pass1'})
    item = act(c, item, 'publish').json()
    assert item['published']['reviewer'] == 'reviewer'
    assert item['draft']['submitter'] == 'test-admin'


def test_overlap_ambiguity_and_canonical_idempotence():
    from app.vocabulary import expand
    rules = [{'source': '国家励志', 'target': '国家励志奖学金'}]
    assert not expand('国家励志奖学金申请', rules)['applied']
    rules = [{'source': '学业评估', 'target': '综合测评'}, {'source': '评估申请', 'target': '项目申报'}]
    assert expand('学业评估申请', rules)['ambiguous']
    for question in ['外国奖项', '全国奖项', '美国奖学金']:
        assert not expand(question, [{'source': '国奖', 'target': '国家奖学金'}])['applied']


def test_concurrent_publication_and_edits_use_database_lock(admin_client):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from app.main import app
    c = admin_client
    entry = create(c, '学业评估', '综合测评', '同义表达')
    cookie = dict(c.cookies)
    def edit(target):
        with TestClient(app) as other:
            other.cookies.update(cookie)
            return other.put(f'{ROOT}/{entry["id"]}', json={
                **entry['draft'], 'target': target, 'revision': entry['revision']}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ['学业测评', '综合考评'])) == [200, 409]


def test_cache_reads_epoch_not_whole_table_on_each_query(admin_client):
    from sqlalchemy import event
    from app.vocabulary import published_rules
    live(admin_client, create(admin_client))
    statements = []
    with SessionLocal() as db:
        def observe(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(db.bind, 'before_cursor_execute', observe)
        try:
            published_rules(db)
            published_rules(db)
        finally:
            event.remove(db.bind, 'before_cursor_execute', observe)
    assert sum('FROM vocabulary_entries' in s for s in statements) <= 1


def test_ordinary_student_has_no_editor_access(client):
    signup(client, student())
    assert client.get(ROOT).status_code == 401
    assert client.post(ROOT + '/preview', json={'question': '国奖'}).status_code == 401
