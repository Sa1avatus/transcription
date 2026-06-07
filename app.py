import asyncio
import uvicorn

import whisper_init
import translation as tl
import llm
from routes import app
from worker import worker
from storage import task_store
from database import init_db, db_restore_task_store
from bot import start_bot


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

    loop = asyncio.get_running_loop()

    if whisper_init.whisper_model is None:
        whisper_init.whisper_model = await loop.run_in_executor(
            None, whisper_init._init_whisper_model
        )

    if tl.nllb_pipeline is None:
        tl.nllb_pipeline = await loop.run_in_executor(None, tl._init_translator)

    if llm.qwen_model is None:
        #None
        llm.qwen_model = await loop.run_in_executor(None, llm._init_qwen)

    app.add_background_task(worker)
    app.add_background_task(start_bot)


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=5000, log_level="info")
