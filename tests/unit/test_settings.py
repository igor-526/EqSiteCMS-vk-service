"""Конфигурация vk-service.

Удалённые email/SMTP-сущности проверяются по их настоящим именам: guard-проверка
на остатки email-домена ограничена реализацией и `tests/**` не покрывает.
"""

from collections.abc import Callable
from typing import Any, cast

import pytest

import settings as settings_module
from settings import CelerySettings, NatsSettings, Settings, VkSettings

_settings_factory = cast(Callable[..., Settings], Settings)
_vk_settings_factory = cast(Callable[..., VkSettings], VkSettings)


def build_settings(**overrides: Any) -> Settings:
    """Собрать Settings без чтения локального `.env`, чтобы тесты были детерминированными."""
    return _settings_factory(_env_file=None, **overrides)


def build_vk_settings(**overrides: Any) -> VkSettings:
    """Собрать VkSettings без чтения локального `.env`."""
    return _vk_settings_factory(_env_file=None, **overrides)


REMOVED_SETTINGS_EXPORT = "smtp_settings"
REMOVED_SETTINGS_CLASS = "SMTPSettings"
REMOVED_SECRET_ENV = "SMTP_PASSWORD"
REMOVED_TTL_FIELD = "email_confirmation_ttl_hours"
REMOVED_NATS_SUBJECT_FIELD = "nats_subject_notification_commands_send_email"
REMOVED_NATS_CONSUMER_FIELD = "nats_consumer_notification_commands_send_email"

SAFE_PRODUCTION_ENV = {
    "POSTGRES_PASSWORD": "3d0f6d1c5f7f4d0b9c9d",
    "REDIS_PASSWORD": "b1c9f3a76e2c4a9d81ff",
    "CELERY_APP_BROKER": "redis://:b1c9f3a76e2c4a9d81ff@eqsitecms-redis:6379/3",
    "CELERY_APP_BACKEND": "redis://:b1c9f3a76e2c4a9d81ff@eqsitecms-redis:6379/4",
    "MAIN_BACKEND_SERVICE_KEY": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "NATS_SERVERS": "nats://eqsitecms-nats:4222",
    "VK_GROUP_TOKEN": "vk1.a.5f6e7d8c9b0a1928374655",
}


def _apply_production_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    monkeypatch.delenv(REMOVED_SECRET_ENV, raising=False)
    for name, value in {**SAFE_PRODUCTION_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_settings_has_no_cors_or_inherited_email_fields() -> None:
    instance = build_settings()

    assert not hasattr(instance, "cors_origins")
    assert not hasattr(instance, REMOVED_TTL_FIELD)
    assert not hasattr(instance, "frontend_url")


def test_settings_module_does_not_export_smtp_configuration() -> None:
    assert not hasattr(settings_module, REMOVED_SETTINGS_EXPORT)
    assert not hasattr(settings_module, REMOVED_SETTINGS_CLASS)
    assert REMOVED_SETTINGS_EXPORT not in vars(settings_module)


def test_production_validation_passes_without_smtp_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_production_env(monkeypatch)

    instance = build_settings(ENVIRONMENT="production")

    assert instance.environment == "production"


def test_production_validation_requires_a_group_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_production_env(monkeypatch, VK_GROUP_TOKEN="")

    with pytest.raises(ValueError, match="VK_GROUP_TOKEN"):
        build_settings(ENVIRONMENT="production")


def test_production_validation_rejects_a_placeholder_group_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_production_env(monkeypatch, VK_GROUP_TOKEN="<set-vk-group-access-token>")

    with pytest.raises(ValueError, match="VK_GROUP_TOKEN"):
        build_settings(ENVIRONMENT="production")


def test_production_validation_rejects_well_known_redis_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_production_env(monkeypatch, REDIS_PASSWORD="eqsitecmsredis")

    with pytest.raises(ValueError, match="REDIS_PASSWORD"):
        build_settings(ENVIRONMENT="production")


def test_production_validation_rejects_placeholder_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_production_env(monkeypatch, CELERY_APP_BROKER="redis://:<set-redis-password>@eqsitecms-redis:6379/3")

    with pytest.raises(ValueError, match="CELERY_APP_BROKER"):
        build_settings(ENVIRONMENT="production")


def test_development_environment_does_not_require_production_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*SAFE_PRODUCTION_ENV, REMOVED_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)

    instance = build_settings(ENVIRONMENT="development")

    assert instance.environment == "development"
    assert instance.debug is True


def test_database_url_is_built_from_postgres_settings_with_asyncpg_driver() -> None:
    instance = build_settings(
        POSTGRES_USER="eqsitecmsvk",
        POSTGRES_PASSWORD="secret-value",
        POSTGRES_HOST="eqsitecms-db-vk",
        POSTGRES_PORT=5432,
        POSTGRES_DB="eqsitecmsvk",
    )

    assert instance.database_url == "postgresql+asyncpg://eqsitecmsvk:secret-value@eqsitecms-db-vk:5432/eqsitecmsvk"


def test_app_title_default_is_vk_service() -> None:
    assert Settings.model_fields["app_title"].default == "VK Service"
    assert build_settings().app_title == "VK Service"


def test_celery_settings_use_dedicated_redis_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CELERY_APP_BROKER", raising=False)
    monkeypatch.delenv("CELERY_APP_BACKEND", raising=False)

    instance = CelerySettings()

    assert instance.celery_app_broker.endswith("/3")
    assert instance.celery_app_backend.endswith("/4")
    assert (instance.celery_app_broker.endswith("/1"), instance.celery_app_backend.endswith("/2")) == (False, False)


def test_celery_app_main_default_is_vk_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CELERY_APP_MAIN", raising=False)

    assert CelerySettings().celery_app_main == "vk-service"


def test_reserved_vk_subject_is_covered_by_notification_commands_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NATS_SUBJECT_NOTIFICATION_COMMANDS_SEND_VK", raising=False)

    instance = NatsSettings()

    assert instance.nats_subject_notification_commands_send_vk == "commands.notification.vk.send"
    assert instance.nats_subjects_notification_commands == ["commands.notification.>"]


def test_reserved_vk_durable_name_is_service_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NATS_CONSUMER_NOTIFICATION_COMMANDS_SEND_VK", raising=False)

    assert NatsSettings().nats_consumer_notification_commands_send_vk == "vk-service-commands-send-vk"


@pytest.mark.parametrize("field", [REMOVED_NATS_SUBJECT_FIELD, REMOVED_NATS_CONSUMER_FIELD])
def test_removed_messaging_fields_are_absent(field: str) -> None:
    instance = NatsSettings()

    assert field not in NatsSettings.model_fields
    with pytest.raises(AttributeError):
        getattr(instance, field)


def test_comma_separated_servers_are_parsed_without_empty_entries() -> None:
    instance = NatsSettings(NATS_SERVERS=" nats://one:4222 , ,nats://two:4222 ,")

    assert instance.nats_servers == ["nats://one:4222", "nats://two:4222"]


def test_blank_servers_fall_back_to_localhost() -> None:
    assert NatsSettings(NATS_SERVERS="   ").nats_servers == ["nats://localhost:4222"]


def test_vk_settings_defaults_match_the_documented_contract() -> None:
    instance = build_vk_settings()

    assert instance.vk_bot_link_command == "/link"
    assert instance.vk_confirmation_ttl_minutes == 30
    assert instance.vk_confirmation_code_length == 8
    assert instance.vk_confirmation_max_attempts == 5
    assert instance.vk_confirmation_attempt_window_minutes == 10
    assert instance.vk_longpoll_wait_seconds == 25
    assert instance.vk_api_version == "5.199"


def test_vk_settings_treat_an_unset_group_as_not_configured() -> None:
    instance = build_vk_settings()

    assert instance.vk_group_id == 0
    assert instance.is_group_configured is False
    assert instance.is_token_usable is False


def test_vk_settings_treat_placeholders_as_not_configured() -> None:
    instance = build_vk_settings(
        VK_GROUP_TOKEN="<set-vk-group-access-token>",
        VK_GROUP_ID="<set-vk-group-numeric-id>",
        VK_GROUP_SCREEN_NAME="",
    )

    assert instance.vk_group_id == 0
    assert instance.is_group_configured is False
    assert instance.is_token_usable is False


def test_vk_settings_build_public_group_links() -> None:
    instance = build_vk_settings(VK_GROUP_ID=123, VK_GROUP_SCREEN_NAME="eqsitecms_bot")

    assert instance.is_group_configured is True
    assert instance.group_url == "https://vk.com/eqsitecms_bot"
    assert instance.dialog_url == "https://vk.me/eqsitecms_bot"


def test_vk_settings_accept_a_real_token_as_usable() -> None:
    instance = build_vk_settings(VK_GROUP_TOKEN="vk1.a.5f6e7d8c9b0a1928374655")

    assert instance.is_token_usable is True
