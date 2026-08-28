from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from settings import SentrySettings
from utils import configure_sentry as sentry_module
from utils import observability


def test_disabled_sentry_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    init = Mock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init)
    sentry_module.configure_sentry(SentrySettings(SENTRY_ENABLED=False))
    init.assert_not_called()


@pytest.mark.parametrize("rate", [0.0, 1.0])
def test_enabled_sentry_passes_metadata_once(monkeypatch: pytest.MonkeyPatch, rate: float) -> None:
    init = Mock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init)
    config = SentrySettings(
        SENTRY_ENABLED=True,
        SENTRY_DSN="https://public@example.invalid/1",
        SENTRY_ENVIRONMENT="qa",
        SENTRY_TRACES_SAMPLE_RATE=rate,
        SENTRY_RELEASE=" release-1 ",
    )
    sentry_module.configure_sentry(config)
    init.assert_called_once()
    kwargs = init.call_args.kwargs
    assert (kwargs["dsn"], kwargs["environment"], kwargs["release"], kwargs["traces_sample_rate"]) == (
        config.sentry_dsn,
        "qa",
        "release-1",
        rate,
    )
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert len(kwargs["integrations"]) == 2


@pytest.mark.parametrize("rate", [-0.01, 1.01])
def test_invalid_sample_rate_is_rejected(rate: float) -> None:
    with pytest.raises(ValidationError):
        SentrySettings(SENTRY_TRACES_SAMPLE_RATE=rate)


def test_enabled_sentry_requires_dsn_and_normalizes_release() -> None:
    with pytest.raises(ValidationError, match="SENTRY_DSN"):
        SentrySettings(SENTRY_ENABLED=True, SENTRY_DSN=" ")
    assert SentrySettings(SENTRY_RELEASE=" ").sentry_release is None


def test_before_send_removes_credentials_and_body() -> None:
    sanitized = sentry_module.before_send(
        {
            "request": {"headers": {"Authorization": "secret", "X-Service-Key": "secret"}, "body": "secret"},
            "extra": {"postgres_password": "secret"},
        },
        {},
    )
    assert sanitized == {
        "request": {"headers": {"Authorization": "[Filtered]", "X-Service-Key": "[Filtered]"}},
        "extra": {"postgres_password": "[Filtered]"},
    }


def test_metrics_listener_is_not_started_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    starter = Mock()
    monkeypatch.setattr(observability, "start_http_server", starter)

    assert observability.start_metrics_runtime(environment="development") is None
    assert observability.start_metrics_runtime(environment="test") is None
    starter.assert_not_called()


def test_metrics_lifecycle_is_production_only_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    server, thread = Mock(), Mock()
    starter = Mock(return_value=(server, thread))
    monkeypatch.setattr(observability, "start_http_server", starter)
    assert observability.start_metrics_runtime(environment="test") is None
    runtime = observability.start_metrics_runtime(environment="PRODUCTION")
    assert runtime is not None
    assert starter.call_args.kwargs["registry"] is observability.REGISTRY
    assert (starter.call_args.kwargs["port"], starter.call_args.kwargs["addr"]) == (9000, "0.0.0.0")
    runtime.close()
    runtime.close()
    server.shutdown.assert_called_once_with()
    server.server_close.assert_called_once_with()
    thread.join.assert_called_once_with()


async def test_lifespan_closes_nats_database_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    nats = Mock(connect=AsyncMock(), setup=AsyncMock(), close=AsyncMock())
    runtime = Mock()
    monkeypatch.setattr(main.container, "nats_client", Mock(return_value=nats))
    consumer = Mock(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(main.container, "vk_notification_consumer", Mock(return_value=consumer))
    close_database = AsyncMock()
    monkeypatch.setattr(main, "close_database", close_database)
    monkeypatch.setattr(main, "start_metrics_runtime", Mock(return_value=runtime))
    async with main.lifespan(main.app):
        pass
    nats.connect.assert_awaited_once_with()
    nats.setup.assert_awaited_once_with()
    nats.close.assert_awaited_once_with()
    consumer.start.assert_awaited_once_with()
    consumer.stop.assert_awaited_once_with()
    close_database.assert_awaited_once_with()
    runtime.close.assert_called_once_with()


async def test_nats_startup_failure_is_not_masked_by_metrics_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    nats = Mock(connect=AsyncMock(side_effect=RuntimeError("nats unavailable")))
    monkeypatch.setattr(main.container, "nats_client", Mock(return_value=nats))
    start_metrics = Mock()
    monkeypatch.setattr(main, "start_metrics_runtime", start_metrics)
    with pytest.raises(RuntimeError, match="nats unavailable"):
        async with main.lifespan(main.app):
            pass
    start_metrics.assert_not_called()
