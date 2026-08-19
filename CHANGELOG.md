# Changelog

## v1.3 (2026-08-19)

### New features
- **Web file upload**: drag-and-drop zone on Dashboard with upload progress bar and auto-polling of transcription results.
- **Settings persistence in PostgreSQL**: settings survive container rebuilds. `settings` table created automatically on startup. Settings are also written to `.env` as fallback for local development.
- **Model status synchronization**: worker reports Whisper model status (device, compute_type, loaded, CUDA devices, load time) to PostgreSQL `model_status` table. API reads from DB for UI display.
- **GPU support**: `Dockerfile.worker` uses `nvidia/cuda:12.6.2-cudnn-runtime-ubuntu22.04` base image. GPU reservation added to `docker-compose.yml` via `deploy.resources.reservations.devices`. Whisper automatically uses CUDA/float16 when GPU is available.
- **Local Telegram Bot API server**: `telegram-bot-api` service in docker-compose (profile: telegram) for downloading files >20 MB. Requires `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env`.
- **Telegram bot large file handling**: bot tries to download files regardless of size. On failure, suggests web upload as alternative.

### Changes
- Settings UI: Third Party Provider URL moved into API Keys section alongside its API key.
- `/api/models` and `/api/models/info` read model status from PostgreSQL (worker-reported) instead of local whisper_init state.
- `update_settings()` persists to PostgreSQL via `db_upsert_settings()`. Falls back to `.env` file for local development.
- `get_settings_dict()` converts Path objects to strings and skips `data_dir`/`models_dir`.
- Bot error handling improved with nested try/except to guarantee response even if error message delivery fails.

### Bug fixes
- Fixed `PosixPath is not JSON serializable` error when calling `/api/settings`.
- Fixed bot container crash due to `DATABASE_URL` password mismatch with PostgreSQL.
- Fixed settings lost after Docker container rebuild.

## v1.2 (2026-08-15)

- Web UI for Whisper model management (`/models`).
- Settings page (`/settings`) for API keys and integrations.
- `huggingface_hub` for model downloads (no ctranslate2 needed in API container).
- Separate `models-data` Docker volume.
- `MODELS_DIRECTORY` environment variable.

## v1.1 (2026-08-10)

- Initial Docker Compose setup with API, worker, Redis, PostgreSQL.
- Telegram bot with audio transcription.
- Gemini and DeepL translation backends.
- Omnitracker integration.
- Telegram Mini App for personal statistics.

## v1.0 (2026-08-01)

- Initial release: Quart API, Whisper transcription, Redis queue.
