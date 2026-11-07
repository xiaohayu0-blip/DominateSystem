import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("folder", type=Path)
args = parser.parse_args()
with closing(sqlite3.connect(args.folder / "database.sqlite")) as db:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    tables = ["registrations", "child_entries", "questions", "issues", "documents", "chunks", "events", "mail_jobs", "audit"]
    result = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    assert all(result[t] > 0 for t in tables)
    print(json.dumps({"status": "ok", "restored_rows": result}, ensure_ascii=False))
