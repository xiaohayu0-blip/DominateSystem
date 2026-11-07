"""Run against local mock Demo only; create a synthetic file to exercise original-file backup."""
import sys
from pathlib import Path

import httpx

access = Path("runtime/demo-access.txt").read_text(encoding="utf-8")
password = access.split("管理员密码：", 1)[1].splitlines()[0]
with httpx.Client(base_url="http://127.0.0.1:8000", timeout=20) as client:
    modes = client.get("/api/modes").json()
    if modes["environment"] != "demo" or modes["model"] != "mock" or modes["mail"] != "mock":
        sys.exit("Only local mock Demo is allowed")
    response = client.post("/api/admin/login", json={"username": "demo-admin", "password": password})
    response.raise_for_status()
    uploaded = client.post("/api/admin/documents/upload", files={
        "file": ("synthetic-backup-drill.csv", "演示事项,合成材料\n备份恢复演练,仅用于测试原文件恢复\n".encode("utf-8"), "text/csv")})
    uploaded.raise_for_status()
    assert uploaded.json()["parse_status"] == "ready"
    client.post("/api/admin/logout").raise_for_status()
print("Synthetic original uploaded; draft only; no external calls")
