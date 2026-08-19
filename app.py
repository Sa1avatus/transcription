import asyncio
import uvicorn

from routes import app
from storage import task_store
from database import init_db, db_restore_task_store
from config import logger, settings


@app.before_serving
async def startup():
    """
    Порядок инициализации:
      1. SQLite БД (создание таблиц, миграции)
      2. Восстановление task_store из БД
      3. Модель Whisper
      4. Модель NLLB-200 (перевод)
      5. Модель Qwen2.5 (коррекция + улучшение перевода)
      6. Воркер очереди транскрибации
      7. Telegram бот (polling)
    """
    await init_db()

    # Restore settings from PostgreSQL (survives container rebuilds)
    try:
        from database import db_get_all_settings
        from config import update_settings, settings as _s
        from dataclasses import fields as _fields
        db_settings = await db_get_all_settings()
        if db_settings:
            # Only apply settings that differ from current defaults
            bool_fields = {f.name for f in _fields(_s) if f.type == "bool"}
            int_fields = {f.name for f in _fields(_s) if f.type == "int"}
            to_apply = {}
            for k, v in db_settings.items():
                current = getattr(_s, k, None)
                if current is None:
                    continue
                if k in bool_fields:
                    parsed = v.lower() in ("1", "true", "yes", "on")
                    if parsed != current:
                        to_apply[k] = v
                elif k in int_fields:
                    if int(v) != current:
                        to_apply[k] = v
                elif v != current:
                    to_apply[k] = v
            if to_apply:
                await update_settings(to_apply)
                logger.info(f"[DB] Restored {len(to_apply)} settings from PostgreSQL")
    except Exception as exc:
        logger.warning(f"[DB] Could not restore settings from PostgreSQL: {exc}")

    restored = await db_restore_task_store()
    task_store.update(restored)

    if settings.run_embedded_worker:
        from worker_main import initialise_models
        from worker import worker
        await initialise_models()
        app.add_background_task(worker)
    if settings.enable_telegram_bot:
        if not settings.telegram_bot_token:
            raise RuntimeError("ENABLE_TELEGRAM_BOT requires TELEGRAM_BOT_TOKEN")
        from bot import start_bot
        app.add_background_task(start_bot)
    logger.info("Service startup complete; embedded_worker=%s, telegram=%s", settings.run_embedded_worker, settings.enable_telegram_bot)


if __name__ == "__main__":
    uvicorn.run("app:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower())
