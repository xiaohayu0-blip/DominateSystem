import io
import uuid
import zipfile

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Chunk, Document
from app.qa import DeepSeekModel, ExtractiveMock, ModelFailure
from app.retrieval import build_chunks
from app.storage import parse_file, store
from conftest import signup, student


def publish(title="奖学金办法", content="奖学金申请材料包括申请表和成绩单，由学院人工审核。", year=None, grade=None,
            start=None, end=None, status="published"):
    with SessionLocal() as db:
        d = Document(title=title, publisher="合成演示学院", business_year=year, admission_year=grade,
                     effective_from=start, effective_to=end, status=status)
        db.add(d)
        db.flush()
        build_chunks(db, d, [("第1条", content)])
        db.commit()
        return d.id


def test_bge_indexes_new_chunks_and_reranks_within_selected_year(monkeypatch):
    from app import retrieval
    from app.config import settings

    monkeypatch.setattr(settings(), "embedding_mode", "bge")
    first_axis = [1.0, 0.0] + [0.0] * 510
    second_axis = [0.0, 1.0] + [0.0] * 510
    monkeypatch.setattr(retrieval, "embed_query", lambda text: first_axis)
    monkeypatch.setattr(retrieval, "embed_passages", lambda passages, **kwargs: [
        first_axis if "目标依据" in text else second_axis for text in passages
    ])
    other = publish("请假规则", "学生请假需要提交说明。", year=2023)
    best = publish("请假规则", "目标依据：学生请假需要提交说明。", year=2023)
    publish("请假规则", "目标依据：学生请假需要提交说明。", year=2024)
    with SessionLocal() as db:
        results, _ = retrieval.retriever.search(db, "学生请假需要提交说明", {"business_year": 2023})
        assert [doc.id for _, doc in results] == [best, other]
        assert all(len(chunk.bge_vector) == 512 and len(chunk.vector) == 128 for chunk, _ in results)


def ask(client, text, **kwargs):
    data = {"request_id": uuid.uuid4().hex, "text": text, **kwargs}
    response = client.post("/api/questions", json=data)
    assert response.status_code == 202, response.text
    return client.get("/api/questions/" + response.json()["id"]).json()


def test_year_grade_validity_and_citations(client):
    signup(client, student())
    publish(year=2025, content="奖学金材料包含往年说明和申请表。")
    current = publish(year=2026)
    publish(year=2026, content="奖学金已经过期的材料。", end="2025-12-31")
    result = ask(client, "2026年度奖学金申请材料")
    answer = result["answer"]["items"][0]
    assert answer["status"] == "answered"
    assert {c["document_id"] for c in answer["citations"]} == {current}
    c = answer["citations"][0]
    source = client.get("/api/sources/" + c["chunk_id"])
    assert source.status_code == 200
    assert source.json()["excerpt"] == c["excerpt"]
    assert "往年说明" in ask(client, "2025年度奖学金材料")["answer"]["items"][0]["content"]
    publish(title="转专业规定", content="转专业申请须提交学习计划。", grade=2024)
    result = ask(client, "转专业申请条件")
    assert result["answer"]["items"][0]["status"] == "needs_clarification"
    result = ask(client, "转专业申请条件", admission_year=2024, previous_id=result["id"])
    assert result["answer"]["items"][0]["status"] == "answered"


def test_partial_missing_and_conflict(client):
    signup(client, student())
    publish()
    result = ask(client, "奖学金申请材料？校外住宿补助如何申请？")
    assert [x["status"] for x in result["answer"]["items"]] == ["answered", "missing_material"]
    publish(title="实习规定甲", content="实习申请应于5天内提交材料。")
    publish(title="实习规定乙", content="实习申请应于8天内提交材料。")
    assert ask(client, "实习申请几天提交")["answer"]["items"][0]["status"] == "document_conflict"


def test_points_without_cohort_show_scoped_evidence_instead_of_scholarships(client):
    signup(client, student())
    publish("奖学金评定办法", "校级奖学金 | 综测前30% | 每人每年1000元")
    older = publish("综测办法2023版", "数学建模竞赛获省级三等奖的，综测加0.3分。", grade=2023)
    newer = publish("综测办法2024版", "数学建模竞赛获省级三等奖的，综测加0.5分。", grade=2024)
    item = ask(client, "数学建模竞赛省级三等奖综测加几分？")["answer"]["items"][0]
    assert item["status"] == "answered"
    assert {c["document_id"] for c in item["citations"]} == {older, newer}
    assert "2023级" in item["content"] and "2024级" in item["content"]
    question = "数学建模竞赛省级三等奖综测加几分？"
    latest = ask(client, question, business_year=2024, admission_year=2024)
    scoped = ask(client, question, business_year=2023, previous_id=latest["id"])["answer"]["items"][0]
    assert scoped["status"] == "answered"
    assert {c["document_id"] for c in scoped["citations"]} == {older}
    assert "0.3" in scoped["content"] and "0.5" not in scoped["content"]
    older_question = ask(client, question, business_year=2023)
    followed = ask(client, "那2024年呢？", business_year=2023, previous_id=older_question["id"])["answer"]["items"][0]
    assert followed["question"] == "那2024年呢"
    assert {c["document_id"] for c in followed["citations"]} == {newer}
    assert "0.5" in followed["content"] and "0.3" not in followed["content"]
    short = ask(client, "那24年呢。", business_year=2023, previous_id=older_question["id"])["answer"]
    assert short["context"]["business_year"] == 2024
    assert {c["document_id"] for c in short["items"][0]["citations"]} == {newer}
    independent = ask(client, "那2024年呢？")["answer"]["items"][0]
    assert independent["status"] == "missing_material"
    assert ask(client, "数学建模竞赛能认定多少学分？")["answer"]["items"][0]["status"] == "missing_material"


def test_model_error_and_invalid_reference_not_missing(client, monkeypatch):
    signup(client, student())
    publish()
    def fail(*_):
        raise ModelFailure("timeout")
    monkeypatch.setattr(ExtractiveMock, "select_evidence", fail)
    assert ask(client, "奖学金材料")["answer"]["items"][0]["status"] == "system_error"
    monkeypatch.setattr(ExtractiveMock, "select_evidence", lambda *_: {"selections": [{"chunk_id": "fake", "quote": "随意编造"}]})
    answer = ask(client, "奖学金材料")["answer"]["items"][0]
    assert answer["status"] == "validation_failed" and not answer["citations"]


def test_deepseek_displays_only_verified_excerpts(client, monkeypatch):
    signup(client, student())
    quote = "数学建模竞赛获省级三等奖的，综测加0.3分。"
    original = f"第三章 加分项目。{quote}同一项目不重复加分。"
    publish("综合素质测评办法", original, year=2023)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")

    def model_answer(_, question, evidence):
        return {"answer": "2023年综测加0.5分。",
                "selections": [{"chunk_id": evidence[0]["chunk_id"], "quote": selected_quote}]}

    monkeypatch.setattr(DeepSeekModel, "select_evidence", model_answer)
    question = "数学建模竞赛省级三等奖综测加几分？"
    selected_quote = quote
    item = ask(client, question, business_year=2023)["answer"]["items"][0]
    assert item["content"] == quote
    assert item["citations"][0]["excerpt"] == original

    selected_quote = quote.replace("0.3", "0.5")
    item = ask(client, question, business_year=2023)["answer"]["items"][0]
    assert item["status"] == "validation_failed" and not item["citations"]
    assert item["content"] == "暂无资料"


def test_current_year_overrides_inherited_year_and_model_can_abstain(client, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    signup(client, student())
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    previous = ask(client, "校外住宿补助", business_year=current_year - 1)
    publish("奖学金评定办法", "申请校级奖学金要求综合素质测评成绩位于专业前30%。", year=current_year)
    monkeypatch.setattr(settings(), "model_mode", "deepseek")
    monkeypatch.setattr(DeepSeekModel, "select_evidence",
                        lambda *_: {"answer": "资料未说明应交材料，无法回答。", "selections": []})
    answer = ask(client, "今年申请奖学金要交什么", previous_id=previous["id"])["answer"]
    assert answer["context"]["business_year"] == current_year
    assert answer["items"][0]["status"] == "missing_material"
    assert answer["items"][0]["content"] == "暂无资料"


def test_draft_disable_and_history_source(client):
    signup(client, student())
    doc_id = publish(status="draft")
    assert ask(client, "奖学金材料")["answer"]["items"][0]["status"] == "missing_material"
    with SessionLocal() as db:
        db.get(Document, doc_id).status = "published"
        db.commit()
    cited = ask(client, "奖学金材料")["answer"]["items"][0]["citations"][0]
    with SessionLocal() as db:
        db.get(Document, doc_id).status = "disabled"
        db.commit()
    assert ask(client, "奖学金材料")["answer"]["items"][0]["status"] == "missing_material"
    assert client.get("/api/sources/" + cited["chunk_id"]).json()["source_status"] == "disabled"


def test_upload_validation_retry_and_file_permission(admin_client):
    c = admin_client
    assert c.post("/api/admin/documents/upload", files={"file": ("x.exe", b"invalid")}).status_code == 422
    assert c.post("/api/admin/documents/upload", files={"file": ("x.pdf", b"invalid")}).status_code == 422
    damaged = c.post("/api/admin/documents/upload", files={"file": ("x.pdf", b"%PDF-1.7\ninvalid")}).json()
    assert damaged["parse_status"] == "failed"
    assert c.post(f'/api/admin/documents/{damaged["id"]}/retry').json()["parse_status"] == "failed"
    uploaded = c.post("/api/admin/documents/upload", files={"file": ("../../escape.csv", "奖学金,申请表\n".encode())}).json()
    assert uploaded["original_name"] == "escape.csv" and uploaded["parse_status"] == "ready"
    assert uploaded["status"] == "published" and "storage_key" not in uploaded
    assert uploaded["publisher"] == "" and uploaded["business_year"] is None
    c.post("/api/admin/logout")
    assert c.get(f'/api/admin/documents/{uploaded["id"]}/file').status_code == 401
    with pytest.raises(ValueError):
        store.path("../escape")


def test_pptx_and_zip_upload_publish_locatable_documents(admin_client, monkeypatch):
    presentation = io.BytesIO()
    with zipfile.ZipFile(presentation, "w") as deck:
        deck.writestr("ppt/presentation.xml", """
            <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                <p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
            </p:presentation>""")
        deck.writestr("ppt/_rels/presentation.xml.rels", """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                    Target="slides/slide2.xml"/>
            </Relationships>""")
        deck.writestr("ppt/slides/slide2.xml", """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                <p:sp><p:txBody><a:p><a:r><a:t>奖学金申请表</a:t></a:r></a:p></p:txBody></p:sp>
            </p:sld>""")
    c = admin_client
    response = c.post("/api/admin/documents/upload", files={"file": ("2024年说明.pptx", presentation.getvalue())})
    assert response.status_code == 200
    doc = response.json()
    assert doc["parse_status"] == "ready" and doc["status"] == "published"
    assert doc["business_year"] == 2024 and doc["publisher"] == ""
    assert doc["chunks"][0]["location"].startswith("第1页幻灯片")
    assert doc["chunks"][0]["text"] == "奖学金申请表"

    batch = io.BytesIO()
    with zipfile.ZipFile(batch, "w") as archive:
        archive.writestr("制度/说明.pptx", presentation.getvalue())
        archive.writestr("资料.csv", "奖学金,成绩单")
        archive.writestr("readme.txt", "ignored")
    response = c.post("/api/admin/documents/upload", files={"file": ("批量.zip", batch.getvalue())})
    assert response.status_code == 200
    result = response.json()
    assert len(result["documents"]) == 2 and result["skipped"] == 1
    assert all(doc["status"] == "published" for doc in result["documents"])

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("正常.csv", "制度,正文")
        archive.writestr("../越界.csv", "制度,正文")
    assert c.post("/api/admin/documents/upload", files={"file": ("越界.zip", unsafe.getvalue())}).status_code == 422
    assert len(c.get("/api/admin/documents").json()) == 3

    illustrated = io.BytesIO()
    with zipfile.ZipFile(presentation) as source, zipfile.ZipFile(illustrated, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "ppt/slides/slide2.xml":
                data = data.replace(b"</p:sld>", b"<a:blip/></p:sld>")
            target.writestr(name, data)
    upload = {"file": ("含图说明.pptx", illustrated.getvalue())}
    draft = c.post("/api/admin/documents/upload", files=upload).json()
    assert draft["status"] == "draft" and "尚未解析" in draft["parse_note"]

    from app import storage

    def recognize(path, pages, slide_count, deadline):
        assert path.suffix == ".pptx" and pages == [1] and slide_count == 1
        return [("第1页幻灯片（OCR）", "申请需提交成绩单")]

    monkeypatch.setattr(settings(), "ocr_enabled", True)
    monkeypatch.setattr(storage, "presentation_ocr", recognize)
    result = c.post(f'/api/admin/documents/{draft["id"]}/retry').json()
    assert result["status"] == "draft" and "请核对" in result["parse_note"]
    assert any("（OCR）" in chunk["location"] and "成绩单" in chunk["text"] for chunk in result["chunks"])
    assert c.post(f'/api/admin/documents/{draft["id"]}/publish').json()["status"] == "published"


@pytest.mark.parametrize("format", ["docx", "xlsx", "csv", "pdf"])
def test_actual_parsers(format):
    buffer = io.BytesIO()
    if format == "docx":
        from docx import Document as Word
        d = Word()
        d.add_paragraph("奖学金申请材料")
        d.add_table(rows=1, cols=1).cell(0, 0).text = "成绩单"
        d.save(buffer)
    elif format == "xlsx":
        from openpyxl import Workbook
        d = Workbook()
        d.active.title = "制度表"
        d.active.append(["申请材料", "成绩单"])
        d.save(buffer)
    elif format == "csv":
        buffer.write("申请材料,成绩单\n".encode())
    else:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=500, height=500)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 50 400 Td (Scholarship application form and transcript.) Tj ET")
        page[NameObject("/Contents")] = stream
        writer.write(buffer)
    key = store.save(buffer.getvalue(), "." + format)
    parts, _ = parse_file(store.path(key))
    assert parts and all(location for location, _ in parts)
    assert any(text.strip() for _, text in parts)


def test_deepseek_adapter_mock_transport(monkeypatch):
    import httpx
    import app.qa as qa
    seen = []
    real_client = httpx.Client
    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"selections": []}'}}]})
    monkeypatch.setattr(qa.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert DeepSeekModel().select_evidence("我的手机号13800000001", []) == {"selections": []}
    assert "13800000001" not in seen[0]


def test_unknown_source_and_scope(client):
    signup(client, student())
    publish()
    with SessionLocal() as db:
        chunk_id = db.scalar(select(Chunk.id))
    assert client.get("/api/sources/" + chunk_id).status_code == 404
    assert ask(client, "帮我审批奖学金")["answer"]["items"][0]["status"] == "out_of_scope"
