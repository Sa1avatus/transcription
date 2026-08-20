"""
Слой персистентности на SQLite.

Таблица tasks:
  task_id    TEXT PRIMARY KEY
  uid        TEXT
  chat_id    TEXT
  status     TEXT   (pending | processing | done | error)
  result     TEXT
  poll_url   TEXT
  created_at TEXT   (ISO-8601 UTC)
  updated_at TEXT   (ISO-8601 UTC)

Таблица translations:
  id           INTEGER PRIMARY KEY AUTOINCREMENT
  task_id      TEXT    (FK → tasks.task_id)
  src_lang     TEXT    (NLLB-код источника, например 'vie_Latn')
  tgt_lang     TEXT    (NLLB-код цели,      например 'eng_Latn')
  translated   TEXT
  created_at   TEXT
  UNIQUE (task_id, tgt_lang)   — один перевод на пару задача+язык
"""

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import BASE_PATH, logger

DB_PATH = f"{BASE_PATH}/tasks.db"


# =============================================================================
# СИНХРОННЫЕ ХЕЛПЕРЫ
# =============================================================================

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id    TEXT PRIMARY KEY,
                uid        TEXT,
                chat_id    TEXT,
                status     TEXT NOT NULL DEFAULT 'pending',
                result     TEXT,
                poll_url   TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT NOT NULL,
                src_lang    TEXT NOT NULL,
                tgt_lang    TEXT NOT NULL,
                translated  TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                UNIQUE (task_id, tgt_lang)
            )
        """)
        # Миграции для старых БД без chat_id
        for col in ("chat_id TEXT",):
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    logger.info(f"[DB] SQLite инициализирована: {DB_PATH}")


def _sync_insert_task(
    task_id: str, uid: Optional[str], poll_url: str, chat_id: Optional[str]
) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (task_id, uid, chat_id, status, result, poll_url, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', NULL, ?, ?, ?)
            """,
            (task_id, uid, chat_id, poll_url, now, now),
        )
        conn.commit()


def _sync_update_task(task_id: str, status: str, task_result: Optional[str]) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE tasks
               SET status = ?, result = COALESCE(?, result), updated_at = ?
             WHERE task_id = ?
            """,
            (status, task_result, now, task_id),
        )
        conn.commit()


def _sync_get_task(task_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def _sync_all_tasks() -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT task_id, uid, chat_id, status, poll_url, created_at, updated_at"
            " FROM tasks ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def _sync_load_all_for_restore() -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT task_id, uid, chat_id, status, result FROM tasks"
        ).fetchall()
    return [dict(r) for r in rows]


# --- translations ---

def _sync_upsert_translation(
    task_id: str, src_lang: str, tgt_lang: str, translated: str
) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO translations (task_id, src_lang, tgt_lang, translated, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id, tgt_lang) DO UPDATE SET
                translated = excluded.translated,
                created_at = excluded.created_at
            """,
            (task_id, src_lang, tgt_lang, translated, now),
        )
        conn.commit()


def _sync_get_translation(task_id: str, tgt_lang: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM translations WHERE task_id = ? AND tgt_lang = ?",
            (task_id, tgt_lang),
        ).fetchone()
    return dict(row) if row else None


def _sync_get_all_translations(task_id: str) -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tgt_lang, src_lang, created_at FROM translations WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# АСИНХРОННЫЕ ОБЁРТКИ
# =============================================================================

async def _run(blocking_operation, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, blocking_operation, *args)


async def init_db() -> None:
    await _run(_sync_init_db)


async def db_insert_task(
    task_id: str, uid: Optional[str], poll_url: str, chat_id: Optional[str] = None
) -> None:
    await _run(_sync_insert_task, task_id, uid, poll_url, chat_id)


async def db_update_task(task_id: str, status: str, task_result: Optional[str] = None) -> None:
    await _run(_sync_update_task, task_id, status, task_result)


async def db_get_task(task_id: str) -> Optional[dict]:
    return await _run(_sync_get_task, task_id)


async def db_all_tasks() -> list:
    return await _run(_sync_all_tasks)


async def db_restore_task_store() -> dict:
    rows = await _run(_sync_load_all_for_restore)
    restored: dict = {}
    interrupted = []

    for row in rows:
        status = row["status"]
        if status in ("pending", "processing"):
            status = "error"
            interrupted.append(row["task_id"])

        restored[row["task_id"]] = {
            "status": status,
            "result": row["result"],
            "uid": row["uid"],
            "chat_id": row["chat_id"],
        }

    for task_id in interrupted:
        await _run(_sync_update_task, task_id, "error", "Сервер перезапущен — задача не завершена")

    if interrupted:
        logger.warning(f"[DB] Помечены как error после перезапуска: {interrupted}")
    logger.info(f"[DB] Восстановлено задач: {len(restored)}")
    return restored


async def db_upsert_translation(
    task_id: str, src_lang: str, tgt_lang: str, translated: str
) -> None:
    await _run(_sync_upsert_translation, task_id, src_lang, tgt_lang, translated)


async def db_get_translation(task_id: str, tgt_lang: str) -> Optional[dict]:
    return await _run(_sync_get_translation, task_id, tgt_lang)


async def db_get_all_translations(task_id: str) -> list:
    return await _run(_sync_get_all_translations, task_id)


def _sync_user_statistics(chat_id: str) -> dict:
    with _connect() as conn:
        summary = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
                      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                      SUM(CASE WHEN status IN ('pending', 'processing') THEN 1 ELSE 0 END) AS active
                 FROM tasks WHERE chat_id = ?""",
            (chat_id,),
        ).fetchone()
        translations = conn.execute(
            """SELECT COUNT(*) AS total FROM translations tr
                 JOIN tasks t ON t.task_id = tr.task_id WHERE t.chat_id = ?""",
            (chat_id,),
        ).fetchone()["total"]
        languages = conn.execute(
            """SELECT tr.tgt_lang AS language, COUNT(*) AS total FROM translations tr
                 JOIN tasks t ON t.task_id = tr.task_id WHERE t.chat_id = ?
                 GROUP BY tr.tgt_lang ORDER BY total DESC, language LIMIT 5""",
            (chat_id,),
        ).fetchall()
        recent = conn.execute(
            """SELECT task_id, status, created_at, updated_at FROM tasks
                 WHERE chat_id = ? ORDER BY created_at DESC LIMIT 10""",
            (chat_id,),
        ).fetchall()
    statistics = dict(summary)
    statistics["done"] = statistics["done"] or 0
    statistics["errors"] = statistics["errors"] or 0
    statistics["active"] = statistics["active"] or 0
    statistics["translations"] = translations
    statistics["languages"] = [dict(row) for row in languages]
    statistics["recent"] = [dict(row) for row in recent]
    return statistics


async def db_get_user_statistics(chat_id: str) -> dict:
    return await _run(_sync_user_statistics, chat_id)


# PostgreSQL is selected explicitly for Docker Compose and Kubernetes.  Keep
# SQLite as a no-configuration fallback for one-process local development.
if __import__("config").settings.database_url:
    from database_postgres import (  # noqa: F401
        db_all_tasks,
        db_clear_stuck_tasks,
        db_delete_task,
        db_get_all_settings,
        db_get_all_translations,
        db_get_model_status,
        db_get_task,
        db_get_translation,
        db_get_user_statistics,
        db_insert_task,
        db_restore_task_store,
        db_update_task,
        db_upsert_model_status,
        db_upsert_settings,
        db_upsert_translation,
        init_db,
    )
else:
    # SQLite fallback: db_delete_task and db_clear_stuck_tasks
    import sqlite3 as _sqlite3

    def _sync_delete_task(task_id: str) -> None:
        with _sqlite3.connect(str(__import__("config").settings.data_dir / "tasks.db")) as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            conn.commit()

    def _sync_clear_stuck_tasks() -> int:
        with _sqlite3.connect(str(__import__("config").settings.data_dir / "tasks.db")) as conn:
            cur = conn.execute(
                "UPDATE tasks SET status = 'error', result = 'Cleared by admin' "
                "WHERE status IN ('pending', 'processing')",
            )
            conn.commit()
            return cur.rowcount

    async def db_delete_task(task_id: str) -> None:
        await _run(_sync_delete_task, task_id)

    async def db_clear_stuck_tasks() -> int:
        return await _run(_sync_clear_stuck_tasks)
