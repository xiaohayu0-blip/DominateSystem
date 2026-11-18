"""Literal, single-pass retrieval expansion. Never modifies sources or answers."""
import re
import threading
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import get_db, now
from .security import admin
from .vocabulary_models import VocabularyEntry as Entry, VocabularyEpoch as Epoch

router = APIRouter(prefix='/api/admin/vocabulary')
KINDS = {'简称', '同义表达', '错别字'}
_cache = {}
_cache_lock = threading.Lock()
# Terms that encode conditions or quantities are not editable vocabulary.
PROTECTED = re.compile(r'[0-9零〇一二三四五六七八九十百千万亿两]|不|无|非|未|除|否|超过|至少|至多|低于|高于|以上|以下|小于|大于|年级|资格|仅限|必须|不得|可以')


def actor(obj=Depends(admin)):
    return {'id': 'admin:' + obj.id, 'name': obj.username, 'review': True}


def payload(entry):
    return {k: deepcopy(getattr(entry, k)) for k in ('id', 'revision', 'state', 'draft', 'published', 'versions')}


class Content(BaseModel):
    source: str = Field(min_length=2, max_length=24)
    target: str = Field(min_length=2, max_length=48)
    kind: str


class Edit(Content):
    revision: int = Field(ge=1)


class Action(BaseModel):
    action: str
    revision: int = Field(ge=1)
    note: str = Field(default='', max_length=500)
    version: int | None = None


def validate(data):
    data = {k: v.strip() for k, v in data.items() if k in ('source', 'target', 'kind')}
    if data['kind'] not in KINDS or data['source'] == data['target']:
        raise HTTPException(422, '请选择词条类型，且日常说法不能与正式名称相同')
    for value in (data['source'], data['target']):
        if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z]{2,48}', value) or PROTECTED.search(value):
            raise HTTPException(422, '仅支持不含条件、数字或正则的普通名称；短于两字的词语不支持')
    # These are different award schemes, including their established aliases.
    from .retrieval import SYNONYMS
    reserved = {**SYNONYMS, '国奖': '国家奖学金', '国励奖': '国家励志奖学金',
                '请架': '请假', '寝事长': '寝室长'}
    if data['source'] in reserved and reserved[data['source']] != data['target']:
        raise HTTPException(409, '与现有兼容词条冲突，请使用正确事项名称')
    from .retrieval import ORAL_TOPICS
    for pattern, target in ORAL_TOPICS.items():
        if re.fullmatch(pattern, data['source']) and target != data['target']:
            raise HTTPException(409, '与现有兼容规则冲突，请先核对原规则')
    return data


def write_lock(db):
    # A database row serializes publication validation across processes, including SQLite.
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if db.bind.dialect.name == 'sqlite' else pg_insert
    db.execute(insert(Epoch).values(id=1, revision=0).on_conflict_do_nothing(index_elements=['id']))
    db.execute(update(Epoch).where(Epoch.id == 1).values(revision=Epoch.revision + 1))


def check_conflicts(db, data, exclude=None, published=False):
    entries = list(db.scalars(select(Entry).where(Entry.id != exclude))) if exclude else list(db.scalars(select(Entry)))
    for entry in entries:
        for existing in (entry.draft, entry.published):
            if existing and existing['source'] == data['source']:
                raise HTTPException(409, '同一范围已有此说法，请编辑原词条；不能自动覆盖或发布多个解释')
    if not published:
        return
    rules = [e.published for e in entries if e.published] + [data]
    from .retrieval import SYNONYMS
    graph = {**SYNONYMS, **{x['source']: x['target'] for x in rules}}
    for start in graph:
        seen, cursor = set(), start
        while cursor in graph:
            if cursor in seen:
                raise HTTPException(409, '存在循环映射，请先调整相关词条')
            seen.add(cursor)
            cursor = graph[cursor]
    # Contained targets would otherwise permit indirect, order-dependent expansions.
    for rule in rules:
        for other in rules:
            if rule is not other and other['source'] in rule['target']:
                raise HTTPException(409, '正式名称包含其他词条的日常说法，请统一为最终正式名称后发布')


def record(entry, action, who, before, note=''):
    entry.history = [*entry.history, {'action': action, 'actor': who['name'], 'actor_id': who['id'],
        'at': now().isoformat(), 'note': note, 'before': before, 'after': payload(entry)}]


def locked_entry(db, entry_id, revision):
    write_lock(db)
    entry = db.get(Entry, entry_id, populate_existing=True)
    if not entry:
        raise HTTPException(404, '词条不存在')
    if entry.revision != revision:
        raise HTTPException(409, '词条已被他人修改，请刷新并核对后再保存；当前输入已保留')
    return entry


@router.get('/access')
def access(who=Depends(actor)):
    return who


@router.get('')
def listing(keyword: str = '', kind: str = '', state: str = '', who=Depends(actor), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Entry).order_by(Entry.id)))
    return [payload(e) for e in rows if (not kind or e.draft['kind'] == kind)
            and (not state or e.state == state)
            and (not keyword or keyword.casefold() in (e.draft['source'] + e.draft['target']).casefold())]


@router.get('/legacy')
def legacy(who=Depends(actor)):
    from .retrieval import SYNONYMS, ORAL_TOPICS
    return {'literal': SYNONYMS, 'patterns': ORAL_TOPICS,
            'notice': '现有兼容规则只读保留。奖助、励志等过宽表达及模式规则需逐条核查，不批量转为已发布词条。'}


@router.post('')
def create(data: Content, who=Depends(actor), db: Session = Depends(get_db)):
    content = validate(data.model_dump())
    write_lock(db)
    check_conflicts(db, content)
    entry = Entry(draft={**content, 'submitter': who['name'], 'submitter_id': who['id']}, published=None,
                  state='draft', revision=1, versions=[], history=[])
    db.add(entry)
    db.flush()
    record(entry, 'create', who, None)
    db.commit()
    return payload(entry)


@router.put('/{entry_id}')
def edit(entry_id: str, data: Edit, who=Depends(actor), db: Session = Depends(get_db)):
    content = validate(data.model_dump(exclude={'revision'}))
    entry = locked_entry(db, entry_id, data.revision)
    check_conflicts(db, content, entry_id)
    before = payload(entry)
    entry.draft = {**content, 'submitter': who['name'], 'submitter_id': who['id']}
    entry.state, entry.revision = 'draft', entry.revision + 1
    record(entry, 'edit', who, before)
    db.commit()
    return payload(entry)


@router.post('/{entry_id}/actions')
def action(entry_id: str, data: Action, who=Depends(actor), db: Session = Depends(get_db)):
    if data.action in ('publish', 'disable', 'rollback', 'return') and not who['review']:
        raise HTTPException(403, '审核、发布、停用与回滚仅限管理员')
    entry = locked_entry(db, entry_id, data.revision)
    before = payload(entry)
    if data.action == 'submit':
        if entry.state not in ('draft', 'returned', 'disabled'):
            raise HTTPException(409, '请先保存需要审核的草稿')
        entry.state = 'submitted'
    elif data.action == 'return':
        if entry.state != 'submitted' or not data.note.strip():
            raise HTTPException(409, '仅可退回待审核草稿，请填写修改意见')
        entry.state = 'returned'
    elif data.action in ('publish', 'rollback'):
        if data.action == 'publish' and entry.state != 'submitted':
            raise HTTPException(409, '请先提交审核，再发布')
        content = entry.draft
        if data.action == 'rollback':
            content = next((v for v in entry.versions if v['version'] == data.version), None)
            if not content:
                raise HTTPException(404, '未找到可回滚的已发布版本')
            # A rollback is an explicit administrator review of a previously published snapshot.
        content = {**validate({k: content[k] for k in ('source', 'target', 'kind')}),
                   'submitter': content['submitter'], 'submitter_id': content['submitter_id']}
        check_conflicts(db, content, entry_id, published=True)
        version = {**content, 'reviewer': who['name'], 'reviewer_id': who['id'],
                   'version': len(entry.versions) + 1, 'at': now().isoformat()}
        entry.published = version
        entry.versions = [*entry.versions, version]
        entry.state = 'published'
        entry.draft = content
    elif data.action == 'disable':
        if not entry.published:
            raise HTTPException(409, '词条当前未生效')
        entry.published, entry.state = None, 'disabled'
    else:
        raise HTTPException(422, '不支持的操作')
    entry.revision += 1
    record(entry, data.action, who, before, data.note)
    db.commit()
    return payload(entry)


@router.get('/{entry_id}/history')
def history(entry_id: str, who=Depends(actor), db: Session = Depends(get_db)):
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(404, '词条不存在')
    return entry.history


def published_rules(db):
    epoch = db.scalar(select(Epoch.revision).where(Epoch.id == 1)) or 0
    key = db.get_bind()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == epoch:
            return deepcopy(cached[1])
        rules = [{**e.published, 'id': e.id} for e in db.scalars(select(Entry).where(Entry.published.is_not(None)))
                 if e.published]
        _cache[key] = (epoch, rules)
        return deepcopy(rules)


def occurrences(text, source):
    for match in re.finditer(re.escape(source), text):
        start, end = match.span()
        # Avoid matching award abbreviations inside unrelated words and Latin fragments.
        if source in ('国奖', '国励奖'):
            if (start and text[start - 1] in '外美英法德韩泰全中') or text[end:end + 1] == '项':
                continue
        if source.isascii() and ((start and text[start - 1].isascii() and text[start - 1].isalnum())
                                  or (end < len(text) and text[end].isascii() and text[end].isalnum())):
            continue
        yield start, end


def expand(text, rules):
    matches = []
    protected = [(m.start(), m.end()) for rule in rules
                 for m in re.finditer(re.escape(rule['target']), text)]
    for rule in rules:
        for start, end in occurrences(text, rule['source']):
            if any(left <= start and end <= right for left, right in protected):
                continue
            matches.append((start, end, rule))
    matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
    chosen, end = [], -1
    ambiguous = False
    for start, stop, rule in matches:
        if start < end:
            if chosen and stop > chosen[-1][1] and rule['target'] != chosen[-1][2]['target']:
                ambiguous = True
            if chosen and start == chosen[-1][0] and stop == chosen[-1][1] and rule['target'] != chosen[-1][2]['target']:
                ambiguous = True
            continue
        if len(chosen) == 8:
            break
        chosen.append((start, stop, rule))
        end = stop
    variant, cursor, applied = [], 0, []
    for start, end, rule in chosen:
        variant.extend([text[cursor:start], rule['target']])
        cursor = end
        if rule not in applied:
            applied.append(rule)
    variant.append(text[cursor:])
    expanded = ''.join(variant)
    return {'original': text, 'queries': list(dict.fromkeys([text, expanded])),
            'applied': applied, 'ambiguous': ambiguous}


class Preview(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    draft_id: str | None = None
    draft_revision: int | None = None
    business_year: int = Field(default=2026, ge=2000, le=2100)
    admission_year: int | None = Field(default=None, ge=2000, le=2100)


@router.post('/preview')
def preview(data: Preview, who=Depends(actor), db: Session = Depends(get_db)):
    from .retrieval import retriever
    rules = published_rules(db)
    if data.draft_id:
        entry = db.get(Entry, data.draft_id)
        if not entry or entry.revision != data.draft_revision:
            raise HTTPException(409, '草稿已变化，请刷新后试搜')
        rules = [r for r in rules if r['id'] != entry.id] + [{**entry.draft, 'id': entry.id}]
    expansion = expand(data.question, rules)
    rows, status = retriever.search(db, data.question, {
        'business_year': data.business_year, 'admission_year': data.admission_year, '_vocabulary_rules': rules})
    hits = []
    for chunk, doc in rows:
        # Preview is intentionally narrower than authorized student Q&A: no roster or personal attachments.
        if (doc.original_name.lower().endswith(('.xlsx', '.csv'))
                or re.search(r'名单|通讯录|人员|班主任|辅导员|值班|花名册|登记表', doc.title + chunk.text)
                or re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)|身份证|手机号|联系方式|学号|姓名', chunk.text)):
            continue
        hits.append({'document_id': doc.id, 'title': doc.title, 'location': chunk.location,
                     'excerpt': chunk.text, 'business_year': doc.business_year, 'admission_year': doc.admission_year})
    return {**expansion, 'hits': hits, 'status': status, 'draft_preview': bool(data.draft_id),
            'notice': '仅检索已发布资料，不调用大模型；人员资料及表格不在试搜中展示。'}
