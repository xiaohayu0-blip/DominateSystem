import logging
import hashlib
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from . import models as m
from .alerts import HELP_TEXT, create_alert, detect_danger, retention_preview
from .config import settings
from .db import SessionLocal, get_db, now
from .qa import answer_question, context_for
from .embeddings import BGE_MODEL, bge_model
from .retrieval import build_chunks, index_bge
from .schemas import (DocumentMeta, EventUpdate, FormalReply, IssueUpdate, LoginInput, MergeInput,
                      QuestionInput, RegisterInput, SignupInput)
from .security import (admin, audit, hash_password, issue_session, token_hash,
                       user, verify_password)
from .services import ensure_evidence, finish_question, issue_data, profile, weekly
from .storage import digest, parse_file, store, validate_file

logger = logging.getLogger("dominate")
limits: dict[str, deque] = defaultdict(deque)
limit_lock = threading.Lock()


def rate_limit(key: str, count: int, seconds=60):
    with limit_lock:
        stamp = time.monotonic()
        hits = limits[key]
        while hits and hits[0] < stamp - seconds:
            hits.popleft()
        if len(hits) >= count:
            raise HTTPException(429, "操作频繁，请稍后重试")
        hits.append(stamp)
        if len(limits) > 10000:
            for old in list(limits):
                if not limits[old] or limits[old][-1] < stamp - 3600:
                    del limits[old]


@asynccontextmanager
async def lifespan(app):
    settings().validate_runtime()
    # A crashed generation is visible as a technical failure, never erased or called missing material.
    with SessionLocal() as db:
        if settings().embedding_mode == "bge":
            if db.scalar(select(m.Chunk.id).join(m.Document).where(
                m.Document.status == "published", m.Document.parse_status == "ready",
                m.Chunk.bge_vector.is_(None),
            ).limit(1)):
                raise RuntimeError("请先运行 python -m app.cli bge-index，再启用 BGE 检索。")
            bge_model()
        stale = list(db.scalars(select(m.Question).where(m.Question.status == "pending")))
        for q in stale:
            q.status = "complete"
            q.answer = {"items": [{"question": q.text, "status": "system_error",
                                    "content": "上次回答处理被中断，已记录技术故障，请重新提问。", "citations": []}]}
            db.add(m.Issue(question_id=q.id, text=q.text, reason="system_error"))
        db.commit()
    yield


app = FastAPI(title="管理学院制度问答与问题处理系统", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def protection(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        config = settings()
        allowed_origins = {config.public_origin}
        if config.environment == "demo" and config.demo_lan_origin:
            allowed_origins.add(config.demo_lan_origin)
        if origin and origin not in allowed_origins:
            return JSONResponse({"detail": "请求来源不允许"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "跨站请求不允许"}, status_code=403)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > (settings().max_upload_mb + 1) * 1024**2:
            return JSONResponse({"detail": "请求体超过限制"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic errors include rejected input by default; omit it to avoid reflecting PII/passwords.
    return JSONResponse({"detail": "字段校验失败", "errors": [
        {"field": ".".join(map(str, x["loc"][1:])), "message": x["msg"]} for x in exc.errors()]}, status_code=422)


@app.exception_handler(Exception)
async def safe_error(request, exc):
    logger.error("request_failed type=%s", type(exc).__name__)
    return JSONResponse({"detail": "服务暂时不可用，未确认成功的操作请重试"}, status_code=500)


def required(db, cls, object_id):
    obj = db.get(cls, object_id)
    if not obj:
        raise HTTPException(404, "记录不存在或无权访问")
    return obj


@app.get("/api/health")
def health(db: DBSession = Depends(get_db)):
    db.execute(text("SELECT 1"))
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return {"status": "ok", "schema_ready": bool(version)}


@app.get("/api/modes")
def modes():
    cfg = settings()
    return {"environment": cfg.environment, "model": cfg.model_mode, "material": cfg.material_mode,
            "mail": cfg.mail_mode, "identity_verified": False,
            "embedding": BGE_MODEL if cfg.embedding_mode == "bge" else "character-hash"}


@app.post("/api/register")
def register(data: SignupInput, request: Request, response: Response, db: DBSession = Depends(get_db)):
    rate_limit("register:" + (request.client.host if request.client else "unknown"), 20)
    old_token = request.cookies.get("user_session", "")
    if old_token:
        db.execute(delete(m.Session).where(m.Session.token_hash == token_hash(old_token), m.Session.admin_id.is_(None)))
    username = data.name.casefold()
    if len(username) > 80:
        raise HTTPException(422, "姓名过长，无法作为登录账号")
    person = m.Registration(**data.model_dump(exclude={"children", "password"}))
    db.add(person)
    db.flush()
    db.add(m.UserAccount(registration_id=person.id, username=username,
                         password_hash=hash_password(data.password)))
    for child in data.children:
        db.add(m.ChildEntry(parent_id=person.id, **child.model_dump()))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "该姓名已注册。如为本人账号，请直接登录；如为同名，请联系管理员。") from exc
    issue_session(db, response, registration_id=person.id)
    db.commit()
    return profile(db, person)


@app.post("/api/login")
def user_login(data: LoginInput, request: Request, response: Response, db: DBSession = Depends(get_db)):
    host = request.client.host if request.client else "unknown"
    username = data.username.casefold()
    rate_limit("user-login:" + host, 30, 300)
    rate_limit(f"user-login:{host}:{username}", 8, 300)
    account = db.scalar(select(m.UserAccount).where(m.UserAccount.username == username))
    fallback = "scrypt$00000000000000000000000000000000$" + "0" * 128
    valid = verify_password(data.password, account.password_hash if account else fallback)
    if not account or not valid:
        raise HTTPException(401, "登录失败，请检查账号和密码")
    old = request.cookies.get("user_session", "")
    if old:
        db.execute(delete(m.Session).where(m.Session.token_hash == token_hash(old), m.Session.admin_id.is_(None)))
    issue_session(db, response, registration_id=account.registration_id)
    db.commit()
    return profile(db, db.get(m.Registration, account.registration_id))


@app.get("/api/me")
def me(person=Depends(user), db: DBSession = Depends(get_db)):
    return profile(db, person)


@app.put("/api/me")
def edit_me(data: RegisterInput, person=Depends(user), db: DBSession = Depends(get_db)):
    if data.role != person.role:
        raise HTTPException(422, "修改资料不能切换提问身份，请退出后重新登记")
    account = db.get(m.UserAccount, person.id)
    if account.username == person.name.casefold() and data.name != person.name:
        if len(data.name.casefold()) > 80:
            raise HTTPException(422, "姓名过长，无法作为登录账号")
        account.username = data.name.casefold()
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "该姓名已被用于登录，请联系管理员。") from exc
    for key, value in data.model_dump(exclude={"children"}).items():
        setattr(person, key, value)
    db.execute(delete(m.ChildEntry).where(m.ChildEntry.parent_id == person.id))
    for child in data.children:
        db.add(m.ChildEntry(parent_id=person.id, **child.model_dump()))
    db.commit()
    return profile(db, person)


def logout_session(request, response, db, is_admin):
    key = "admin_session" if is_admin else "user_session"
    token = request.cookies.get(key, "")
    db.execute(delete(m.Session).where(m.Session.token_hash == token_hash(token)))
    response.delete_cookie(key, path="/", secure=settings().secure_cookies, httponly=True, samesite="strict")
    db.commit()
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response, db: DBSession = Depends(get_db)):
    return logout_session(request, response, db, False)


@app.post("/api/questions", status_code=202)
def submit(data: QuestionInput, tasks: BackgroundTasks, person=Depends(user), db: DBSession = Depends(get_db)):
    request_hash = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    existing = db.scalar(select(m.Question).where(m.Question.registration_id == person.id,
                                                  m.Question.request_id == data.request_id))
    if existing:
        if existing.text != data.text or existing.context.get("request_hash", request_hash) != request_hash:
            raise HTTPException(409, "请求编号已用于另一个问题")
        return question_response(db, existing)
    previous = required(db, m.Question, data.previous_id) if data.previous_id else None
    if previous and previous.registration_id != person.id:
        raise HTTPException(404, "记录不存在或无权访问")
    danger = detect_danger(data.text, previous.text if previous else "")
    if not danger:
        rate_limit("question:" + person.id, 12)
    context = context_for(data, previous)
    context["request_hash"] = request_hash
    if previous:
        context["previous_id"] = previous.id
    q = m.Question(registration_id=person.id, request_id=data.request_id, text=data.text,
                   snapshot=profile(db, person), context=context, model_mode=settings().model_mode)
    db.add(q)
    try:
        db.flush()
        if danger:
            create_alert(db, q)
            q.answer = {"items": []}
            q.status, q.completed_at = "complete", now()
        db.commit()
    except IntegrityError:
        db.rollback()
        q = db.scalar(select(m.Question).where(m.Question.registration_id == person.id,
                                               m.Question.request_id == data.request_id))
        if not q:
            raise HTTPException(503, "未能保存问题，请重试")
    if q.status == "pending":
        tasks.add_task(finish_question, q.id)
    return question_response(db, q)


def question_response(db, q):
    event = db.scalar(select(m.EmergencyEvent).where(m.EmergencyEvent.question_id == q.id))
    alert = None
    if event:
        job = db.scalar(select(m.MailJob).where(m.MailJob.event_id == event.id))
        alert = {"message": HELP_TEXT, "mail_mode": job.mode, "status": job.status,
                 "notice": "演示预警已生成，不会真实发送邮件。" if job.mode == "mock" else
                 "预警任务已记录；系统无法确认收件人是否阅读，请不要等待邮件通知。"}
    return {"id": q.id, "text": q.text, "status": q.status, "answer": q.answer,
            "created_at": q.created_at, "alert": alert}


@app.get("/api/questions/{question_id}")
def get_question(question_id: str, person=Depends(user), db: DBSession = Depends(get_db)):
    q = required(db, m.Question, question_id)
    if q.registration_id != person.id:
        raise HTTPException(404, "记录不存在或无权访问")
    return question_response(db, q)


@app.get("/api/sources/{chunk_id}")
def source(chunk_id: str, person=Depends(user), db: DBSession = Depends(get_db)):
    for q in db.scalars(select(m.Question).where(m.Question.registration_id == person.id)):
        for item in q.answer.get("items", []):
            for saved in item["citations"]:
                if saved["chunk_id"] == chunk_id:
                    doc = db.get(m.Document, saved["document_id"])
                    return {**saved, "source_status": doc.status if doc else "unavailable"}
    raise HTTPException(404, "来源未在当前会话引用")


@app.post("/api/admin/login")
def login(data: LoginInput, request: Request, response: Response, db: DBSession = Depends(get_db)):
    rate_limit("login:" + (request.client.host if request.client else "unknown"), 8, 300)
    actor = db.scalar(select(m.Admin).where(m.Admin.username == data.username, m.Admin.active.is_(True)))
    fallback = "scrypt$00000000000000000000000000000000$" + "0" * 128
    valid = verify_password(data.password, actor.password_hash if actor else fallback)
    if not actor or not valid:
        raise HTTPException(401, "登录失败，请检查账号和密码")
    old = request.cookies.get("admin_session", "")
    if old:
        db.execute(delete(m.Session).where(m.Session.token_hash == token_hash(old)))
    issue_session(db, response, admin_id=actor.id)
    audit(db, actor, "login", actor.id)
    db.commit()
    return {"username": actor.username}


@app.get("/api/admin/me")
def admin_me(actor=Depends(admin)):
    return {"username": actor.username}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response, actor=Depends(admin), db: DBSession = Depends(get_db)):
    audit(db, actor, "logout", actor.id)
    return logout_session(request, response, db, True)


@app.get("/api/admin/overview")
def overview(actor=Depends(admin), db: DBSession = Depends(get_db)):
    from zoneinfo import ZoneInfo
    from datetime import datetime
    report = weekly(db, datetime.now(ZoneInfo("Asia/Shanghai")).date())
    return {"weekly": report["counts"], "total_issue_groups": report["total_issue_groups"],
            "open_events": db.scalar(select(func.count()).select_from(m.EmergencyEvent).where(m.EmergencyEvent.state != "closed")),
            "mail_attention": db.scalar(select(func.count()).select_from(m.MailJob).where(
                m.MailJob.status.in_(["failed", "unconfigured", "uncertain", "retry"]))),
            "pending_questions": db.scalar(select(func.count()).select_from(m.Question).where(m.Question.status == "pending")),
            "documents": db.scalar(select(func.count()).select_from(m.Document))}


@app.get("/api/admin/issues")
def issues(reason: str = "", state: str = "", keyword: str = "", actor=Depends(admin), db: DBSession = Depends(get_db)):
    query = select(m.Issue).where(m.Issue.merged_into.is_(None),
        m.Issue.question_id.not_in(select(m.EmergencyEvent.question_id))).order_by(m.Issue.created_at.desc())
    if reason:
        query = query.where(m.Issue.reason == reason)
    if state:
        query = query.where(m.Issue.state == state)
    data = [issue_data(db, x) for x in db.scalars(query)]
    if keyword:
        data = [x for x in data if keyword.lower() in str(x).lower()]
    return data


@app.get("/api/admin/issues/{issue_id}")
def issue_detail(issue_id: str, actor=Depends(admin), db: DBSession = Depends(get_db)):
    return issue_data(db, required(db, m.Issue, issue_id))


@app.patch("/api/admin/issues/{issue_id}")
def issue_update(issue_id: str, data: IssueUpdate, actor=Depends(admin), db: DBSession = Depends(get_db)):
    item = required(db, m.Issue, issue_id)
    if item.merged_into:
        raise HTTPException(409, "请处理合并后的主问题")
    ensure_evidence(db, data.evidence_ids)
    if data.state == "completed" and item.reason not in {"system_error", "out_of_scope"}:
        if not data.evidence_ids or not item.retests or not item.retests[-1]["passed"]:
            raise HTTPException(422, "完成前需要已发布依据和通过的原题复测")
        retest_docs = set(item.retests[-1].get("evidence_ids", []))
        if not retest_docs.intersection(data.evidence_ids):
            raise HTTPException(422, "完成依据必须与最近复测使用的资料一致")
    history = item.history + [{"at": now().isoformat(), "by": actor.username,
                               "state": data.state, "note": data.note}]
    result = db.execute(update(m.Issue).where(m.Issue.id == item.id, m.Issue.revision == data.revision).values(
        state=data.state, owner=data.owner, evidence_ids=data.evidence_ids, history=history,
        revision=m.Issue.revision + 1))
    if result.rowcount != 1:
        raise HTTPException(409, "记录已被更新，请刷新后再提交")
    audit(db, actor, "issue_update", item.id, state=data.state)
    db.commit()
    db.expire_all()
    return issue_data(db, db.get(m.Issue, item.id))


@app.post("/api/admin/issues/{issue_id}/merge")
def merge(issue_id: str, data: MergeInput, actor=Depends(admin), db: DBSession = Depends(get_db)):
    root = required(db, m.Issue, issue_id)
    if root.merged_into or root.state in {"completed", "dismissed"}:
        raise HTTPException(422, "只可合并到未完成的主问题")
    qroot = db.get(m.Question, root.question_id)
    sources = [required(db, m.Issue, x) for x in set(data.source_ids)]
    for item in sources:
        if item.id == root.id or item.merged_into or item.reason != root.reason:
            raise HTTPException(422, "不能合并自身、已合并或原因不同的问题")
        q = db.get(m.Question, item.question_id)
        if q.context.get("business_year") != qroot.context.get("business_year") or q.context.get("admission_year") != qroot.context.get("admission_year"):
            raise HTTPException(422, "不同年度或年级的问题不能合并")
    for item in sources:
        for child in db.scalars(select(m.Issue).where(m.Issue.merged_into == item.id)):
            child.merged_into = root.id
        item.merged_into = root.id
    root.history = root.history + [{"at": now().isoformat(), "by": actor.username,
                                   "action": "merge", "source_ids": data.source_ids, "note": data.note}]
    root.revision += 1
    audit(db, actor, "issues_merge", root.id, source_ids=data.source_ids)
    db.commit()
    return issue_data(db, root)


@app.post("/api/admin/issues/{issue_id}/retest")
def retest(issue_id: str, actor=Depends(admin), db: DBSession = Depends(get_db)):
    issue = required(db, m.Issue, issue_id)
    members = [issue] + list(db.scalars(select(m.Issue).where(m.Issue.merged_into == issue.id)))
    results = []
    for member in members:
        q = db.get(m.Question, member.question_id)
        result = answer_question(db, member.text, q.context)
        results.append({"issue_id": member.id, "answer": result})
    passed = all(all(i["status"] == "answered" for i in r["answer"]["items"]) for r in results)
    doc_ids = sorted({c["document_id"] for r in results for i in r["answer"]["items"] for c in i["citations"]})
    issue.retests = issue.retests + [{"at": now().isoformat(), "by": actor.username, "passed": passed,
                                     "results": results, "evidence_ids": doc_ids}]
    issue.revision += 1
    audit(db, actor, "issue_retest", issue.id, passed=passed)
    db.commit()
    return issue_data(db, issue)


@app.get("/api/admin/weekly")
def report(day: date | None = None, actor=Depends(admin), db: DBSession = Depends(get_db)):
    from zoneinfo import ZoneInfo
    from datetime import datetime
    return weekly(db, day or datetime.now(ZoneInfo("Asia/Shanghai")).date())


def document_data(db, document):
    return {c.name: getattr(document, c.name) for c in document.__table__.columns if c.name != "storage_key"} | {
        "chunks": [{"id": c.id, "location": c.location, "text": c.text} for c in
                   db.scalars(select(m.Chunk).where(m.Chunk.document_id == document.id))]}


def parse_document(db, document):
    try:
        parts, note = parse_file(store.path(document.storage_key))
        build_chunks(db, document, parts)
        document.parse_status, document.parse_note = "ready", note
    except Exception as exc:
        document.parse_status = "failed"
        document.parse_note = str(exc)[:300] if isinstance(exc, ValueError) else "解析失败，请检查文件与OCR配置后重试"


@app.get("/api/admin/documents")
def documents(actor=Depends(admin), db: DBSession = Depends(get_db)):
    return [document_data(db, d) for d in db.scalars(select(m.Document).order_by(m.Document.created_at.desc()))]


@app.post("/api/admin/documents/upload")
def upload(file: UploadFile = File(...), actor=Depends(admin), db: DBSession = Depends(get_db)):
    raw = file.file.read(settings().max_upload_mb * 1024**2 + 1)
    name = (file.filename or "upload").replace("\\", "/").split("/")[-1][:255]
    try:
        suffix = validate_file(name, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    key = store.save(raw, suffix)
    d = m.Document(title=name[:200], original_name=name, storage_key=key, content_hash=digest(raw),
                   material_mode=settings().material_mode)
    db.add(d)
    db.flush()
    parse_document(db, d)
    audit(db, actor, "document_upload", d.id, parse_status=d.parse_status)
    db.commit()
    return document_data(db, d)


@app.post("/api/admin/documents/formal")
def formal(data: FormalReply, actor=Depends(admin), db: DBSession = Depends(get_db)):
    d = m.Document(**data.model_dump(mode="json", exclude={"content"}), material_mode=settings().material_mode)
    db.add(d)
    db.flush()
    build_chunks(db, d, [("正式答复正文", data.content)])
    audit(db, actor, "formal_reply_created", d.id)
    db.commit()
    return document_data(db, d)


@app.put("/api/admin/documents/{document_id}")
def metadata(document_id: str, data: DocumentMeta, actor=Depends(admin), db: DBSession = Depends(get_db)):
    d = required(db, m.Document, document_id)
    if d.status == "published":
        raise HTTPException(422, "已发布版本不能直接修改，请先停用或上传新版本")
    for key, value in data.model_dump(mode="json").items():
        setattr(d, key, value)
    audit(db, actor, "document_metadata", d.id)
    db.commit()
    return document_data(db, d)


@app.post("/api/admin/documents/{document_id}/retry")
def parse_retry(document_id: str, actor=Depends(admin), db: DBSession = Depends(get_db)):
    d = required(db, m.Document, document_id)
    if not d.storage_key or d.status == "published":
        raise HTTPException(422, "只能重试未发布的原始文件")
    parse_document(db, d)
    audit(db, actor, "parse_retry", d.id)
    db.commit()
    return document_data(db, d)


@app.post("/api/admin/documents/{document_id}/{action}")
def publication(document_id: str, action: str, actor=Depends(admin), db: DBSession = Depends(get_db)):
    if action not in {"publish", "disable", "reindex"}:
        raise HTTPException(404, "操作不存在")
    d = required(db, m.Document, document_id)
    chunks = list(db.scalars(select(m.Chunk).where(m.Chunk.document_id == d.id)))
    if action == "publish":
        if d.parse_status != "ready" or not chunks or not d.publisher:
            raise HTTPException(422, "发布前需要成功解析的正文和发布单位")
        if settings().embedding_mode == "bge" and any(c.bge_vector is None for c in chunks):
            index_bge([(c, d) for c in chunks])
        d.status = "published"
    elif action == "disable":
        d.status = "disabled"
    else:
        from .retrieval import vector
        for c in chunks:
            c.vector = vector(c.text)
        if settings().embedding_mode == "bge":
            index_bge([(c, d) for c in chunks])
    audit(db, actor, "document_" + action, d.id)
    db.commit()
    return document_data(db, d)


@app.get("/api/admin/documents/{document_id}/file")
def original(document_id: str, actor=Depends(admin), db: DBSession = Depends(get_db)):
    d = required(db, m.Document, document_id)
    if not d.storage_key:
        raise HTTPException(404, "该资料为正式答复，无原始附件")
    path = store.path(d.storage_key)
    if not path.is_file():
        raise HTTPException(404, "原文件不可用")
    audit(db, actor, "original_download", d.id)
    db.commit()
    return FileResponse(path, filename=d.original_name, media_type="application/octet-stream")


@app.get("/api/admin/people")
def people(actor=Depends(admin), db: DBSession = Depends(get_db)):
    audit(db, actor, "people_view", "registrations")
    result = [profile(db, p) for p in db.scalars(select(m.Registration).order_by(m.Registration.created_at.desc()))]
    db.commit()
    return result


@app.get("/api/admin/questions")
def questions(registration_id: str = "", actor=Depends(admin), db: DBSession = Depends(get_db)):
    query = select(m.Question).order_by(m.Question.created_at.desc())
    if registration_id:
        query = query.where(m.Question.registration_id == registration_id)
    result = [{**question_response(db, q), "snapshot": q.snapshot, "context": q.context} for q in db.scalars(query)]
    audit(db, actor, "questions_view", registration_id or "all")
    db.commit()
    return result


@app.get("/api/admin/events")
def events(actor=Depends(admin), db: DBSession = Depends(get_db)):
    result = []
    for e in db.scalars(select(m.EmergencyEvent).order_by(m.EmergencyEvent.created_at.desc())):
        q = db.get(m.Question, e.question_id)
        job = db.scalar(select(m.MailJob).where(m.MailJob.event_id == e.id))
        attempts = list(db.scalars(select(m.MailAttempt).where(m.MailAttempt.job_id == job.id)))
        result.append({"id": e.id, "question_id": q.id, "text": q.text, "snapshot": q.snapshot,
                       "state": e.state, "history": e.history, "created_at": e.created_at,
                       "mail": {"id": job.id, "mode": job.mode, "status": job.status, "body": job.body,
                                "attempts": job.attempts, "error_code": job.error_code, "sent_at": job.sent_at,
                                "attempt_history": [{"result": x.result, "at": x.created_at} for x in attempts]}})
    return result


@app.patch("/api/admin/events/{event_id}")
def handle_event(event_id: str, data: EventUpdate, actor=Depends(admin), db: DBSession = Depends(get_db)):
    e = required(db, m.EmergencyEvent, event_id)
    e.state = data.state
    e.history = e.history + [{"at": now().isoformat(), "by": actor.username, **data.model_dump()}]
    audit(db, actor, "event_disposition", e.id, state=data.state)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/events/{event_id}/retry")
def retry_event(event_id: str, actor=Depends(admin), db: DBSession = Depends(get_db)):
    job = db.scalar(select(m.MailJob).where(m.MailJob.event_id == event_id))
    if not job:
        raise HTTPException(404, "事件不存在")
    if job.status not in {"failed", "unconfigured", "retry"}:
        raise HTTPException(409, "成功、发送中或投递不确定的任务不能重发；不确定任务需先人工核对邮箱")
    job.status, job.next_attempt, job.attempts = "pending", now(), 0
    audit(db, actor, "mail_retry", job.id, channel=job.mode)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/retention-preview")
def preview(actor=Depends(admin), db: DBSession = Depends(get_db)):
    return retention_preview(db)


@app.get("/api/admin/settings")
def admin_settings(actor=Depends(admin), db: DBSession = Depends(get_db)):
    from .alerts import smtp_ready
    cfg = settings()
    return {**modes(), "model_configured": cfg.model_mode == "mock" or bool(cfg.deepseek_api_key),
            "smtp_ready": smtp_ready(), "weekly_smtp_ready": smtp_ready(weekly=True),
            "weekly_mail_enabled": cfg.weekly_mail_enabled, "real_email_authorized": cfg.allow_real_email,
            "retriever": f"keyword+{BGE_MODEL}" if cfg.embedding_mode == "bge" else "keyword+character-hash-vector-v1",
            "ocr_enabled": cfg.ocr_enabled,
            "retention": "preview_only", "daily_model_limit": cfg.model_daily_limit,
            "pending_mail": db.scalar(select(func.count()).select_from(m.MailJob).where(m.MailJob.status.in_(["pending", "retry", "sending"])))}


@app.get("/api/admin/audit")
def audit_list(actor=Depends(admin), db: DBSession = Depends(get_db)):
    return [{"id": x.id, "action": x.action, "object_id": x.object_id, "admin_id": x.admin_id,
             "detail": x.detail, "created_at": x.created_at} for x in
            db.scalars(select(m.Audit).order_by(m.Audit.created_at.desc()).limit(300))]
