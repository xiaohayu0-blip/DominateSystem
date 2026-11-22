"""Question progress, diagnostics and the boundary before a paid model call."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import monotonic

STAGES = ("received", "searching", "checking", "composing")
_execution = ContextVar("question_execution", default=None)
_stages = {}
_lock = Lock()
logger = logging.getLogger("uvicorn.error.question")


def log_question_event(question_id, event, *, warning=False, **fields):
    logger.log(logging.WARNING if warning else logging.INFO, "question_id=%s event=%s %s",
               question_id, event, " ".join(f"{key}={value}" for key, value in fields.items()))


def report_question_event(event, *, warning=False, **fields):
    execution = _execution.get()
    if execution:
        question_id, started, _ = execution
        log_question_event(question_id, event, warning=warning,
                           elapsed_ms=round((monotonic() - started) * 1000), **fields)


def mark_model_request():
    execution = _execution.get()
    if execution:
        _, _, mark_started = execution
        if mark_started:
            mark_started()
        report_question_event("model_request")


def report_progress(stage):
    execution = _execution.get()
    if execution and stage in STAGES:
        question_id, _, _ = execution
        with _lock:
            if _stages.get(question_id) == stage:
                return
            _stages[question_id] = stage
        report_question_event("progress", stage=stage)


def question_progress(question_id):
    with _lock:
        return _stages.get(question_id, "received")


@contextmanager
def track_progress(question_id, mark_started=None):
    token = _execution.set((question_id, monotonic(), mark_started))
    report_progress("received")
    try:
        yield
    finally:
        with _lock:
            _stages.pop(question_id, None)
        _execution.reset(token)
