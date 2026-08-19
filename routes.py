import asyncio
import os
import uuid
import base64
import traceback
from typing import Optional

from quart import Quart, request, jsonify, render_template

from config import TMP_DIR, logger, settings
from storage import task_store, task_queue, store_put
from database import (
    db_insert_task, db_get_task, db_all_tasks,
    db_get_translation, db_upsert_translation, db_get_all_translations,
    db_get_user_statistics,
)
from translation import LANGUAGES, run_translate_sync
from telegram_webapp import TelegramWebAppAuthError, validate_init_data
import whisper_init

app = Quart(__name__)
app.config['MAX_CONTENT_LENGTH'] = settings.max_upload_mb * 1024 * 1024
app.json.ensure_ascii = False


@app.route('/', methods=['GET'])
async def dashboard():
    """Small operational dashboard for demos and local support."""
    return await render_template('index.html')


@app.route('/miniapp', methods=['GET'])
async def miniapp():
    """Telegram Mini App shell; data is loaded only after signed auth."""
    return await render_template('miniapp.html')


@app.route('/api/miniapp/stats', methods=['POST'])
async def miniapp_statistics():
    """Return personal statistics for a signed Telegram Mini App session."""
    payload = await request.get_json(silent=True) or {}
    init_data = payload.get("init_data") or request.headers.get("X-Telegram-Init-Data", "")
    try:
        launch = validate_init_data(
            init_data,
            settings.telegram_bot_token,
            settings.webapp_auth_max_age_seconds,
        )
    except TelegramWebAppAuthError:
        logger.warning("Rejected Telegram Mini App statistics request")
        return jsonify({"error": "Telegram authorization failed"}), 401

    user = launch["user"]
    statistics = await db_get_user_statistics(str(user["id"]))
    return jsonify({
        "user": {"id": user["id"], "first_name": user.get("first_name", "")},
        "statistics": statistics,
    })


@app.route('/health', methods=['GET'])
async def health():
    return jsonify({"status": "ok", "service": "transcription-service"}), 200


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
        request_payload = await request.get_json(silent=True) or {}
        form = await request.form
        files = await request.files

        uid: Optional[str] = (
            request_payload.get('uid') or request_payload.get('UID')
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

        elif 'base64_data' in request_payload:
            audio_path = os.path.join(TMP_DIR, f"b64_{uuid.uuid4().hex}.tmp")
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(request_payload['base64_data'].split(',')[-1]))
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
    entry = await db_get_task(task_id) or task_store.get(task_id)
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
    entry = await db_get_task(task_id) or task_store.get(task_id)
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
    entry = await db_get_task(task_id) or task_store.get(task_id)
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
            language_code: {"label": language.label, "flag": language.flag, "url": f"/translated/{task_id}/{language_code}"}
            for language_code, language in LANGUAGES.items()
        },
    }), 200


# =============================================================================
# УПРАВЛЕНИЕ МОДЕЛЯМИ WHISPER
# =============================================================================

@app.route('/models', methods=['GET'])
async def models_page():
    """Web UI for managing Whisper models."""
    return await render_template('models.html')


@app.route('/api/models', methods=['GET'])
async def api_list_models():
    """Return list of all available Whisper models with status."""
    info = whisper_init.get_model_info()
    models = whisper_init.list_models()
    return jsonify({"current": info, "models": models}), 200


@app.route('/api/models/switch', methods=['POST'])
async def api_switch_model():
    """Switch the active Whisper model. Accepts JSON {"model_size": "large-v3"}."""
    payload = await request.get_json(silent=True) or {}
    model_size = payload.get("model_size") or payload.get("size")
    if not model_size:
        return jsonify({"error": "Provide 'model_size' in request body"}), 400

    logger.info(f"API: model switch request to '{model_size}'")
    result = await whisper_init.switch_model(model_size)
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route('/api/models/download', methods=['POST'])
async def api_download_model():
    """Download a Whisper model from HuggingFace. Accepts JSON {"model_size": "large-v3"}."""
    payload = await request.get_json(silent=True) or {}
    model_size = payload.get("model_size") or payload.get("size")
    if not model_size:
        return jsonify({"error": "Provide 'model_size' in request body"}), 400

    logger.info(f"API: model download request for '{model_size}'")
    result = await whisper_init.download_model(model_size)
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route('/api/models/info', methods=['GET'])
async def api_model_info():
    """Return current model metadata."""
    return jsonify(whisper_init.get_model_info()), 200


# =============================================================================
# УПРАВЛЕНИЕ НАСТРОЙКАМИ
# =============================================================================

@app.route('/settings', methods=['GET'])
async def settings_page():
    """Web UI for managing application settings."""
    return await render_template('settings.html')


@app.route('/api/settings', methods=['GET'])
async def api_get_settings():
    """Return current settings (sensitive fields masked)."""
    from config import get_settings_dict, EDITABLE_FIELDS
    return jsonify({"settings": get_settings_dict(), "editable": EDITABLE_FIELDS}), 200


@app.route('/api/settings', methods=['POST'])
async def api_update_settings():
    """Update settings. Accepts JSON with key-value pairs."""
    from config import update_settings, EDITABLE_FIELDS
    payload = await request.get_json(silent=True) or {}

    if not payload:
        return jsonify({"error": "Provide settings to update in request body"}), 400

    # Validate that only editable fields are being changed
    invalid = [k for k in payload if k not in EDITABLE_FIELDS]
    if invalid:
        return jsonify({"error": f"Cannot edit fields: {invalid}", "editable": list(EDITABLE_FIELDS.keys())}), 400

    try:
        result = update_settings(payload)
        logger.info(f"API: settings updated: {list(payload.keys())}")
        return jsonify({"ok": True, "settings": result}), 200
    except Exception as e:
        logger.error(f"API: settings update failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400
