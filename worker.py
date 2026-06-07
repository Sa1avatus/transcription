import os
import asyncio

from config import logger
from storage import task_store, task_queue, store_update
from database import db_update_task
from omninet import fetch_omninet_audio, send_soap_callback
from transcription import run_whisper_sync, process_external_api


async def _set_status(task_id: str, status: str, result: str | None = None) -> None:
    """Атомарно обновляет статус в кэше и в БД."""
    store_update(task_id, status, result)
    await db_update_task(task_id, status, result)


async def _notify(task_id: str, uid: str | None, chat_id: str | None, text: str) -> None:
    """Отправляет результат в нужный канал: OMNINET и/или Telegram."""
    if uid:
        await send_soap_callback(uid, text)

    if chat_id:
        # Импорт здесь, чтобы избежать циклической зависимости bot ↔ worker
        from bot import send_transcription_result
        await send_transcription_result(chat_id, task_id, text)


async def worker() -> None:
    """Бесконечный воркер: забирает задачи из очереди и обрабатывает их."""
    while True:
        task = await task_queue.get()
        task_id, uid, initial_audio_path, is_temp = task

        entry = task_store.get(task_id, {})
        chat_id: str | None = entry.get("chat_id")

        await _set_status(task_id, "processing")

        try:
            if not initial_audio_path:
                logger.info(f"[{task_id}] UID={uid} — fetching from OMNINET...")
                current_files = await fetch_omninet_audio(uid)
            else:
                current_files = [initial_audio_path]

            if not current_files:
                error_msg = "Ошибка: аудиофайлы не найдены."
                logger.error(f"[{task_id}] No audio files found.")
                await _set_status(task_id, "error", error_msg)
                await _notify(task_id, uid, chat_id, error_msg)
                continue

            all_results = []

            for audio_path in current_files:
                logger.info(f"[{task_id}] Processing: {audio_path}")

                loop = asyncio.get_running_loop()
                l_res, e_res = await asyncio.gather(
                    loop.run_in_executor(None, run_whisper_sync, audio_path),
                    process_external_api(audio_path, task_id),
                )
                all_results.append(l_res)
                #all_results.append(f"=== EXTERNAL ===\n{e_res}\n\n=== LOCAL ===\n{l_res}")

                if (is_temp or not initial_audio_path) and os.path.exists(audio_path):
                    os.remove(audio_path)

            final_text = "\n\n".join(all_results)

            await _set_status(task_id, "done", final_text)
            logger.info(f"[{task_id}] Done. Notifying...")

            await _notify(task_id, uid, chat_id, final_text)

        except Exception as e:
            logger.error(f"Worker Error [{task_id}]: {e}")
            await _set_status(task_id, "error", str(e))
            await _notify(task_id, uid, chat_id, f"❌ Ошибка транскрибации: {e}")
        finally:
            task_queue.task_done()
