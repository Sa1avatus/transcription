# Architecture

## Distributed data flow

```text
client / Telegram / Omnitracker
            -> API or bot -> PostgreSQL task record
                          -> Redis job queue -> worker
worker -> Whisper (GPU/CPU) -> correction -> persisted result -> callback / Telegram response
worker -> PostgreSQL model_status (synced to API UI)
client -> API -> task status, transcript, and cached translations
settings UI -> API -> PostgreSQL settings (persists across rebuilds)
```

## Components

- `routes.py`: HTTP endpoints, validation, task creation, status, translations, model management, settings.
- `storage.py`: Redis or in-process queue abstraction and local task cache.
- `worker.py`, `worker_main.py`: job execution and model lifecycle.
- `transcription.py`, `whisper_init.py`: external/local transcription boundary, model loading with GPU detection.
- `translation.py`, `llm.py`: translation and optional transcript correction providers.
- `config.py`: application settings with PostgreSQL persistence and `.env` fallback.
- `database_postgres.py`: shared production task, translation, settings, and model_status persistence.
- `database.py`: SQLite development fallback; exports PostgreSQL functions when DATABASE_URL is set.
- `bot.py`, `telegram_webapp.py`: Telegram bot and signed Mini App identity validation.
- `omninet.py`: Omnitracker audio retrieval and SOAP callback integration.
- `templates/`: Web UI (index.html, models.html, settings.html, miniapp.html).

## Deployment topology

| Container | Base image | Purpose |
| --- | --- | ---|
| `api` | python:3.11-slim | Quart web server, dashboard, REST API |
| `worker` | nvidia/cuda:12.6.2-cudnn-runtime | Whisper transcription with GPU, translation |
| `bot` | python:3.11-slim | Telegram bot polling, file reception |
| `telegram-bot-api` | aiogram/telegram-bot-api | Local Bot API server (>20 MB files) |
| `postgres` | postgres:16-alpine | Persistent state |
| `redis` | redis:7-alpine | Job queue |

## Deployment invariants

- API, bot, and worker share task state through PostgreSQL and jobs through Redis.
- Settings and model status are persisted in PostgreSQL and survive container rebuilds.
- Audio artifacts use shared storage when producers and workers run in different containers or pods.
- Model files are mounted read-only into workers via `models-data` volume and excluded from images and Git.
- GPU capacity determines worker model selection; the worker uses `cuda/float16` when GPU is available, `cpu/int8` fallback.
- Telegram Mini App identity is derived only from validated signed `initData`.
- The Telegram bot uses the local Bot API server for file operations; `TELEGRAM_LOCAL_SERVER` is set automatically.
