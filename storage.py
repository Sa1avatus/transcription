"""
storage.py — in-memory кэш задач + очередь.

При старте восстанавливается из SQLite (см. app.py → startup).
Задачи pending/processing при рестарте → error.
"""

import asyncio
import json
from typing import Optional

from config import settings

task_store: dict = {}


class TaskQueue:
    """Queue backed by Redis in containers and asyncio for one-process runs."""

    key = "transcription:tasks"

    def __init__(self) -> None:
        self._local: asyncio.Queue = asyncio.Queue()
        self._redis = None

    async def _client(self):
        if not settings.redis_url:
            return None
        if self._redis is None:
            import redis.asyncio as redis
            self._redis = redis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    async def put(self, task: tuple | dict) -> None:
        client = await self._client()
        if client:
            await client.rpush(self.key, json.dumps(task))
        else:
            await self._local.put(task)

    async def get(self) -> tuple | list | dict:
        client = await self._client()
        if client:
            _, payload = await client.blpop(self.key, timeout=0)
            # A transcription task is serialized as a list, while auxiliary
            # jobs (for example a translation) are dictionaries.  Do not
            # coerce the decoded value: ``tuple(dict)`` would return keys,
            # not the job itself.
            return json.loads(payload)
        return await self._local.get()

    def task_done(self) -> None:
        if not settings.redis_url:
            self._local.task_done()


task_queue = TaskQueue()


def store_put(
    task_id: str,
    uid: Optional[str] = None,
    chat_id: Optional[str] = None,
    status: str = "pending",
) -> None:
    """Добавляет новую запись в кэш."""
    task_store[task_id] = {
        "status": status,
        "result": None,
        "uid": uid,
        "chat_id": chat_id,
    }


def store_update(task_id: str, status: str, task_result: Optional[str] = None) -> None:
    """Обновляет статус и результат записи в кэше."""
    if task_id in task_store:
        task_store[task_id]["status"] = status
        if task_result is not None:
            task_store[task_id]["result"] = task_result
