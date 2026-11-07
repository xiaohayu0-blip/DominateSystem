import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from .config import settings
from .db import get_db, now
from .models import Admin, Audit, Registration, Session, UserAccount


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${result}"


def verify_password(password: str, stored: str) -> bool:
    _, salt, expected = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_session(db: DBSession, response: Response, *, admin_id=None, registration_id=None):
    token = secrets.token_urlsafe(32)
    hours = settings().admin_hours if admin_id else settings().session_hours
    db.add(Session(token_hash=token_hash(token), admin_id=admin_id, registration_id=registration_id,
                   expires_at=now() + timedelta(hours=hours)))
    response.set_cookie("admin_session" if admin_id else "user_session", token,
                        max_age=hours * 3600, httponly=True, secure=settings().secure_cookies,
                        samesite="strict", path="/")


def lookup_session(request: Request, db: DBSession, admin=False):
    token = request.cookies.get("admin_session" if admin else "user_session", "")
    session = db.get(Session, token_hash(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, "会话已失效，请重新进入")
    return session


def user(request: Request, db: DBSession = Depends(get_db)) -> Registration:
    session = lookup_session(request, db)
    obj = db.get(Registration, session.registration_id) if session.registration_id else None
    if not obj or not db.get(UserAccount, obj.id):
        raise HTTPException(401, "请先注册或登录账号")
    return obj


def admin(request: Request, db: DBSession = Depends(get_db)) -> Admin:
    session = lookup_session(request, db, admin=True)
    obj = db.get(Admin, session.admin_id) if session.admin_id else None
    if not obj or not obj.active:
        raise HTTPException(401, "请使用独立管理员账号登录")
    return obj


def audit(db: DBSession, actor: Admin, action: str, object_id: str, **detail):
    db.add(Audit(admin_id=actor.id, action=action, object_id=object_id, detail=detail))
