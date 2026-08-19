# Transcription service instructions (v1.3)

## Purpose and boundaries

This project provides a Quart-compatible transcription API with separate worker and Telegram processes. Redis transports jobs, PostgreSQL is the authoritative shared task store in distributed deployments, and local ML models are mounted into workers rather than built into API images.

## Required context

- Read `docs/architecture.md` before changing queues, persistence, deployment, or model loading.
- Use `docs/commands.md` for the correct local, Docker, GPU, and verification commands.
- Read `k8s/README.md` before Kubernetes changes.
- Follow the workspace naming, security, testing, and review guidance.
- For delegated implementation, follow the workspace Local Code Worker workflow exactly: run from `D:\OpenAIProjects\local-code-worker`, use the provider and model from its `.env` without command-line overrides, and require Codex approval plus the Worker's interactive `y/N` confirmation.

## Project rules

- Keep heavy ML imports and model initialization out of the API and bot startup paths.
- Keep API, worker, and bot deployable as separate processes.
- PostgreSQL is authoritative for multi-process/production state; SQLite and the in-memory queue are development fallbacks only.
- Settings are persisted in PostgreSQL (`settings` table) and restored on startup. Never rely on container-local `.env` writes for durable configuration.
- Model status (loaded, device, compute_type, cuda_devices) is stored in PostgreSQL (`model_status` table) by the worker and read by the API for UI display.
- Do not bake models, audio data, `.env`, or secrets into images or Git.
- Preserve server-side Telegram `initData` signature validation; never trust a browser-supplied user ID.
- Keep TLS verification enabled by default. A self-signed internal exception must be explicit and scoped.
- The Telegram long-polling bot remains a single active replica per token.

## Architecture (v1.3)

- **API container** (`Dockerfile`): Quart app, serves web UI (dashboard, models, settings), REST endpoints. Does NOT load ML models.
- **Worker container** (`Dockerfile.worker`): CUDA-enabled (nvidia/cuda base), loads Whisper, optionally NLLB/Qwen. Reports model status to PostgreSQL.
- **Bot container** (`Dockerfile.bot`): Telegram bot via aiogram, long-polling. Routes through local Telegram Bot API server for large files.
- **Telegram Bot API server** (`telegram-bot-api`): Self-hosted `aiogram/telegram-bot-api` with `--local` flag. Enables file downloads >20 MB.
- **PostgreSQL**: Tasks, translations, settings, model_status. Single source of truth.
- **Redis**: Job queue between API/bot and worker.

## Web UI

- `/` — Dashboard with drag-and-drop file upload and task status polling
- `/models` — Whisper model management (download, switch, status)
- `/settings` — Settings page (API keys, translations, integrations). Third Party Provider URL is in the API Keys section alongside its API key.

## Docker deployment

```bash
# Basic (CPU)
docker compose up -d --build

# With Telegram bot and local Bot API server
docker compose --profile telegram up -d --build

# Requires TELEGRAM_API_ID and TELEGRAM_API_HASH in .env for >20MB file support
```

GPU access is automatic — `Dockerfile.worker` uses nvidia/cuda base image, `deploy.resources.reservations.devices` requests GPU in docker-compose.yml.

## Definition of done

- `python -m compileall .` and `pytest -q` pass.
- Changes to distributed flows are checked across API, queue, worker, database, and callback paths.
- Deployment changes preserve secret separation, shared storage requirements, and GPU/resource assumptions.
