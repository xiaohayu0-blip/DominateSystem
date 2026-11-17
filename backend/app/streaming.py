"""Progress plus character streaming of persisted, evidence-checked answers."""

import asyncio
import json
import time

from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool

CHAR_DELAY_SECONDS = 0.01
WAIT_TIMEOUT_SECONDS = 180


def event(kind, value):
    return f'event: {kind}\ndata: {json.dumps(jsonable_encoder(value), ensure_ascii=False)}\n\n'


async def answer_events(load, disconnected):
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    previous_stage = None
    while True:
        if await disconnected():
            return
        question = await run_in_threadpool(load)
        if question is None:
            yield event('error', {'message': '记录不存在或无权访问'})
            return
        if question['status'] == 'complete':
            break
        stage = question.get('progress') or 'received'
        if stage != previous_stage:
            yield event('progress', {'stage': stage})
            previous_stage = stage
        else:
            yield ': keep-alive\n\n'
        if time.monotonic() >= deadline:
            yield event('error', {'message': '回答仍在处理，可点击“检查回答状态”继续读取，请勿重复提交。'})
            return
        await asyncio.sleep(0.35)

    # Never expose draft JSON, unverified model numbers, or invented citations.
    for index, item in enumerate(question['answer'].get('items', [])):
        yield event('item', {'index': index, 'item': {**item, 'content': '', 'citations': []}})
        for char in item['content']:
            if await disconnected():
                return
            yield event('delta', {'index': index, 'text': char})
            await asyncio.sleep(CHAR_DELAY_SECONDS)
    yield event('complete', question)
