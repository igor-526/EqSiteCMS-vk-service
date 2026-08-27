import os
from functools import cached_property

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=True, alias="DEBUG")
    app_title: str = Field(default="VK Service", alias="APP_TITLE")

    postgres_user: str = Field(default="app", alias="POSTGRES_USER")
    postgres_password: str = Field(default="app", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="app", alias="POSTGRES_DB")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment.lower() == "production":
            required = (
                "POSTGRES_PASSWORD",
                "REDIS_PASSWORD",
                "CELERY_APP_BROKER",
                "CELERY_APP_BACKEND",
                "MAIN_BACKEND_SERVICE_KEY",
                "NATS_SERVERS",
            )
            unsafe = {"", "app", "changeme", "eqsitecmsredis"}

            def is_unsafe(name: str) -> bool:
                value = os.getenv(name, "").strip().lower()
                return value in unsafe or any(marker in value for marker in ("<set-", "<generate-"))

            invalid = [name for name in required if is_unsafe(name)]
            if invalid:
                raise ValueError(f"Unsafe or missing production settings: {', '.join(invalid)}")
        return self

    @cached_property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class SentrySettings(BaseSettings):
    sentry_enabled: bool = Field(default=False, alias="SENTRY_ENABLED")
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")
    sentry_environment: str = Field(default="development", alias="SENTRY_ENVIRONMENT")
    sentry_traces_sample_rate: float = Field(default=0.0, alias="SENTRY_TRACES_SAMPLE_RATE", ge=0.0, le=1.0)
    sentry_release: str | None = Field(default=None, alias="SENTRY_RELEASE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def validate_sentry_configuration(self) -> SentrySettings:
        self.sentry_dsn = self.sentry_dsn.strip()
        self.sentry_environment = self.sentry_environment.strip()
        self.sentry_release = (self.sentry_release or "").strip() or None
        if self.sentry_enabled and not self.sentry_dsn:
            raise ValueError("SENTRY_DSN is required when SENTRY_ENABLED=true")
        return self


class NatsSettings(BaseSettings):
    # BASE
    nats_servers_raw: str = Field(
        default="nats://localhost:4222",
        alias="NATS_SERVERS",
    )

    @property
    def nats_servers(self) -> list[str]:
        if self.nats_servers_raw.strip():
            return [server.strip() for server in self.nats_servers_raw.split(",") if server.strip()]

        return [
            "nats://localhost:4222",
        ]

    # STREAMS
    nats_stream_site_events: str = Field(
        default="SITE_EVENTS",
        alias="NATS_STREAM_SITE_EVENTS",
    )
    nats_stream_notification_commands: str = Field(
        default="NOTIFICATION_COMMANDS",
        alias="NOTIFICATION_COMMANDS",
    )

    # CONSUMERS (зарезервировано, подписка не активирована)
    nats_consumer_notification_commands_send_vk: str = Field(
        default="vk-service-commands-send-vk",
        alias="NATS_CONSUMER_NOTIFICATION_COMMANDS_SEND_VK",
    )

    # SUBJECTS (зарезервировано, подписка не активирована)
    nats_subject_notification_commands_send_vk: str = Field(
        default="commands.notification.vk.send",
        alias="NATS_SUBJECT_NOTIFICATION_COMMANDS_SEND_VK",
    )
    nats_subjects_notification_commands_raw: str = Field(
        default="commands.notification.>", alias="NATS_SUBJECTS_NOTIFICATION_COMMANDS"
    )

    @property
    def nats_subjects_notification_commands(self) -> list[str]:
        if self.nats_subjects_notification_commands_raw.strip():
            return [o.strip() for o in self.nats_subjects_notification_commands_raw.split(",") if o.strip()]
        return [
            "commands.notification.>",
        ]

    # CONSUMER DELIVERY
    nats_consumer_ack_wait_seconds: int = Field(
        default=30,
        alias="NATS_CONSUMER_ACK_WAIT_SECONDS",
        ge=1,
    )

    nats_consumer_max_deliver: int = Field(
        default=5,
        alias="NATS_CONSUMER_MAX_DELIVER",
        ge=1,
    )

    # PULL SETTINGS
    nats_consumer_fetch_batch_size: int = Field(
        default=10,
        alias="NATS_CONSUMER_FETCH_BATCH_SIZE",
        ge=1,
    )

    nats_consumer_fetch_timeout_seconds: float = Field(
        default=5,
        alias="NATS_CONSUMER_FETCH_TIMEOUT_SECONDS",
        gt=0,
    )

    model_config = SettingsConfigDict(
        populate_by_name=True,
    )


class CelerySettings(BaseSettings):
    celery_app_main: str = Field(
        default="vk-service",
        alias="CELERY_APP_MAIN",
    )

    celery_app_broker: str = Field(
        default="redis://:eqsitecmsredis@redis:6379/3",
        alias="CELERY_APP_BROKER",
    )

    celery_app_backend: str = Field(
        default="redis://:eqsitecmsredis@redis:6379/4",
        alias="CELERY_APP_BACKEND",
    )

    model_config = SettingsConfigDict(
        populate_by_name=True,
    )


settings = Settings()
sentry_settings = SentrySettings()
nats_settings = NatsSettings()
celery_settings = CelerySettings()


class MainBackendSettings(BaseSettings):
    """Настройки для подключения к main backend."""

    main_backend_url: str = Field(
        default="http://localhost:8000",
        alias="MAIN_BACKEND_URL",
    )
    main_backend_service_key: str = Field(
        default="",
        alias="MAIN_BACKEND_SERVICE_KEY",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


main_backend_settings = MainBackendSettings()
