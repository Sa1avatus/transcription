# Commands

Run commands from `transcription/` in PowerShell.

## Lightweight API-only setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

API-only mode does not process queued transcription work without a worker.

## Local embedded worker

```powershell
pip install -r requirements-worker.txt
python app.py
```

Optional local translation or correction dependencies are split across `requirements-nllb.txt` and `requirements-qwen.txt`.

## Verification

```powershell
pip install -r requirements-dev.txt
python -m compileall .
pytest -q
```

## Docker Compose

```powershell
docker compose up --build
docker compose --profile telegram up --build
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

For repeat GPU builds, build only the worker and preserve Docker cache:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build worker
```

Do not add `--no-cache` routinely; ML dependency layers are large. Do not commit `.env`, `data/`, or `models/`.

