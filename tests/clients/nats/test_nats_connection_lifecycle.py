import logging
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest
from nats.js.errors import BadRequestError, NotFoundError

from clients.nats.client import NatsJetstreamClient
from clients.nats.lifecycle import NatsConnectionErrorPolicy
from settings import NatsSettings


def make_client(drain_error: BaseException | None = None) -> tuple[NatsJetstreamClient, MagicMock]:
    client = NatsJetstreamClient(NatsSettings())
    connection = MagicMock()
    connection.is_closed = False
    connection.drain = AsyncMock(side_effect=drain_error)
    connection.close = AsyncMock()
    client._connection = connection
    client._jetstream = MagicMock()
    return client, connection


async def test_close_drains_and_clears_state_on_healthy_connection() -> None:
    client, connection = make_client()

    await client.close()

    connection.drain.assert_awaited_once_with()
    connection.close.assert_not_awaited()
    assert client._connection is None
    assert client._jetstream is None


async def test_close_survives_drain_while_reconnecting(caplog: pytest.LogCaptureFixture) -> None:
    client, connection = make_client(nats.errors.ConnectionReconnectingError())

    with caplog.at_level(logging.WARNING):
        await client.close()

    connection.close.assert_awaited_once_with()
    assert client._connection is None
    assert client._jetstream is None
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_close_survives_drain_timeout() -> None:
    client, connection = make_client(TimeoutError())

    await client.close()

    connection.close.assert_awaited_once_with()
    assert client._connection is None


async def test_close_does_not_swallow_unrelated_errors() -> None:
    client, _ = make_client(RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await client.close()

    assert client._connection is None


async def test_transient_failures_stay_below_error_level(caplog: pytest.LogCaptureFixture) -> None:
    policy = NatsConnectionErrorPolicy(service_name="test", report_after_attempts=3)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await policy.on_error(ConnectionRefusedError(111, "Connection refused"))

    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert policy.escalated is False


async def test_prolonged_outage_escalates_exactly_once(caplog: pytest.LogCaptureFixture) -> None:
    policy = NatsConnectionErrorPolicy(service_name="test", report_after_attempts=3)

    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            await policy.on_error(ConnectionRefusedError(111, "Connection refused"))

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert policy.escalated is True


async def test_reconnect_resets_incident_state(caplog: pytest.LogCaptureFixture) -> None:
    policy = NatsConnectionErrorPolicy(service_name="test", report_after_attempts=2)

    for _ in range(5):
        await policy.on_error(ConnectionRefusedError(111, "Connection refused"))
    await policy.on_reconnected()

    assert policy.consecutive_failures == 0
    assert policy.escalated is False

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            await policy.on_error(ConnectionRefusedError(111, "Connection refused"))

    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_consumer_setup_retries_until_owner_creates_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = NatsSettings(NATS_SETUP_MAX_ATTEMPTS=5, NATS_SETUP_BACKOFF_SECONDS=0)
    client = NatsJetstreamClient(settings)

    attempts = {"count": 0}

    async def flaky() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise NotFoundError(code=404, err_code=10059, description="stream not found")

    monkeypatch.setattr(client, "_add_notification_commands_send_vk_consumer", flaky)

    await client.setup_consumers()

    assert attempts["count"] == 3


async def test_consumer_setup_fails_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = NatsSettings(NATS_SETUP_MAX_ATTEMPTS=3, NATS_SETUP_BACKOFF_SECONDS=0)
    client = NatsJetstreamClient(settings)

    attempts = {"count": 0}

    async def always_missing() -> None:
        attempts["count"] += 1
        raise NotFoundError(code=404, err_code=10059, description="stream not found")

    monkeypatch.setattr(client, "_add_notification_commands_send_vk_consumer", always_missing)

    with caplog.at_level(logging.ERROR), pytest.raises(NotFoundError):
        await client.setup_consumers()

    assert attempts["count"] == 3
    assert settings.nats_stream_notification_commands in caplog.text


async def test_consumer_setup_does_not_retry_configuration_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = NatsSettings(NATS_SETUP_MAX_ATTEMPTS=5, NATS_SETUP_BACKOFF_SECONDS=0)
    client = NatsJetstreamClient(settings)

    attempts = {"count": 0}

    async def bad_config() -> None:
        attempts["count"] += 1
        raise BadRequestError(code=400, err_code=10052, description="consumer config mismatch")

    monkeypatch.setattr(client, "_add_notification_commands_send_vk_consumer", bad_config)

    with pytest.raises(BadRequestError):
        await client.setup_consumers()

    assert attempts["count"] == 1


async def test_setup_streams_remains_noop() -> None:
    client = NatsJetstreamClient(NatsSettings())
    jetstream = MagicMock()
    jetstream.add_stream = AsyncMock()
    client._jetstream = jetstream

    await client.setup_streams()

    jetstream.add_stream.assert_not_awaited()
