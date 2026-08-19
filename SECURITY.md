# Security

## Secrets

- Never commit `.env`, `models/`, `data/`, virtual environments, or Kubernetes secrets.
- API keys are masked in `/api/settings` responses (displayed as `***`).
- Settings are stored in PostgreSQL; `.env` is used as fallback for local development only.
- `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `DEEPL_API_KEY`, `OMNINET_PASSWORD`, and `THIRD_PARTY_API_KEY` are masked in API responses.

## TLS

- TLS verification is enabled by default for all internal and external endpoints.
- Set `INTERNAL_TLS_VERIFY=false` only for a controlled internal endpoint with a self-signed certificate.

## Telegram

- The Telegram `initData` signature is always validated server-side; a browser-supplied user ID is never trusted.
- One Telegram bot token must be served by exactly one long-polling bot replica.
- The local Telegram Bot API server (`telegram-bot-api`) binds to `127.0.0.1:8081` and is not exposed externally.

## Database

- PostgreSQL is the authoritative store for tasks, translations, settings, and model status.
- The `postgres-data` Docker volume persists across rebuilds; use `docker compose down -v` only when you intend to destroy all data.
- `POSTGRES_PASSWORD` should be changed from the default in production deployments.

## Docker

- Model files and audio data are excluded from Docker images via `.dockerignore`.
- The worker container runs as non-root user `appuser`.
- GPU access is restricted to the worker container only via `deploy.resources.reservations.devices`.
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are required only for the local Bot API server and should be kept secret.
