# Architecture

## Distributed data flow

```text
client / Telegram / Omnitracker
            -> API or bot -> PostgreSQL task record
                          -> Redis job queue -> worker
worker -> Whisper -> correction -> persisted result -> callback / Telegram response
client -> API -> task status, transcript, and cached translations
```

## Components

- `routes.py`: HTTP endpoints, validation, task creation, status, and translations.
- `storage.py`: Redis or in-process queue abstraction and local task cache.
- `worker.py`, `worker_main.py`: job execution and model lifecycle.
- `transcription.py`, `whisper_init.py`: external/local transcription boundary.
- `translation.py`, `llm.py`: translation and optional transcript correction providers.
- `database_postgres.py`: shared production task and translation persistence.
- `database.py`: SQLite development fallback.
- `bot.py`, `telegram_webapp.py`: Telegram bot and signed Mini App identity validation.
- `omninet.py`: Omnitracker audio retrieval and SOAP callback integration.
- `docker-compose.yml`, `docker-compose.gpu.yml`, `k8s/`: deployment topology.

## Deployment invariants

- API, bot, and worker share task state through PostgreSQL and jobs through Redis.
- Audio artifacts use shared storage when producers and workers run in different containers or pods.
- Model files are mounted read-only into workers and excluded from images and Git.
- GPU capacity determines worker replicas and model selection; the current RTX 3050 profile assumes constrained VRAM.
- Telegram Mini App identity is derived only from validated signed `initData`.

