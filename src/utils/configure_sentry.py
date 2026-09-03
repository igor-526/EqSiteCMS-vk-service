from typing import Any, cast

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.types import Event

from settings import SentrySettings, sentry_settings

_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "service_key",
    "service-key",
    "x-service-key",
    "password",
    "postgres_password",
    "vk_group_token",
    "dsn",
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {item.replace("-", "_") for item in _SENSITIVE_KEYS}
        return {
            key: "[Filtered]" if str(key).lower().replace("-", "_") in normalized else _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in {"data", "body"}
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def before_send(event: Event, _: dict[str, Any]) -> Event:
    return cast(Event, _sanitize(event))


def configure_sentry(config: SentrySettings = sentry_settings) -> None:
    if not config.sentry_enabled:
        return

    sentry_sdk.init(
        dsn=config.sentry_dsn,
        environment=config.sentry_environment,
        release=config.sentry_release,
        traces_sample_rate=config.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=before_send,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
    )

    # Транзиентные reconnect'ы NATS не являются инцидентами: собственный
    # error_cb клиента эскалирует их сам, а логирование библиотеки
    # не должно обходить эту политику.
    ignore_logger("nats.aio.client")
    # BotPolling reports handled network failures through the root vkbottle
    # logger. Keep the local record, but do not turn this library retry into a
    # Sentry event; application loggers and exception capture remain enabled.
    ignore_logger("vkbottle")
