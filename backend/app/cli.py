import argparse
import getpass
import json
import secrets
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from .alerts import retention_preview, run_one
from .config import settings
from .db import SessionLocal, now
from .models import Admin, ChildEntry, Document, Issue, Question, Registration
from .retrieval import build_chunks
from .security import hash_password


def create_admin(username: str, password: str):
    if len(password) < 12:
        raise ValueError("管理员密码至少12位")
    with SessionLocal() as db:
        if db.scalar(select(Admin).where(Admin.username == username)):
            raise ValueError("管理员已存在，不覆盖密码")
        db.add(Admin(username=username, password_hash=hash_password(password)))
        db.commit()


def initialize_demo():
    cfg = settings()
    if cfg.environment != "demo" or cfg.model_mode != "mock" or cfg.mail_mode != "mock":
        raise ValueError("演示初始化仅允许demo环境且模型/邮件均为mock")
    with SessionLocal() as db:
        if db.scalar(select(Registration).limit(1)) or db.scalar(select(Document).limit(1)):
            raise ValueError("数据库非空，不覆盖已有数据。请选择独立的全新演示数据库。")
        password = secrets.token_urlsafe(18)
        db.add(Admin(username="demo-admin", password_hash=hash_password(password)))
        students = []
        for i in (1, 2):
            p = Registration(role="student", name=f"虚构学生{i}", phone=f"1380000000{i}",
                             major="演示工商管理", class_name=f"演示{i}班")
            db.add(p)
            students.append(p)
        parent = Registration(role="parent", name="虚构家长", phone="13900000001")
        db.add(parent)
        db.flush()
        for i in (1, 2):
            db.add(ChildEntry(parent_id=parent.id, name=f"虚构孩子{i}", phone=f"1380000000{i}",
                              major="演示会计", class_name=f"演示{i}班", relationship="母亲"))
        year = date.today().year
        policies = [
            ("【合成演示】奖学金申请办法", year, None,
             "奖学金申请材料包括申请表与成绩单。申请人需在10个工作日内向学院提交材料。材料提交不代表资格通过，最终结果由人工评审决定。"),
            ("【合成演示】往年奖学金申请办法", year - 1, None,
             "奖学金申请材料包括申请表、成绩单与个人说明。往年材料须在15个工作日内提交，不适用本年度申请。"),
            ("【合成演示】学生请假规定", None, None,
             "请假流程：学生先填写请假申请，说明起止时间和事由；由辅导员人工审核。未经批准不能视为已获准请假。"),
            ("【合成演示】新生转专业说明", year, year - 2,
             f"转专业申请仅适用{year - 2}级入学学生。申请材料包括学习计划与成绩单，由学院进行人工审查。"),
            ("【合成演示】实习申请办法A", year, None,
             "实习申请材料应于5天内提交，由学院核实实习单位和安全安排。"),
            ("【合成演示】实习申请办法B（冲突样本）", year, None,
             "实习申请材料应于8天内提交，由学院核实实习单位和安全安排。"),
        ]
        for title, business_year, admission_year, content in policies:
            d = Document(title=title, publisher="虚构管理学院（演示）", version="demo-1",
                         business_year=business_year, admission_year=admission_year,
                         status="published", material_mode="synthetic")
            db.add(d)
            db.flush()
            build_chunks(db, d, [("合成制度第1条", content)])
        p = students[0]
        snapshot = {"id": p.id, "role": p.role, "name": p.name, "phone": p.phone,
                    "major": p.major, "class_name": p.class_name, "children": [], "identity_verified": False}
        old = now() - timedelta(days=8)
        q = Question(registration_id=p.id, request_id="demo-historical-backlog", text="校外住宿补助如何申请？",
                     snapshot=snapshot, context={"business_year": year, "admission_year": None},
                     status="complete", model_mode="mock", created_at=old,
                     answer={"items": [{"question": "校外住宿补助如何申请", "status": "missing_material",
                                        "content": "合成资料不包含此事项，已记录待补充。", "citations": []}]})
        db.add(q)
        db.flush()
        db.add(Issue(question_id=q.id, text=q.text, reason="missing_material", created_at=old))
        db.commit()
    output = Path("runtime/demo-access.txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"仅限本地虚构演示\n管理员账号：demo-admin\n管理员密码：{password}\n", encoding="utf-8")
    print("演示数据已初始化；随机管理员密码保存在 runtime/demo-access.txt（已被.gitignore排除）。")


def build_bge_index():
    from .embeddings import BGE_MODEL, bge_model
    from .models import Chunk
    from .retrieval import index_bge

    bge_model(allow_download=True)
    with SessionLocal() as db:
        rows = db.execute(select(Chunk, Document).join(Document).where(Document.parse_status == "ready")).all()
        index_bge(rows, allow_download=True)
        db.commit()
    print(f"{BGE_MODEL}: 已索引 {len(rows)} 个资料片段")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-admin", "demo-init", "mail-once", "retention-preview",
                                            "bge-index", "group-duplicate-issues"])
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.username, getpass.getpass("New administrator password (12+ characters): "))
    elif args.command == "demo-init":
        initialize_demo()
    elif args.command == "mail-once":
        print("processed" if run_one() else "idle")
    elif args.command == "bge-index":
        build_bge_index()
    elif args.command == "group-duplicate-issues":
        from .services import group_existing_issues

        with SessionLocal() as db:
            grouped = group_existing_issues(db)
            db.commit()
        print(f"已归并 {grouped} 条重复待处理问题")
    else:
        with SessionLocal() as db:
            print(json.dumps(retention_preview(db), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
