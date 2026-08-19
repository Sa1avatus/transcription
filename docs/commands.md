# Commands

Run commands from `transcription/` in PowerShell or bash.

## Lightweight API-only setup

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env      # fill in your secrets
python app.py
```

API-only mode does not process queued transcription work without a worker.

## Local embedded worker

```bash
pip install -r requirements-worker.txt
python app.py
```

Optional local translation or correction dependencies are split across `requirements-nllb.txt` and `requirements-qwen.txt`.

## Verification

```bash
pip install -r requirements-dev.txt
python -m compileall .
pytest -q
```

## Docker Compose

```bash
# CPU mode: API + worker + Redis + PostgreSQL
docker compose up -d --build

# With Telegram bot (+ local Bot API server for >20MB files)
docker compose --profile telegram up -d --build

# GPU mode (requires nvidia-container-toolkit)
docker compose up -d --build worker
```

GPU is automatic: `Dockerfile.worker` uses `nvidia/cuda:12.6.2-cudnn-runtime-ubuntu22.04` base and `deploy.resources.reservations.devices` requests the GPU.

### Telegram Bot API local server

For files >20 MB via Telegram, the compose includes a self-hosted `aiogram/telegram-bot-api` service. Requires in `.env`:

```dotenv
TELEGRAM_API_ID=your_app_id
TELEGRAM_API_HASH=your_app_hash
```

Get these from https://my.telegram.org/apps.

### Model management

```bash
# Download a model via API
curl -X POST http://localhost:5000/api/models/download \
  -H 'Content-Type: application/json' \
  -d '{"model_size": "large-v3"}'

# Switch model
curl -X POST http://localhost:5000/api/models/switch \
  -H 'Content-Type: application/json' \
  -d '{"model_size": "large-v3"}'

# Check model status
curl http://localhost:5000/api/models
```

Or use the web UI at http://localhost:5000/models.

### Settings management

```bash
# Get current settings (sensitive fields masked)
curl http://localhost:5000/api/settings

# Update settings (persisted to PostgreSQL)
curl -X POST http://localhost:5000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"translation_backend": "gemini", "gemini_model": "gemini-2.5-flash"}'
```

Or use the web UI at http://localhost:5000/settings.

Do not add `--no-cache` routinely; ML dependency layers are large. Do not commit `.env`, `data/`, or `models/`.
