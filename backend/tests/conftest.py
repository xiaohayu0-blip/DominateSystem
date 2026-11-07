import os
import tempfile
from pathlib import Path

Path("runtime/tests").mkdir(parents=True, exist_ok=True)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="dominate-tests-", dir="runtime/tests")).resolve()
os.environ["DATABASE_URL"] = "sqlite:///" + str(TEST_ROOT / "test.db").replace("\\", "/")
os.environ["STORAGE_DIR"] = str(TEST_ROOT / "files")
os.environ["ENVIRONMENT"] = "test"
os.environ["MODEL_MODE"] = "mock"
os.environ["MAIL_MODE"] = "mock"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import Base, SessionLocal  # noqa: E402
from app.main import app, limits  # noqa: E402
from app.models import Admin  # noqa: E402
from app.security import hash_password  # noqa: E402

command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_db():
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.add(Admin(username="test-admin", password_hash=hash_password("test-secret-strong")))
        db.commit()
    limits.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(client):
    assert client.post("/api/admin/login", json={"username": "test-admin", "password": "test-secret-strong"}).status_code == 200
    return client


def student(name="演示甲", phone="13800000001"):
    return {"role": "student", "name": name, "phone": phone, "major": "工商管理", "class_name": "演示一班"}


def parent():
    return {"role": "parent", "name": "虚构家长", "phone": "13900000001", "children": [
        {"name": "虚构甲", "phone": "13800000001", "major": "工商管理", "class_name": "一班", "relationship": "母亲"},
        {"name": "虚构乙", "phone": "13800000002", "major": "会计", "class_name": "二班", "relationship": "母亲"}]}


def signup(client, profile, **kwargs):
    return client.post("/api/register", json={
        **profile, "password": "test-secret-strong",
    }, **kwargs)
