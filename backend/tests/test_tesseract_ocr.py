from subprocess import CompletedProcess

from app import storage
from app.config import settings


def test_chinese_scan_recovers_from_tesseract_default_layout(tmp_path, monkeypatch):
    target = tmp_path / "scan.png"
    monkeypatch.setattr(settings(), "ocr_language", "chi_sim+eng")
    attempts = []

    def run(command, **kwargs):
        attempts.append(command)
        text = "奖学金申请需要提交成绩单" if command[-2:] == ["--psm", "6"] else "ae WE LM Kb TMH Tih KD 加加要提"
        return CompletedProcess(command, 0, text.encode(), b"")

    monkeypatch.setattr(storage.subprocess, "run", run)

    assert storage.ocr_image(target, deadline=None) == "奖学金申请需要提交成绩单"
    assert len(attempts) == 2
    assert attempts[1][-2:] == ["--psm", "6"]


def test_english_scan_keeps_default_layout_result(tmp_path, monkeypatch):
    target = tmp_path / "scan.png"
    monkeypatch.setattr(settings(), "ocr_language", "chi_sim+eng")

    def run(command, **kwargs):
        text = "Transcript" if command[-2:] != ["--psm", "6"] else "Transcrlpt"
        return CompletedProcess(command, 0, text.encode(), b"")

    monkeypatch.setattr(storage.subprocess, "run", run)

    assert storage.ocr_image(target, deadline=None) == "Transcript"


def test_fragmented_scan_recovers_instead_of_losing_english_words(tmp_path, monkeypatch):
    target = tmp_path / "scan.png"
    monkeypatch.setattr(settings(), "ocr_language", "chi_sim+eng")

    def run(command, **kwargs):
        if command[-2:] == ["--psm", "6"]:
            text = "Scholarship applications require a transcript."
        else:
            text = "Sc\nha\nars\nhi\nications require a transcri\npt\n"
        return CompletedProcess(command, 0, text.encode(), b"")

    monkeypatch.setattr(storage.subprocess, "run", run)

    assert storage.ocr_image(target, deadline=None) == "Scholarship applications require a transcript."
