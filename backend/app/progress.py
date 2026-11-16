"""Transient progress for the existing single-process question worker."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

STAGES = ("received", "searching", "checking", "composing")
_question_id = ContextVar("progress_question_id", default=None)
_stages = {}
_lock = Lock()


def report_progress(stage):
    question_id = _question_id.get()
    if question_id and stage in STAGES:
        with _lock:
            _stages[question_id] = stage


def question_progress(question_id):
    with _lock:
        return _stages.get(question_id, "received")


@contextmanager
def track_progress(question_id):
    token = _question_id.set(question_id)
    report_progress("received")
    try:
        yield
    finally:
        with _lock:
            _stages.pop(question_id, None)
        _question_id.reset(token)
