"""
storage.py — in-memory кэш задач + очередь.

При старте восстанавливается из SQLite (см. app.py → startup).
Задачи pending/processing при рестарте → error.
"""

import asyncio
from typing import Optional

task_store: dict = {}
task_queue: asyncio.Queue = asyncio.Queue()


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


def store_update(task_id: str, status: str, result: Optional[str] = None) -> None:
    """Обновляет статус и результат записи в кэше."""
    if task_id in task_store:
        task_store[task_id]["status"] = status
        if result is not None:
            task_store[task_id]["result"] = result
