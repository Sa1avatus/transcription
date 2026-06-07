import asyncio
import os
import uuid
import base64
import traceback
from typing import Optional

from quart import Quart, request, jsonify

from config import TMP_DIR, logger
from storage import task_store, task_queue, store_put
from database import (
    db_insert_task, db_get_task, db_all_tasks,
    db_get_translation, db_upsert_translation, db_get_all_translations,
)
from translation import LANGUAGES, run_translate_sync

app = Quart(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.json.ensure_ascii = False


# =============================================================================
# ТРАНСКРИБАЦИЯ
# =============================================================================

@app.route('/transcrib/', methods=['POST'])
async def handle_transcription():
    """
    Принимает аудиофайл или UID. Возвращает task_id для polling.

    Варианты:
      1. multipart/form-data с полем 'file'
      2. JSON с полем 'base64_data'
      3. JSON с полем 'uid' / 'UID'  — режим OMNINET
    """
    try:
        data = await request.get_json(silent=True) or {}
        form = await request.form
        files = await request.files

        uid: Optional[str] = (
            data.get('uid') or data.get('UID')
            or form.get('uid') or form.get('UID')
            or None
        )

        audio_path: Optional[str] = None
        is_temp = False

        if 'file' in files:
            ext = os.path.splitext(files['file'].filename)[1] or ".tmp"
            audio_path = os.path.join(TMP_DIR, f"upload_{uuid.uuid4().hex}{ext}")
            await files['file'].save(audio_path)
            is_temp = True
            logger.info(f"File upload received: {audio_path}, uid={uid}")

        elif 'base64_data' in data:
            audio_path = os.path.join(TMP_DIR, f"b64_{uuid.uuid4().hex}.tmp")
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(data['base64_data'].split(',')[-1]))
            is_temp = True
            logger.info(f"Base64 upload received: {audio_path}, uid={uid}")

        elif uid:
            logger.info(f"UID-only request received: uid={uid}")

        else:
            return jsonify({"error": "Provide 'file', 'base64_data', or 'uid'"}), 400

        task_id = uuid.uuid4().hex
        poll_url = f"/task/{task_id}"

        await db_insert_task(task_id, uid=uid, poll_url=poll_url, chat_id=None)
        store_put(task_id, uid=uid, chat_id=None)
        await task_queue.put((task_id, uid, audio_path, is_temp))

        return jsonify({
            "status": "accepted",
            "task_id": task_id,
            "poll_url": poll_url,
            **({"uid": uid} if uid else {}),
        }), 202

    except Exception as e:
        logger.error(f"handle_transcription error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route('/task/<task_id>', methods=['GET'])
async def get_task_result(task_id: str):
    """Статус и результат задачи. Сначала кэш, потом БД."""
    entry = task_store.get(task_id) or await db_get_task(task_id)
    if entry is None:
        return jsonify({"error": "Task not found"}), 404

    response = {"task_id": task_id, "status": entry["status"]}
    if entry.get("uid"):
        response["uid"] = entry["uid"]
    if entry.get("chat_id"):
        response["chat_id"] = entry["chat_id"]
    if entry.get("result") is not None:
        response["result"] = entry["result"]

    # Список уже готовых переводов для этой задачи
    translations = await db_get_all_translations(task_id)
    if translations:
        response["translations"] = [
            {
                "lang": t["tgt_lang"],
                "label": LANGUAGES[t["tgt_lang"]].label if t["tgt_lang"] in LANGUAGES else t["tgt_lang"],
                "url": f"/translated/{task_id}/{t['tgt_lang']}",
                "created_at": t["created_at"],
            }
            for t in translations
        ]

    return jsonify(response), 200


@app.route('/task/', methods=['GET'])
async def list_tasks():
    """Полная история задач из БД."""
    rows = await db_all_tasks()
    return jsonify({"tasks": rows, "total": len(rows)}), 200


# =============================================================================
# ПЕРЕВОД
# =============================================================================

@app.route('/translated/<task_id>/<tgt_lang>', methods=['GET'])
async def get_translation(task_id: str, tgt_lang: str):
    """
    Возвращает перевод транскрибации на указанный язык.

    Если перевод уже есть в БД — отдаёт мгновенно.
    Если нет — запускает перевод синхронно и сохраняет результат.

    Коды языков: eng_Latn, rus_Cyrl, vie_Latn, zho_Hans, fra_Latn,
                 deu_Latn, kor_Hang, jpn_Jpan
    """
    if tgt_lang not in LANGUAGES:
        return jsonify({
            "error": f"Unknown language code '{tgt_lang}'",
            "available": list(LANGUAGES.keys()),
        }), 400

    # Проверяем кэш (можно сбросить через ?force=true)
    force = request.args.get("force", "").lower() == "true"
    cached = await db_get_translation(task_id, tgt_lang)
    if cached and not force:
        return jsonify({
            "task_id": task_id,
            "tgt_lang": tgt_lang,
            "src_lang": cached["src_lang"],
            "translated": cached["translated"],
            "cached": True,
            "created_at": cached["created_at"],
        }), 200

    # Достаём транскрибацию
    entry = task_store.get(task_id) or await db_get_task(task_id)
    if entry is None:
        return jsonify({"error": "Task not found"}), 404

    if entry["status"] != "done":
        return jsonify({
            "error": f"Transcription not ready (status: {entry['status']})",
            "task_id": task_id,
        }), 409

    source_text: str = entry.get("result") or ""
    if not source_text:
        return jsonify({"error": "Transcription result is empty"}), 422

    src_lang = "vie_Latn"   # приложение транскрибирует вьетнамский

    # Запускаем перевод в executor
    loop = asyncio.get_running_loop()
    translated = await loop.run_in_executor(
        None, run_translate_sync, source_text, src_lang, tgt_lang
    )

    await db_upsert_translation(task_id, src_lang, tgt_lang, translated)

    return jsonify({
        "task_id": task_id,
        "tgt_lang": tgt_lang,
        "src_lang": src_lang,
        "translated": translated,
        "cached": False,
    }), 200


@app.route('/translated/<task_id>', methods=['GET'])
async def list_translations(task_id: str):
    """Список всех переводов для задачи со ссылками."""
    entry = task_store.get(task_id) or await db_get_task(task_id)
    if entry is None:
        return jsonify({"error": "Task not found"}), 404

    rows = await db_get_all_translations(task_id)
    return jsonify({
        "task_id": task_id,
        "translations": [
            {
                "tgt_lang": r["tgt_lang"],
                "src_lang": r["src_lang"],
                "label": LANGUAGES[r["tgt_lang"]].label if r["tgt_lang"] in LANGUAGES else r["tgt_lang"],
                "url": f"/translated/{task_id}/{r['tgt_lang']}",
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "available_languages": {
            code: {"label": info.label, "flag": info.flag, "url": f"/translated/{task_id}/{code}"}
            for code, info in LANGUAGES.items()
        },
    }), 200
