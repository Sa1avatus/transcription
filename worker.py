import os
import asyncio
import httpx

from config import logger, settings
from storage import task_store, task_queue, store_update
from database import db_get_task, db_update_task, db_upsert_translation
from omninet import fetch_omninet_audio, send_soap_callback


async def _set_status(task_id: str, status: str, task_result: str | None = None) -> None:
    """Атомарно обновляет статус в кэше и в БД."""
    store_update(task_id, status, task_result)
    await db_update_task(task_id, status, task_result)


async def _notify(task_id: str, uid: str | None, chat_id: str | None, text: str) -> None:
    """Отправляет результат в нужный канал: OMNINET и/или Telegram."""
    if uid:
        await send_soap_callback(uid, text)

    if chat_id:
        await _send_transcription_result(chat_id, task_id, text)


async def _send_telegram_text(
    chat_id: str, text: str, reply_markup: dict | None = None,
) -> None:
    """Send a worker-produced message without importing aiogram in this image."""
    if not settings.telegram_bot_token:
        logger.warning("Translation complete, but TELEGRAM_BOT_TOKEN is not configured")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    chunks = [text[offset:offset + 4000] for offset in range(0, len(text), 4000)] or [text]
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in chunks:
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup and chunk == chunks[-1]:
                payload["reply_markup"] = reply_markup
            response = await client.post(url, json=payload)
            response.raise_for_status()


async def _send_transcription_result(chat_id: str, task_id: str, text: str) -> None:
    """Deliver a completed transcription from the worker's isolated image."""
    await _send_telegram_text(chat_id, f"📝 Транскрибация готова ({task_id})")
    await _send_telegram_text(chat_id, text)

    # Keep the existing bot callback contract without importing aiogram into
    # the GPU worker image.
    from translation import LANGUAGES

    buttons = [
        {"text": f"{language.flag} {language.label}", "callback_data": f"translate:{task_id}:{language_code}"}
        for language_code, language in LANGUAGES.items()
    ]
    keyboard = {"inline_keyboard": [buttons[offset:offset + 2] for offset in range(0, len(buttons), 2)]}
    await _send_telegram_text(
        chat_id,
        "🌐 Хочешь перевести транскрибацию на другой язык?",
        reply_markup=keyboard,
    )


async def _process_translation_job(job: dict) -> None:
    """Translate in the GPU worker and return the result through Telegram."""
    from translation import run_translate_sync

    source_text = job["source_text"]
    src_lang = job.get("src_lang", "vie_Latn")
    tgt_lang = job["tgt_lang"]
    chat_id = job["chat_id"]
    task_id = job.get("task_id")

    loop = asyncio.get_running_loop()
    translated = await loop.run_in_executor(None, run_translate_sync, source_text, src_lang, tgt_lang)

    if task_id:
        await db_upsert_translation(task_id, src_lang, tgt_lang, translated)

    await _send_telegram_text(chat_id, f"🌐 Перевод ({tgt_lang}):\n\n{translated}")
    logger.info("Translation job complete: task=%s, target=%s", task_id or "plain", tgt_lang)


async def worker() -> None:
    """Бесконечный воркер: забирает задачи из очереди и обрабатывает их."""
    while True:
        # Importing here keeps the API and Telegram images free of local ML libraries.
        from transcription import process_external_api, run_whisper_sync
        job = await task_queue.get()

        if isinstance(job, dict) and job.get("kind") == "translation":
            try:
                await _process_translation_job(job)
            except Exception:
                logger.exception("Translation job failed")
                await _send_telegram_text(
                    job["chat_id"],
                    "❌ Не удалось выполнить перевод. Попробуйте ещё раз немного позже.",
                )
            finally:
                task_queue.task_done()
            continue

        task_id, uid, initial_audio_path, is_temp = job

        # API, bot and worker are isolated processes.  The worker's local
        # cache does not contain a task created by the bot, so PostgreSQL is
        # the authoritative source for the Telegram chat destination.
        entry = task_store.get(task_id) or await db_get_task(task_id) or {}
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

        except Exception as exc:
            logger.exception("Worker error [%s]", task_id)
            await _set_status(task_id, "error", str(exc))
            await _notify(
                task_id,
                uid,
                chat_id,
                "❌ Не удалось выполнить транскрибацию. Попробуйте ещё раз немного позже.",
            )
        finally:
            task_queue.task_done()
