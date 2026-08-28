# VK Service

Сервис канала доставки VK для EqSiteCMS и бот привязки пользователей.

Сервис владеет привязкой пользователя EqSiteCMS к аккаунту VK: выдаёт
контрольную строку, принимает её от пользователя сообщением боту группы,
хранит состояние привязки и журнал действий. Он поднимает FastAPI-приложение
(`GET /health` и приватные маршруты `/vks*`), отдельный long-poll runtime бота,
Celery-воркер очереди `vk` и потребляет команды доставки из NATS JetStream.

## Стек

- Python 3.14.6
- FastAPI
- SQLAlchemy Core + asyncpg
- PostgreSQL 16
- Alembic
- NATS JetStream (durable pull consumer VK-команд)
- Celery + Redis (очередь `vk`, Redis DB 3/4)
- `vkbottle` (асинхронный клиент VK API и Bots Long Poll)
- Sentry (опционально)
- Prometheus (production metrics во внутренней сети)

Общая конфигурация Sentry, внутренний Prometheus listener `:9000/metrics`,
sanitization, проверки и rollback описаны в
[`docs/operations/observability.md`](../../docs/operations/observability.md).

## Структура `src/`

```text
src/
├── api/
│   ├── dependencies.py  # Сборка доменных сервисов из сессии
│   ├── endpoints/       # vks.py — приватные маршруты /vks*
│   └── schemas/         # vk.py — request/response схемы
├── bot/                 # Long-poll runtime: точка входа, обработчики событий
├── clients/
│   ├── main_backend/    # HTTP-клиент главного backend (X-Service-Key)
│   ├── nats/            # NatsJetstreamClient, VK command consumer и handler
│   └── vk/              # Адаптер vkbottle под протоколы домена
├── containers/          # DI-контейнер (NATS + Celery + VK)
├── core/
│   ├── entities/        # Базовые сущности домена
│   ├── exceptions/      # AppError и клиентские ошибки
│   ├── protocols/
│   │   └── vk/          # VkMessengerProtocol, VkUserProfile
│   ├── schemas/         # Базовые схемы и messaging base event data
│   └── services/        # vk_binding, vk_confirmation, vk_state, vk_code
├── depends/             # FastAPI Depends-фабрики
├── migration/           # Alembic env + initial и ревизия VK-домена
├── models/              # user_vks, vk_confirmations, vk_logs
├── repositories/        # Протоколы и SQLAlchemy-реализации VK-домена
├── utils/               # database / basemodel / sentry / observability
├── workers/
│   ├── celery_app.py    # Celery app, очередь `vk`
│   └── tasks/
│       └── integration_probe.py   # vk.integration_probe (пробник брокера)
├── main.py              # FastAPI app, lifespan, обработчики ошибок, роутеры
└── settings.py          # Settings, SentrySettings, NatsSettings, CelerySettings,
                         # MainBackendSettings, VkSettings
```

`vkbottle` импортируется **только** в `clients/vk/**` и `bot/**`: домен
(`core/services`, `repositories`, `models`, `api`) зависит от протоколов
`core/protocols/vk`, поэтому тестируется без сети и без группового токена.

## NATS JetStream

| Stream | Subject | Durable | Назначение | Роль |
|---|---|---|---|---|
| `NOTIFICATION_COMMANDS` | `commands.notification.vk.send` | `vk-service-commands-send-vk` | Callback-уведомление выбранным пользователям | входящий |

VK Service создаёт/актуализирует только собственный durable consumer с explicit ACK/NAK,
но не создаёт stream `NOTIFICATION_COMMANDS`. Consumer запускается только в lifespan
FastAPI-приложения; отдельный bot runtime его не запускает. Успешные доставки фиксируются
в `vk_notification_deliveries`, поэтому redelivery пропускает уже отправленных адресатов.

Проверка схемы: `npx --yes @asyncapi/cli validate docs/asyncapi.yaml`.

## Запуск

### Локально

```bash
cp .env.example .env      # затем заполнить значения
uv sync
uv run alembic -c src/alembic.ini upgrade head
uv run uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000
```

Long-poll runtime бота — **отдельный процесс**, HTTP-приложение его не
запускает:

```bash
PYTHONPATH=src uv run python -m bot   # из корня сервиса, не из src/
```

Команду нужно выполнять **из корня сервиса**: `.env` лежит здесь и читается
относительно текущего каталога. При запуске из `src/` файл не найдётся, и
настройки молча возьмут значения по умолчанию (пустой токен, `VK_GROUP_ID=0`).
В контейнере этой проблемы нет: compose передаёт переменные через `env_file`.

Перед стартом цикла runtime выполняет preflight-проверку `groups.getLongPollServer`
и завершается с ненулевым кодом и понятным сообщением, если:

- `VK_GROUP_TOKEN` пуст или содержит placeholder-значение;
- `VK_GROUP_ID` не задан положительным числом;
- у токена не хватает прав либо Long Poll API выключен в настройках сообщества
  (VK error `15`/`100`);
- токен недействителен или отозван (VK error `5`/`27`/`28`).

Временная сетевая ошибка на preflight стартовать не мешает — цикл переподключится
сам. Bots Long Poll допускает **одного** слушателя на группу, поэтому запускать
более одного экземпляра нельзя.

### В составе монорепозитория

```bash
make vk-build       # сборка образов
make vk             # поднять API, миграции, celery-worker и бота
make vk-bot-logs    # логи контейнера eqsitecms-vk-bot
make vk-bot-restart # перезапустить только бота
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
| `GET` | `/vks?user_ids=<uuid,...>&state=<STATE>` | Public Read (приватная сеть) | не проверяются | `200` со списком привязок | то же |
| `GET` | `/vks/bot-info` | Public Read | не проверяются | `200` с публичными атрибутами группы; `503`, если группа не настроена | то же |
| `POST` | `/vks` | Protected Write (приватная сеть) | внутренний вызывающий | `201` для нового владельца, `200` для существующего | то же |
| `POST` | `/vks/issue-confirmation` | Protected Write (приватная сеть) | внутренний вызывающий | `201` с кодом; `409` для `ACTIVE`/`BLOCKED` | то же |
| `DELETE` | `/vks/{user_id}` | Protected Write (приватная сеть) | внутренний вызывающий | `204` (идемпотентно) | то же |

Сервис **приватный**: порт на host не публикуется, browser-facing gateway —
только главный backend, поэтому owner-проверка выполняется им, а не здесь;
peer-service credential не используется. Публичного маршрута подтверждения
(`/vks/confirm`) **нет**: контрольную строку сверяет long-poll runtime по
сообщению из VK.

Соответствие доменных ошибок статусам: `NotFoundError` → `404`,
`ConflictError` / `AlreadyExistsError` → `409`, `GoneError` → `410`,
`RateLimitedError` → `429`, незавершённая VK-конфигурация → `503`. Ошибки
валидации запроса возвращают `400` со списком проблем — это сервисное
соглашение, заданное обработчиком `RequestValidationError` в `main.py`.
Любой незарегистрированный путь возвращает `404`. CORS не настраивается,
auth-маршруты не регистрируются. Контрольная строка и групповой токен не
попадают в тела ответов и ошибок.

## Привязка пользователя VK

### Требования к сообществу VK

Перед первым запуском бота в сообществе нужно:

1. Выдать токен с правами **«Сообщения»** и **«Управление сообществом»**
   (Управление → Работа с API → Ключи доступа). Проверить фактические права можно
   методом `groups.getTokenPermissions`: маска должна включать и `messages`, и
   `manage`.
2. Включить **Long Poll API** (Управление → Работа с API → Long Poll API), выбрав
   актуальную версию.
3. В типах событий Long Poll включить **«Входящее сообщение»**, **«Разрешение на
   сообщения»** и **«Запрет на сообщения»** — без последних двух состояния
   `ACTIVE`/`BLOCKED` не будут обновляться.
4. Разрешить сообщения сообщества (Управление → Сообщения → включить).

Пайплайн подтверждения:

1. Пользователь в CMS открывает «Уведомления» → «Настройки» и нажимает
   «Получить код». CMS вызывает главный backend, тот — `POST
   /vks/issue-confirmation` этого сервиса.
2. Сервис создаёт или переиспользует запись `user_vks` в состоянии `PENDING`,
   помечает все предыдущие неиспользованные коды использованными и выдаёт новую
   контрольную строку с TTL `VK_CONFIRMATION_TTL_MINUTES`.
3. Контрольная строка — 8 символов из алфавита `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`
   (без `I`, `O`, `0`, `1`), генерируется через `secrets`. Пользователь
   отправляет боту группы в **личный диалог** сообщение
   `<VK_BOT_LINK_COMMAND> <код>`, например `/link ABC23XYZ`.
4. Long-poll runtime получает `message_new`, нормализует код (обрезка пробелов,
   верхний регистр), сверяет его и переводит привязку в `ACTIVE`, сохраняя
   `vk_peer_id` и кэш публичного имени. Бот отвечает пользователю в диалоге.
5. `message_deny` переводит привязку в `BLOCKED`, `message_allow` возвращает в
   `ACTIVE`. Отвязка из CMS — soft-delete записи.

Состояния привязки: `PENDING` (код выдан, подтверждения нет), `ACTIVE`
(`vk_peer_id` привязан, группа может писать), `BLOCKED` (пользователь запретил
сообщения от группы). Отвязка отдельным состоянием не является.

Ограничения безопасности: не более `VK_CONFIRMATION_MAX_ATTEMPTS` неуспешных
попыток на один `vk_peer_id` за `VK_CONFIRMATION_ATTEMPT_WINDOW_MINUTES`;
полная контрольная строка не попадает ни в `vk_logs`, ни в логи процесса —
журналируется только маскированное значение.

Таблицы домена: `user_vks` (привязка и состояние, partial unique индексы на
`user_id` и `vk_peer_id` среди неудалённых), `vk_confirmations` (контрольные
строки с TTL и `used_at`), `vk_logs` (журнал действий и событий бота).

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
| `NATS_STREAM_NOTIFICATION_COMMANDS` | `NOTIFICATION_COMMANDS` | Имя stream команд нотификаций |
| `NATS_SUBJECTS_NOTIFICATION_COMMANDS` | `commands.notification.>` | Wildcard subjects stream |
| `NATS_SUBJECT_NOTIFICATION_COMMANDS_SEND_VK` | `commands.notification.vk.send` | Subject VK-канала |
| `NATS_CONSUMER_NOTIFICATION_COMMANDS_SEND_VK` | `vk-service-commands-send-vk` | Durable VK-канала |
| `NATS_CONSUMER_ACK_WAIT_SECONDS` | `30` | Ack wait consumer |
| `NATS_CONSUMER_MAX_DELIVER` | `5` | Максимум доставок |
| `NATS_CONSUMER_FETCH_BATCH_SIZE` | `10` | Размер батча pull-подписки |
| `NATS_CONSUMER_FETCH_TIMEOUT_SECONDS` | `5` | Таймаут fetch |
| `MAIN_BACKEND_URL` | `http://localhost:8000` | Base URL главного backend. В стеке — `http://eqsitecms-app:8000`: `eqsitecms-app` это `container_name` главного backend в `eqsitecms_network`. Значение `http://eqsitecms-backend:8000` недопустимо — контейнера с таким именем не существует. Compose-alias `backend` не используется, потому что он надёжен только внутри compose-проекта backend, а `vk-service` поднимается отдельным проектом `eqsitecms-vk` |
| `MAIN_BACKEND_SERVICE_KEY` | — | Service key для `/api/service/*` главного backend |

#### Переменные VK

Первые три заполняет **владелец VK-группы** перед первым запуском бота: без них
long-poll runtime завершается с ошибкой, а `GET /vks/bot-info` отвечает `503`.
HTTP-контур сервиса при этом остаётся работоспособным.

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `VK_GROUP_TOKEN` | — | **Заполняет владелец группы.** Токен сообщества с правами **«Сообщения» и «Управление сообществом»**: `groups.getLongPollServer` требует `manage`, одного `messages` недостаточно. Обязателен и должен быть безопасным в `production`; в логи, журнал и ответы API не попадает |
| `VK_GROUP_ID` | `0` | **Заполняет владелец группы.** Числовой идентификатор сообщества. Placeholder или нечисловое значение трактуется как «группа не настроена» |
| `VK_GROUP_SCREEN_NAME` | — | **Заполняет владелец группы.** Короткий адрес сообщества из URL (например `eqcms`), **не название группы**: из него формируются `https://vk.com/<screen_name>` и `https://vk.me/<screen_name>` |
| `VK_API_VERSION` | `5.199` | Версия VK API |
| `VK_BOT_LINK_COMMAND` | `/link` | Команда привязки, которую пользователь отправляет боту перед кодом |
| `VK_CONFIRMATION_TTL_MINUTES` | `30` | Срок действия контрольной строки |
| `VK_CONFIRMATION_CODE_LENGTH` | `8` | Длина контрольной строки, `4`–`16` |
| `VK_CONFIRMATION_MAX_ATTEMPTS` | `5` | Неуспешных попыток на один `vk_peer_id` в окне |
| `VK_CONFIRMATION_ATTEMPT_WINDOW_MINUTES` | `10` | Окно подсчёта неуспешных попыток |
| `VK_LONGPOLL_WAIT_SECONDS` | `25` | Время ожидания long-poll запроса, `1`–`90` |

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
| `VK_TEST_DATABASE_URL` | `postgresql+asyncpg://eqsitecmsvk:<password>@127.0.0.1:5436/eqsitecmsvk` | БД для `tests/integration/test_vk_repositories.py`; при отсутствии берётся адрес из настроек сервиса |

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

## Границы сервиса

В сервисе сознательно **отсутствуют**:

- доставка уведомлений о событиях в VK: публикация и потребление
  `commands.notification.vk.send` не реализованы, активного NATS consumer и
  handler VK-команд нет;
- массовые рассылки, вложения, клавиатуры и callback-кнопки бота;
- диалоговые сценарии помимо привязки аккаунта;
- webhook-режим (Callback API) — используется только Bots Long Poll;
- публичный HTTP-маршрут подтверждения: контрольную строку сверяет только
  long-poll runtime по сообщению из VK;
- пользовательская аутентификация в сервисе — он приватный и защищён сетевой
  изоляцией, owner-проверку выполняет главный backend.

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
  `vk-build`, `vk-build-nc`, `vk`, `vk-attach`, `vk-bot-logs`,
  `vk-bot-restart`, `check-vk`, `fix-vk`.
