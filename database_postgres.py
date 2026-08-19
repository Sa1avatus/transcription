"""PostgreSQL persistence backend used by the multi-pod deployment.

The public functions deliberately match ``database.py`` so application code is
independent of the storage engine.  Calls run in an executor because psycopg's
small synchronous connection API is reliable and keeps the existing service
interface unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from config import logger, settings


def _connect():
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_init_db() -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                uid TEXT,
                chat_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                poll_url TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                src_lang TEXT NOT NULL,
                tgt_lang TEXT NOT NULL,
                translated TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE (task_id, tgt_lang)
            )
        """)
    logger.info("[DB] PostgreSQL initialised")


def _sync_insert_task(task_id: str, uid: Optional[str], poll_url: str, chat_id: Optional[str]) -> None:
    now = _now()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tasks (task_id, uid, chat_id, status, result, poll_url, created_at, updated_at)
               VALUES (%s, %s, %s, 'pending', NULL, %s, %s, %s)""",
            (task_id, uid, chat_id, poll_url, now, now),
        )


def _sync_update_task(task_id: str, status: str, task_result: Optional[str]) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE tasks SET status = %s, result = COALESCE(%s, result), updated_at = %s
               WHERE task_id = %s""",
            (status, task_result, _now(), task_id),
        )


def _fetchone(query: str, params: tuple) -> Optional[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall(query: str, params: tuple = ()) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


async def _run(blocking_operation, *args):
    return await asyncio.get_running_loop().run_in_executor(None, blocking_operation, *args)


async def init_db() -> None:
    await _run(_sync_init_db)


async def db_insert_task(task_id: str, uid: Optional[str], poll_url: str, chat_id: Optional[str] = None) -> None:
    await _run(_sync_insert_task, task_id, uid, poll_url, chat_id)


async def db_update_task(task_id: str, status: str, task_result: Optional[str] = None) -> None:
    await _run(_sync_update_task, task_id, status, task_result)


async def db_get_task(task_id: str) -> Optional[dict]:
    return await _run(_fetchone, "SELECT * FROM tasks WHERE task_id = %s", (task_id,))


async def db_all_tasks() -> list:
    return await _run(_fetchall, """SELECT task_id, uid, chat_id, status, poll_url, created_at, updated_at
        FROM tasks ORDER BY created_at DESC""")


async def db_restore_task_store() -> dict:
    rows = await _run(_fetchall, "SELECT task_id, uid, chat_id, status, result FROM tasks")
    # A Kubernetes API pod may restart while a separate worker is still
    # processing.  It must never rewrite that worker's status during startup.
    restored = {}
    for row in rows:
        restored[row["task_id"]] = row
    return restored


def _sync_upsert_translation(task_id: str, src_lang: str, tgt_lang: str, translated: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO translations (task_id, src_lang, tgt_lang, translated, created_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (task_id, tgt_lang) DO UPDATE SET
                   translated = EXCLUDED.translated, created_at = EXCLUDED.created_at""",
            (task_id, src_lang, tgt_lang, translated, _now()),
        )


async def db_upsert_translation(task_id: str, src_lang: str, tgt_lang: str, translated: str) -> None:
    await _run(_sync_upsert_translation, task_id, src_lang, tgt_lang, translated)


async def db_get_translation(task_id: str, tgt_lang: str) -> Optional[dict]:
    return await _run(_fetchone, "SELECT * FROM translations WHERE task_id = %s AND tgt_lang = %s", (task_id, tgt_lang))


async def db_get_all_translations(task_id: str) -> list:
    return await _run(_fetchall, "SELECT tgt_lang, src_lang, created_at FROM translations WHERE task_id = %s", (task_id,))


def _sync_user_statistics(chat_id: str) -> dict:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status = 'done') AS done,
                      COUNT(*) FILTER (WHERE status = 'error') AS errors,
                      COUNT(*) FILTER (WHERE status IN ('pending', 'processing')) AS active
                 FROM tasks WHERE chat_id = %s""",
            (chat_id,),
        )
        summary = cur.fetchone()
        cur.execute(
            """SELECT COUNT(*) AS total FROM translations tr
                 JOIN tasks t ON t.task_id = tr.task_id WHERE t.chat_id = %s""",
            (chat_id,),
        )
        translations = cur.fetchone()["total"]
        cur.execute(
            """SELECT tr.tgt_lang AS language, COUNT(*) AS total FROM translations tr
                 JOIN tasks t ON t.task_id = tr.task_id WHERE t.chat_id = %s
                 GROUP BY tr.tgt_lang ORDER BY total DESC, language LIMIT 5""",
            (chat_id,),
        )
        languages = list(cur.fetchall())
        cur.execute(
            """SELECT task_id, status, created_at, updated_at FROM tasks
                 WHERE chat_id = %s ORDER BY created_at DESC LIMIT 10""",
            (chat_id,),
        )
        recent = list(cur.fetchall())

    for row in recent:
        for key in ("created_at", "updated_at"):
            row[key] = row[key].isoformat() if row[key] else None
    return {**summary, "translations": translations, "languages": languages, "recent": recent}


async def db_get_user_statistics(chat_id: str) -> dict:
    return await _run(_sync_user_statistics, chat_id)
