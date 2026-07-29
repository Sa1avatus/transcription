# Transcription service instructions

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
- Do not bake models, audio data, `.env`, or secrets into images or Git.
- Preserve server-side Telegram `initData` signature validation; never trust a browser-supplied user ID.
- Keep TLS verification enabled by default. A self-signed internal exception must be explicit and scoped.
- The Telegram long-polling bot remains a single active replica per token.

## Definition of done

- `python -m compileall .` and `pytest -q` pass.
- Changes to distributed flows are checked across API, queue, worker, database, and callback paths.
- Deployment changes preserve secret separation, shared storage requirements, and GPU/resource assumptions.
