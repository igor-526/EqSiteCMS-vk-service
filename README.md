# VK Service

Сервис канала доставки VK для EqSiteCMS.

Текущее состояние — **чистый скелет**: рабочего VK-функционала в нём ещё нет.
Сервис поднимает FastAPI-приложение с единственным endpoint `GET /health`,
подключается к NATS JetStream (без активных streams и consumers), запускает
Celery-воркер очереди `vk` и применяет пустую цепочку Alembic-миграций.
Бизнес-логика VK добавляется отдельным change.

## Стек

- Python 3.14.6
- FastAPI
- SQLAlchemy Core + asyncpg
- PostgreSQL 16
- Alembic
- NATS JetStream (клиент без активной топологии)
- Celery + Redis (очередь `vk`, Redis DB 3/4)
- Sentry (опционально)
- Prometheus (production metrics во внутренней сети)

Общая конфигурация Sentry, внутренний Prometheus listener `:9000/metrics`,
sanitization, проверки и rollback описаны в
[`docs/operations/observability.md`](../../docs/operations/observability.md).

## Структура `src/`

```text
src/
├── clients/
│   ├── main_backend/    # HTTP-клиент главного backend (X-Service-Key)
│   └── nats/            # NatsJetstreamClient; consumers/ и handlers/ пусты
├── containers/          # DI-контейнер (NATS + Celery)
├── core/
│   ├── entities/        # Базовые сущности домена
│   ├── exceptions/      # AppError и клиентские ошибки
│   ├── protocols/       # Точки расширения контрактов (пусто)
│   ├── schemas/         # Базовые схемы и messaging base event data
│   └── services/        # Точка расширения use cases (пусто)
├── depends/             # FastAPI Depends-фабрики
├── migration/           # Alembic env + единственная initial-ревизия
├── models/              # Реестр SQLAlchemy Core tables (пусто)
├── repositories/        # Реализации репозиториев (пусто)
├── utils/               # database / basemodel / sentry / observability
├── workers/
│   ├── celery_app.py    # Celery app, очередь `vk`
│   └── tasks/
│       └── integration_probe.py   # vk.integration_probe (пробник брокера)
├── main.py              # FastAPI app, lifespan, обработчики ошибок, GET /health
└── settings.py          # Settings, SentrySettings, NatsSettings, CelerySettings,
                         # MainBackendSettings
```

Пустые пакеты (`models`, `repositories`, `core/services`, `core/protocols`,
`clients/nats/consumers`, `clients/nats/handlers`) сохранены как точки
расширения с пустым `__all__` и без мёртвого кода.

## Запуск

### Локально

```bash
cp .env.example .env      # затем заполнить значения
uv sync
uv run alembic -c src/alembic.ini upgrade head
uv run uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000
```

### В составе монорепозитория

```bash
make vk-build     # сборка образов
make vk           # поднять API, миграции и celery-worker
```

Compose-файл `.docker-compose/docker-compose.vk.yml` и контейнер БД
`eqsitecms-db-vk` принадлежат оркестрации монорепозитория. Порты сервиса
на host не публикуются: API доступен только внутри `eqsitecms_network`.

### Проверки

```bash
make format    # ruff --fix + ruff format
make lint      # mypy, basedpyright, ruff check, ruff format --check, flake8
make test      # pytest без маркера infrastructure
make test-infra # pytest -m infrastructure (требует поднятый стек)
```

## API

| Метод | Путь | Класс доступа | Роли | Без авторизации | С авторизацией |
|---|---|---|---|---|---|
| `GET` | `/health` | Public Read | не проверяются | `200`, `{"status": "ok"}` | `200`, `{"status": "ok"}` |
| `GET` | `:9000/metrics` | Infrastructure-only (не FastAPI-маршрут, только `ENVIRONMENT=production`) | сетевая изоляция вместо ролей | `200` изнутри `eqsitecms_network`; на host порт не публикуется | `200` изнутри `eqsitecms_network` |

Других маршрутов в скелете нет. `POST` / `PATCH` / `DELETE` отсутствуют,
поэтому Protected Write контракт не создаётся. Любой незарегистрированный путь
возвращает `404`. CORS не настраивается, auth-маршруты не регистрируются.

## Celery

| Очередь | Задачи | Описание |
|---|---|---|
| `vk` | `vk.integration_probe` | Детерминированный пробник брокера для infrastructure-тестов |

```bash
celery -A workers.celery_app worker -Q vk -l info --hostname vk-worker@%h
celery -A workers.celery_app inspect ping --destination vk-worker@vk-worker
```

Имена задач следуют формату `vk.<action>`. Redis DB `3` используется как broker,
DB `4` — как backend результатов; номера зафиксированы в
[`agents/redis-databases.yaml`](../../agents/redis-databases.yaml).

## Переменные окружения

Полный перечень с placeholder-значениями — в [`.env.example`](.env.example).

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `ENVIRONMENT` | `development` | Режим работы; `production` включает проверку секретов и metrics listener |
| `DEBUG` | `true` | Debug-режим FastAPI |
| `APP_TITLE` | `VK Service` | Заголовок приложения |
| `SENTRY_ENABLED` | `false` | Включение Sentry |
| `SENTRY_DSN` | — | Обязателен при `SENTRY_ENABLED=true` |
| `SENTRY_ENVIRONMENT` | `development` | Окружение Sentry |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Доля трассировок, `0.0`–`1.0` |
| `SENTRY_RELEASE` | — | Идентификатор релиза |
| `POSTGRES_USER` | `app` | Пользователь БД (в стеке — `eqsitecmsvk`) |
| `POSTGRES_PASSWORD` | `app` | Пароль БД; обязателен и должен быть безопасным в production |
| `POSTGRES_HOST` | `localhost` | Хост БД (в стеке — `eqsitecms-db-vk`) |
| `POSTGRES_PORT` | `5432` | Порт БД |
| `POSTGRES_DB` | `app` | Имя БД (в стеке — `eqsitecmsvk`) |
| `REDIS_PASSWORD` | — | Пароль Redis |
| `CELERY_APP_MAIN` | `vk-service` | Имя Celery-приложения |
| `CELERY_APP_BROKER` | `redis://:...@redis:6379/3` | Redis broker |
| `CELERY_APP_BACKEND` | `redis://:...@redis:6379/4` | Redis backend результатов |
| `NATS_SERVERS` | `nats://localhost:4222` | Список серверов NATS через запятую |
| `NATS_STREAM_SITE_EVENTS` | `SITE_EVENTS` | Имя stream (зарезервировано) |
| `NOTIFICATION_COMMANDS` | `NOTIFICATION_COMMANDS` | Имя stream команд нотификаций |
| `NATS_SUBJECTS_NOTIFICATION_COMMANDS` | `commands.notification.>` | Wildcard subjects stream |
| `NATS_SUBJECT_NOTIFICATION_COMMANDS_SEND_VK` | `commands.notification.vk.send` | Зарезервированный subject VK-канала |
| `NATS_CONSUMER_NOTIFICATION_COMMANDS_SEND_VK` | `vk-service-commands-send-vk` | Зарезервированный durable VK-канала |
| `NATS_CONSUMER_ACK_WAIT_SECONDS` | `30` | Ack wait будущего consumer |
| `NATS_CONSUMER_MAX_DELIVER` | `5` | Максимум доставок |
| `NATS_CONSUMER_FETCH_BATCH_SIZE` | `10` | Размер батча pull-подписки |
| `NATS_CONSUMER_FETCH_TIMEOUT_SECONDS` | `5` | Таймаут fetch |
| `MAIN_BACKEND_URL` | `http://localhost:8000` | Base URL главного backend. В стеке — `http://eqsitecms-app:8000`: `eqsitecms-app` это `container_name` главного backend в `eqsitecms_network`. Значение `http://eqsitecms-backend:8000` недопустимо — контейнера с таким именем не существует. Compose-alias `backend` не используется, потому что он надёжен только внутри compose-проекта backend, а `vk-service` поднимается отдельным проектом `eqsitecms-vk` |
| `MAIN_BACKEND_SERVICE_KEY` | — | Service key для `/api/service/*` главного backend |

### Инфраструктурные переменные запуска

Эти переменные живут **не** в `.env` сервиса, а в gitignored файле
`.docker-compose/.env` монорепозитория и потребляются
`.docker-compose/docker-compose.vk.yml` и `docker-compose.infra.yml`. Значения
воспроизводятся вручную — файл не попадает в репозиторий.

| Переменная | Значение | Назначение |
|---|---|---|
| `EXPOSE_VK_SERVICE_PORT` | `8004` | Зарезервированный host-порт API (`8001`–`8003` заняты). Compose публикует только `expose: 8000` внутри сети, на host порт не выводится |
| `POSTGRES_VK_USER` | `eqsitecmsvk` | Пользователь БД контейнера `eqsitecms-db-vk` |
| `POSTGRES_VK_PASSWORD` | локальное dev-значение | Пароль БД; в репозиторий не попадает |
| `POSTGRES_VK_NAME` | `eqsitecmsvk` | Имя БД |
| `EXPOSE_VK_DB_PORT` | `5436` | Host-порт PostgreSQL (`5433`–`5435` заняты) |

Infrastructure-тест `tests/integration/test_real_celery.py` дополнительно требует
две переменные окружения процесса (не файла):

| Переменная | Пример значения | Назначение |
|---|---|---|
| `VK_TEST_CELERY_BROKER` | `redis://:<redis-password>@127.0.0.1:6379/3` | Redis broker очереди `vk` для `make test-infra` |
| `VK_TEST_CELERY_BACKEND` | `redis://:<redis-password>@127.0.0.1:6379/4` | Redis backend результатов для `make test-infra` |

```bash
VK_TEST_CELERY_BROKER='redis://:<redis-password>@127.0.0.1:6379/3' \
VK_TEST_CELERY_BACKEND='redis://:<redis-password>@127.0.0.1:6379/4' \
make test-infra
```

## NATS JetStream (зарезервировано)

Сервис подключается к NATS под клиентским именем `vk-service`, но **не создаёт
stream и не регистрирует durable consumer**: `setup_streams()` и
`setup_consumers()` — no-op. Владельцами топологии stream
`NOTIFICATION_COMMANDS` остаются `notification-service` и `email-service`;
третий владелец с расходящимся `StreamConfig` был бы источником конфликтов
`add_stream`.

| Роль | Значение | Статус |
|---|---|---|
| Stream | `NOTIFICATION_COMMANDS` | существует, создаётся сервисами-владельцами |
| Wildcard subjects stream | `commands.notification.>` | существует, покрывает VK-subject |
| Subject VK-канала | `commands.notification.vk.send` | **зарезервирован, не используется** |
| Durable VK-канала | `vk-service-commands-send-vk` | **зарезервирован, не создаётся** |

Подписка не активирована, сообщения не потребляются. Файл
`services/vk-service/docs/asyncapi.yaml` намеренно **не создан**, и цель
`make asyncapi-validate` не расширена: канонический AsyncAPI-документ появится
одновременно с реальным consumer и обработчиком отдельным change.

## Границы скелета

В сервисе сознательно **отсутствуют**:

- VK API-клиент, токены сообщества и отправка сообщений;
- подтверждение пользователей через VK и связанные таблицы;
- модели БД и миграции данных — `alembic upgrade head` создаёт только
  служебную `alembic_version`, схема остаётся пустой;
- endpoints рассылки и любые `POST` / `PATCH` / `DELETE` маршруты;
- HTTP-слой `src/api/` — единственный маршрут `GET /health` объявлен в `main.py`
  и router-слой будет восстановлен вместе с первыми VK-endpoints;
- активный NATS consumer и handler VK-команд;
- шаблоны сообщений и бизнес-сервисы.

## ⚠️ Деплой-конфигурация не готова: это конфигурация `email-service`

Каталоги [`.helm/`](.helm) и [`.github/`](.github) скопированы из
**`services/email-service`** побайтово, **без единого изменения** — это прямое
решение владельца продукта на этапе инициализации («helm и секреты не трогай
вообще, скопируй как есть»). Поэтому в них сохранены email-значения:

| Что | Унаследованное значение `email-service` |
|---|---|
| Helm release name | `eqcms-email-service` |
| Docker-образ | `ghcr.io/igor-526/eqsitecms-email-service` |
| Команда worker'а в `.helm/values.yaml` | `-Q email` |
| Ссылка на k8s-секрет | `eqsitecms-email-service-secret` |
| Имена шаблонов | `.helm/templates/email-service-deployment.yml`, `-migration-job.yaml`, `-monitor.yml`, `-service.yml`, `-workers.yaml` |
| GitHub Actions | `.github/workflows/check_and_deploy.yml` собирает и деплоит `email-service` |

**Запрещено выкатывать `vk-service` этой конфигурацией.** Она задеплоит
`email-service`, а не VK-сервис. Ветка `release` для `vk-service` не создаётся,
k8s-секрет `eqsitecms-vk-service-secret` не заведён, remote-репозитория нет.

Приведение `.helm/**` и `.github/**` к VK выполняется **отдельным change**
одновременно с созданием remote-репозитория, записи в `services.manifest` и
секрета `eqsitecms-vk-service-secret` в кластере. Это осознанный технический
долг, зафиксированный также в [`SERVICES.md`](../../SERVICES.md), а не недосмотр.

## Границы репозитория

- `vk-service` не входит в `services.manifest` и не является отдельным
  git-клоном: внутри каталога выполнен локальный `git init` **без remote**.
- Сервис не входит в core release scope монорепозитория (`make build`,
  `make check`, `make test`, `make lint`, `make format`, `migrate-core`,
  `recreate-core`, `health-core`, `status-core`, `logs-core`,
  `asyncapi-validate`, `secret-scan`). Для него существуют автономные цели
  `vk-build`, `vk-build-nc`, `vk`, `vk-attach`, `check-vk`, `fix-vk`.
