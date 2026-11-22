"""Run one durable question queue using the existing questions table."""

from contextlib import contextmanager
from threading import Event, Lock, Thread

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal, engine, now
from .models import Issue, Question
from .progress import log_question_event, logger
from .services import finish_question


local_worker_lock = Lock()


def interrupt_question(question):
    question.status = "complete"
    question.completed_at = now()
    question.processing_stage = None
    question.answer = {"items": [{"question": question.text, "status": "system_error",
                                   "content": "上次回答处理被中断，已记录技术故障，请重新提问。",
                                   "citations": []}]}
    return Issue(question_id=question.id, text=question.text, reason="system_error")


def recover_questions():
    with SessionLocal() as db:
        # A row from before the migration has no record of whether the model was called.
        interrupted = list(db.scalars(select(Question).where(
            Question.status == "pending",
            (Question.processing_stage == "model_started") | Question.processing_stage.is_(None),
        ).with_for_update()))
        for question in interrupted:
            db.add(interrupt_question(question))
        requeued = db.scalars(update(Question).where(
            Question.status == "pending", Question.processing_stage == "running",
        ).values(processing_stage="queued").returning(Question.id)).all()
        db.commit()
    for question in interrupted:
        log_question_event(question.id, "interrupted", reason="restart", warning=True)
    for question_id in requeued:
        log_question_event(question_id, "requeued", reason="restart_before_model")


def claim_question(connection):
    next_id = select(Question.id).where(
        Question.status == "pending", Question.processing_stage == "queued",
    ).order_by(Question.created_at, Question.id).limit(1).scalar_subquery()
    claimed = connection.execute(update(Question).where(
        Question.id == next_id, Question.status == "pending", Question.processing_stage == "queued",
    ).values(processing_stage="running").returning(Question.id, Question.created_at)).first()
    connection.commit()
    if claimed:
        question_id, created_at = claimed
        log_question_event(question_id, "claimed", wait_ms=round((now() - created_at).total_seconds() * 1000))
        return question_id
    return None


@contextmanager
def worker_lock():
    if engine.dialect.name != "postgresql":
        acquired = local_worker_lock.acquire(blocking=False)
        try:
            if acquired:
                with engine.connect() as connection:
                    yield connection
            else:
                yield None
        finally:
            if acquired:
                local_worker_lock.release()
        return
    # Keep exactly one consumer during overlapping API starts. A disconnected session loses the lock.
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(74831905)"))
        connection.commit()
        try:
            yield connection if acquired else None
        finally:
            if acquired:
                try:
                    connection.execute(text("SELECT pg_advisory_unlock(74831905)"))
                    connection.commit()
                except SQLAlchemyError:
                    connection.invalidate()
                    raise


class QuestionWorker:
    def __init__(self):
        self.ready = Event()
        self.wake = Event()
        self.stopped = Event()
        self.thread = Thread(target=self.run, name="question-queue", daemon=True)

    def close(self):
        self.stopped.set()
        self.wake.set()
        self.thread.join()

    def run(self):
        while not self.stopped.is_set():
            try:
                with worker_lock() as connection:
                    if connection is not None:
                        recover_questions()
                    self.ready.set()
                    if connection is not None:
                        self.consume(connection)
            except Exception as exc:
                logger.warning("event=question_queue_error error_type=%s", type(exc).__name__)
                self.ready.set()
            self.stopped.wait(1)

    def consume(self, connection):
        while not self.stopped.is_set():
            self.wake.clear()
            question_id = claim_question(connection)
            if not question_id:
                self.wake.wait(1)
                continue
            try:
                finish_question(question_id)
            except Exception as exc:
                log_question_event(question_id, "persistence_error", warning=True, error_type=type(exc).__name__)
                with SessionLocal() as db:
                    question = db.get(Question, question_id, with_for_update=True)
                    if question and question.status == "pending":
                        db.add(interrupt_question(question))
                        db.commit()
