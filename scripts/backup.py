"""Offline, consistent database + original-file backup. No destructive restore paths."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402


def pg_env(url):
    parsed = urlparse(url.replace("postgresql+psycopg:", "postgresql:"))
    return {**os.environ, "PGHOST": parsed.hostname or "localhost", "PGPORT": str(parsed.port or 5432),
            "PGUSER": unquote(parsed.username or ""), "PGPASSWORD": unquote(parsed.password or ""),
            "PGDATABASE": parsed.path.lstrip("/")}


def backup(destination: Path):
    cfg = settings()
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("备份目标已存在，拒绝覆盖")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        folder = Path(temp)
        if cfg.database_url.startswith("sqlite:///"):
            source = Path(cfg.database_url.removeprefix("sqlite:///")).resolve()
            if not source.is_file():
                raise ValueError("源数据库不存在")
            dbfile, kind = folder / "database.sqlite", "sqlite"
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(dbfile)) as dst:
                src.backup(dst)
        else:
            dbfile, kind = folder / "database.dump", "postgresql"
            subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dbfile)],
                           env=pg_env(cfg.database_url), check=True, capture_output=True)
        files = [(dbfile, dbfile.name)]
        if cfg.storage_dir.exists():
            for file in cfg.storage_dir.iterdir():
                if file.is_symlink():
                    raise ValueError("原文件目录包含符号链接，拒绝备份")
                if file.is_file():
                    files.append((file, "files/" + file.name))
        manifest = {"schema": 1, "database": kind, "created_at": datetime.now(timezone.utc).isoformat(),
                    "files": {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in files}}
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for file, name in files:
                archive.write(file, name)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def restore(archive_path: Path, destination: Path):
    """Extract only to a new directory; never connect to or overwrite an existing database."""
    target = destination.resolve()
    if target.exists():
        raise ValueError("恢复目录必须不存在，拒绝覆盖任何现有数据")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist()) > 50000 or sum(x.file_size for x in archive.infolist()) > 10 * 1024**3:
            raise ValueError("备份包超过恢复边界")
        manifest = json.loads(archive.read("manifest.json"))
        allowed = set(manifest["files"]) | {"manifest.json"}
        if set(archive.namelist()) != allowed or len(archive.namelist()) != len(allowed):
            raise ValueError("备份清单与文件不符")
        for name, expected in manifest["files"].items():
            path = (target / name).resolve()
            if not path.is_relative_to(target) or name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise ValueError("备份包包含非法路径")
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("备份校验失败")
        target.mkdir()
        for name in allowed:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(name))
    if manifest["database"] == "sqlite":
        with closing(sqlite3.connect(target / "database.sqlite")) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("恢复数据库完整性检查失败")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backup")
    back.add_argument("--destination", required=True, type=Path)
    rest = sub.add_parser("restore")
    rest.add_argument("--archive", required=True, type=Path)
    rest.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        data = backup(args.destination) if args.command == "backup" else restore(args.archive, args.destination)
        print(f"{args.command}: ok; {len(data['files'])} files verified; database={data['database']}")
    except Exception as exc:
        print(f"Operation failed ({type(exc).__name__}). Existing data was not overwritten.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
