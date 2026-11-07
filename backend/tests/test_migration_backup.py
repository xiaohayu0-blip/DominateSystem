import importlib.util
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import SessionLocal
from app.models import Registration


def test_migration_repeat_preserves_data():
    with SessionLocal() as db:
        p = Registration(role="student", name="虚构迁移测试", phone="13800000001", major="会计", class_name="一班")
        db.add(p)
        db.commit()
        person_id = p.id
    command.upgrade(Config("alembic.ini"), "head")
    command.check(Config("alembic.ini"))
    with SessionLocal() as db:
        assert db.get(Registration, person_id).name == "虚构迁移测试"
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_backup_restore_and_reject_existing_or_tampered(tmp_path):
    # Use workspace path because managed Windows sandbox may not expose sqlite temp paths.
    import tempfile
    import zipfile
    folder = Path(tempfile.mkdtemp(dir="runtime/tests")).resolve()
    spec = importlib.util.spec_from_file_location("backup_tool", Path("../scripts/backup.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from app.storage import store
    key = store.save(b"synthetic,csv\n", ".csv")
    with SessionLocal() as db:
        db.add(Registration(role="student", name="虚构备份测试", phone="13800000001", major="会计", class_name="一班"))
        db.commit()
    archive = folder / "backup.zip"
    module.backup(archive)
    restored = folder / "restored"
    module.restore(archive, restored)
    assert (restored / "files" / key).read_bytes() == b"synthetic,csv\n"
    with sqlite3.connect(restored / "database.sqlite") as db:
        assert db.execute("SELECT name FROM registrations").fetchone()[0] == "虚构备份测试"
    import pytest
    with pytest.raises(ValueError):
        module.restore(archive, restored)
    with zipfile.ZipFile(folder / "tampered.zip", "w") as bad, zipfile.ZipFile(archive) as good:
        for name in good.namelist():
            bad.writestr(name, b"wrong" if name.startswith("files/") else good.read(name))
    with pytest.raises(ValueError):
        module.restore(folder / "tampered.zip", folder / "tampered-restore")
    assert not (folder / "tampered-restore").exists()
