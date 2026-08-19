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
