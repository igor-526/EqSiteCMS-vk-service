import os
from functools import cached_property

from pydantic import Field, field_validator, model_validator
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
                "VK_GROUP_TOKEN",
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
        alias="NATS_STREAM_NOTIFICATION_COMMANDS",
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

    # CONNECTION ERROR POLICY
    nats_error_report_after_attempts: int = Field(
        default=3,
        alias="NATS_ERROR_REPORT_AFTER_ATTEMPTS",
        ge=1,
    )

    # SETUP RETRY (durable на stream другого владельца)
    nats_setup_max_attempts: int = Field(
        default=10,
        alias="NATS_SETUP_MAX_ATTEMPTS",
        ge=1,
    )

    nats_setup_backoff_seconds: float = Field(
        default=2.0,
        alias="NATS_SETUP_BACKOFF_SECONDS",
        ge=0,
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


class VkSettings(BaseSettings):
    """Настройки группы VK и параметров подтверждения привязки."""

    vk_group_token: str = Field(default="", alias="VK_GROUP_TOKEN")
    vk_group_id: int = Field(default=0, alias="VK_GROUP_ID")
    vk_group_screen_name: str = Field(default="", alias="VK_GROUP_SCREEN_NAME")
    vk_api_version: str = Field(default="5.199", alias="VK_API_VERSION")
    vk_bot_link_command: str = Field(default="/link", alias="VK_BOT_LINK_COMMAND")
    vk_confirmation_ttl_minutes: int = Field(default=30, alias="VK_CONFIRMATION_TTL_MINUTES", ge=1)
    vk_confirmation_code_length: int = Field(default=8, alias="VK_CONFIRMATION_CODE_LENGTH", ge=4, le=16)
    vk_confirmation_max_attempts: int = Field(default=5, alias="VK_CONFIRMATION_MAX_ATTEMPTS", ge=1)
    vk_confirmation_attempt_window_minutes: int = Field(
        default=10,
        alias="VK_CONFIRMATION_ATTEMPT_WINDOW_MINUTES",
        ge=1,
    )
    vk_longpoll_wait_seconds: int = Field(default=25, alias="VK_LONGPOLL_WAIT_SECONDS", ge=1, le=90)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("vk_group_id", mode="before")
    @classmethod
    def treat_placeholder_group_id_as_unset(cls, value: object) -> object:
        """Placeholder из `.env.example` означает «группа ещё не настроена», а не ошибку."""
        if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
            return 0
        return value

    @property
    def is_group_configured(self) -> bool:
        """Группа считается настроенной, когда известны её идентификатор и адрес."""
        return bool(self.vk_group_screen_name.strip()) and self.vk_group_id > 0

    @property
    def is_token_usable(self) -> bool:
        """Токен пригоден, если он задан и не является placeholder-значением."""
        token = self.vk_group_token.strip()
        return bool(token) and not token.startswith("<")

    @property
    def group_url(self) -> str:
        return f"https://vk.com/{self.vk_group_screen_name.strip()}"

    @property
    def dialog_url(self) -> str:
        return f"https://vk.me/{self.vk_group_screen_name.strip()}"


settings = Settings()
sentry_settings = SentrySettings()
nats_settings = NatsSettings()
celery_settings = CelerySettings()
vk_settings = VkSettings()


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
