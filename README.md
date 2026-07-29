# Transcription Service

Асинхронный сервис транскрибации и перевода. Поток обработки: **upload → Redis queue → ML worker → SQLite task store → polling**. Есть HTTP API, дашборд и опциональный Telegram-адаптер.

## Архитектура

```text
Browser / client ──> API + dashboard ──> Redis ──> ML worker
                         │                              │
                         └──────── SQLite ──────────────┘
                                        │
                              optional Telegram bot
```

Контейнеры разделены по профилю нагрузки, а не ради «микросервисов»:

| Контейнер | Роль | Зависимости |
| --- | --- | --- |
| `api` | HTTP API, dashboard, создание задач и polling | лёгкий Python web stack |
| `worker` | Whisper, Qwen, NLLB и вызовы внешней транскрибации | ML/GPU stack |
| `bot` | Telegram-интерфейс | aiogram; запускается отдельным profile |
| `redis` | межпроцессная очередь задач | Redis с persistence |

API не импортирует локальные ML-библиотеки: тяжёлые зависимости и модели находятся только в `worker`. Это уменьшает размер и время запуска публичного сервиса.

## Docker Compose

1. Подготовьте окружение:

```powershell
cd transcription
Copy-Item .env.example .env
```

2. Если нужен локальный Whisper/NLLB/Qwen, положите модели в каталог из `MODEL_DIRECTORY` (по умолчанию `./models`). Docker подключит его в worker как `/app/data/models` в режиме read-only. Укажите реальные ключи интеграций в `.env`.

3. Запустите API, worker и Redis:

```powershell
docker compose up --build
```

Дашборд будет доступен по `http://localhost:5000/`, health-check — по `/health`.

Telegram-адаптер не стартует по умолчанию:

```powershell
docker compose --profile telegram up --build
```

Для NVIDIA GPU (после установки NVIDIA Container Toolkit) добавьте override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Текущий GPU override рассчитан на RTX 3050 с 4 ГБ VRAM: он включает CUDA-вариант PyTorch и NLLB-600M, выбирает Whisper `small` и отключает Qwen-коррекцию. Это важно: одновременная загрузка NLLB-1.3B, Whisper `medium` и Qwen в 4 ГБ видеопамяти ненадёжна и обычно приводит к `CUDA out of memory`.

### Повторная GPU-сборка

Docker автоматически использует кэш успешно завершённых слоёв. Собирайте только worker и **не** добавляйте `--no-cache`:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build worker
```

GPU Dockerfile разделяет установку зависимостей на отдельные слои и сохраняет pip-кэш BuildKit. Если скачивание PyTorch оборвётся, повторите ту же команду: завершённые этапы будут взяты из кэша, а wheel-файлы не потребуется получать заново. Docker не может продолжить ровно с середины одной упавшей команды, но такая структура минимизирует повторную работу.

Если в логе есть `ReadTimeoutError` при `files.pythonhosted.org`, это временный сетевой таймаут PyPI. В Dockerfile заданы 10 повторов и 180-секундный timeout; просто повторите ту же команду сборки. Не запускайте одновременно `up --build` для всех сервисов — конкурирующие скачивания делают нестабильную сеть ещё хуже.

`Dockerfile` создаёт лёгкий API-образ. `Dockerfile.worker` — CPU worker, `Dockerfile.worker.gpu` — NVIDIA CUDA/cuDNN worker для локального NLLB и Whisper, а `Dockerfile.bot` содержит зависимости Telegram. Модели подключаются volume-ом и не попадают в образ или Git.

Файл `.dockerignore` исключает `models/`, `data/`, `.env`, виртуальные окружения и тестовые артефакты из Docker build context. Это принципиально: локальные модели могут занимать десятки гигабайт, но worker получает их только через read-only volume `MODEL_DIRECTORY:/app/data/models`.

Локальный NLLB — отдельная опция. При `TRANSLATION_BACKEND=gemini` или `deepl` оставьте `INSTALL_NLLB=false`: PyTorch и Transformers не будут попадать в CPU worker-образ. GPU override автоматически выбирает `Dockerfile.worker.gpu`, устанавливает CUDA-вариант PyTorch и включает NLLB-600M. Для CPU NLLB установите `INSTALL_NLLB=true`; worker добавит CPU-вариант PyTorch.

Qwen-коррекция также отделена от базового worker: для CPU-профиля включайте одновременно `ENABLE_QWEN_CORRECTION=true` и `INSTALL_QWEN=true`. В GPU-профиле RTX 3050 4 GB оставляйте оба значения `false`.

### Redis URL

При запуске через Compose оставьте `REDIS_URL` пустым: Compose сам передаёт контейнерам `redis://redis:6379/0`, где `redis` — имя сервиса во внутренней Docker-сети.

Для локального запуска с внешним Redis укажите `REDIS_URL=redis://localhost:6379/0`. Если переменная пуста при монолитном запуске, используется встроенная `asyncio.Queue` — это удобно для разработки, но не позволяет API и worker работать в разных процессах.

> SQLite в этой конфигурации подходит для демонстрации и одного worker. Включены WAL и busy timeout для совместного доступа контейнеров. Для нескольких worker или production-нагрузки следующим шагом будет PostgreSQL.

## Локальный монолитный запуск

Для разработки без Docker очистите `REDIS_URL`, оставьте `RUN_EMBEDDED_WORKER=true` и установите ML-зависимости:

```powershell
pip install -r requirements-worker.txt
python app.py
```

Для локального NLLB дополнительно выполните `pip install -r requirements-nllb.txt` и установите подходящий PyTorch для вашей CPU/GPU-среды.

Для API-only локально достаточно `pip install -r requirements.txt`, но без worker задачи не будут обрабатываться.

## API

- `POST /transcrib/` — принять аудиофайл, `base64_data` или OMNINET `uid`.
- `GET /task/<task_id>` — состояние и результат задачи.
- `GET /task/` — история задач для дашборда.
- `GET /translated/<task_id>/<language>` — получить или создать кэшированный перевод.
- `GET /health` — проверка работоспособности.

## Security

Скопируйте `.env.example` в `.env`; этот файл игнорируется Git. TLS verification включена по умолчанию. `INTERNAL_TLS_VERIFY=false` допустим только для контролируемого внутреннего endpoint с self-signed сертификатом. Секреты, которые ранее попадали в код, необходимо отозвать и перевыпустить.

## Verification

```powershell
pip install -r requirements-dev.txt
python -m compileall .
pytest -q
```

## Kubernetes

Kubernetes deployment lives in [`k8s/`](k8s).  It separates the API, GPU
worker, Telegram bot, Redis and PostgreSQL. PostgreSQL is the authoritative
task store, so API replicas and the worker observe the same status.
See [`k8s/README.md`](k8s/README.md) for the storage/GPU requirements and the
important distinction between Docker Desktop kind and a production cluster.

Before deployment:

1. Publish the three images and replace `ghcr.io/your-org/...:TAG` in
   `k8s/application.yaml`.
2. Ensure the cluster has the NVIDIA device plugin and a StorageClass that
   supports `ReadWriteMany`. The `transcription-data` PVC is shared because
   the API/bot download an audio file and the worker reads that same file.
3. Put Whisper and NLLB model directories on the `transcription-models` PVC.
4. Create the real secret outside Git:

```powershell
Copy-Item k8s/secret.example.yaml k8s/secret.yaml
# Replace all REPLACE_* values in k8s/secret.yaml
kubectl apply -f k8s/namespace.yaml
kubectl -n transcription apply -f k8s/secret.yaml
kubectl apply -k k8s/
kubectl -n transcription get pods
```

`secret.example.yaml` is intentionally not included in `kustomization.yaml`:
it contains placeholders and must never be applied as-is. The bot has one
replica because Telegram long polling permits only one active consumer for a
given token. The worker has one GPU by default; scale it only after confirming
that the model volume and available GPUs support the required parallelism.

For Docker Compose, recreate services after this change so they start with
PostgreSQL rather than the legacy SQLite fallback:

```powershell
docker compose --profile telegram -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

## Telegram Mini App: personal statistics

The bot can open a personal statistics page inside Telegram. The page is served
by the existing API at `/miniapp`; it shows a user's transcription count,
translation count, task states, common target languages and recent tasks.

The API trusts neither a browser-supplied Telegram ID nor `initDataUnsafe`.
It validates the signed `Telegram.WebApp.initData` server-side before querying
statistics. This keeps one user's history private from other users.

To enable the **Statistics** menu button, publish the API through a public
HTTPS URL and set it in `.env`:

```dotenv
WEBAPP_URL=https://stats.example.com/miniapp
```

Restart only the bot after changing this value. For local Telegram testing use
a temporary HTTPS tunnel to the restricted `miniapp-gateway` on port 5050;
do not expose the full API port 5000. `http://localhost:5000/miniapp` works in
a desktop browser but Telegram clients require HTTPS. See the official
[Telegram Mini Apps documentation](https://core.telegram.org/bots/webapps).
