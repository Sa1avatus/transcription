import os
import asyncio
import traceback

import httpx
from config import logger, settings


# =============================================================================
# ЛОКАЛЬНАЯ ТРАНСКРИБАЦИЯ (Whisper + LLM-коррекция)
# =============================================================================

def run_whisper_sync(audio_path: str) -> str:
    """
    Транскрибирует файл через Whisper, затем прогоняет результат
    через Qwen для исправления ASR-ошибок.
    """
    try:
        import llm
        import whisper_init
        logger.info(f"Whisper: начало транскрибации {audio_path}")
        segments, _ = whisper_init.whisper_model.transcribe(
            audio_path,
            beam_size=5,
            #laguage="vi",
            condition_on_previous_text=False,
            #vad_filter=True,
        )
        raw = "\n".join(
            f"[{s.start:.2f}s -> {s.end:.2f}s] {s.text.strip()}" for s in segments
        )
        logger.info("Whisper: транскрибация завершена, запускаем LLM-коррекцию")
        corrected = llm.correct_transcript(raw)
        return corrected
    except Exception:
        err = traceback.format_exc()
        logger.error(f"Whisper: ошибка транскрибации\n{err}")
        return f"Whisper Error: {err}"


# =============================================================================
# ВНЕШНИЙ API (Upload → Task → Polling → Result)
# =============================================================================

async def process_external_api(audio_path: str, uid: str) -> str:
    """Загружает файл во внешний сервис, ожидает результат и возвращает текст с таймкодами."""
    if not settings.third_party_url or not settings.third_party_api_key:
        return "External API is not configured."
    headers = {"aik-api-key": settings.third_party_api_key}
    base_url = settings.third_party_url.rstrip('/')

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            logger.info(f"[{uid}] Ext: Uploading file...")
            with open(audio_path, 'rb') as audio_file:
                upload_response = await client.post(
                    f"{base_url}/upload",
                    files={'file': (os.path.basename(audio_path), audio_file)},
                    headers=headers,
                )
                upload_response.raise_for_status()
                file_link = upload_response.json().get("link")

            logger.info(f"[{uid}] Ext: Creating task for {file_link}")
            task_response = await client.post(
                f"{base_url}/task",
                json={"type": "transcribe", "data": {"url": file_link}},
                headers=headers,
            )
            task_response.raise_for_status()
            task_id = task_response.json()["data"]["id"]

            for poll_attempt in range(60):
                await asyncio.sleep(10)
                status_response = await client.get(f"{base_url}/task/{task_id}", headers=headers)
                logger.info(f"[{uid}] Ext Poll #{poll_attempt + 1} [{status_response.status_code}]: {status_response.text}")

                task_status = status_response.json().get("data", {})
                if task_status.get("status") == "complete":
                    segments = task_status.get("result", {}).get("transcribe", [])
                    transcript = "\n".join(
                        f"[{segment.get('start', 0):.2f}s -> {segment.get('end', 0):.2f}s] "
                        f"{segment.get('speaker')}: {segment.get('text', '').strip()}"
                        for segment in segments
                    )
                    return transcript or "External: Success, but no text detected."

                if task_status.get("status") == "fail":
                    return f"Ext Error: {task_status.get('result', {}).get('message')}"

            return "External Error: Timeout (polling limit reached)"

        except httpx.ConnectError:
            logger.error(f"[{uid}] Ext: Connection failed to {base_url}")
            return "External API Error: Connection failed. Check network/DNS."
        except Exception as e:
            logger.error(f"[{uid}] Ext: Exception: {e}")
            return f"External API Exception: {e}"
