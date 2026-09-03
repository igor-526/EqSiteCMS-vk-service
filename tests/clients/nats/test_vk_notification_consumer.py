import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from clients.nats.consumers import NotificationCommandsSendVkConsumer
from settings import NatsSettings


def consumer() -> tuple[NotificationCommandsSendVkConsumer, AsyncMock, AsyncMock]:
    client = Mock()
    client.jetstream = Mock()
    client.jetstream.pull_subscribe = AsyncMock()
    handler = AsyncMock()
    return NotificationCommandsSendVkConsumer(client=client, settings=NatsSettings(), handler=handler), client, handler


async def test_ut25_consumer_acks_after_success() -> None:
    value, _, handler = consumer()
    message = Mock(data=b"{}", headers={})
    message.ack, message.nak = AsyncMock(), AsyncMock()
    await value._process_message(message)
    handler.handle.assert_awaited_once()
    message.ack.assert_awaited_once()
    message.nak.assert_not_awaited()


async def test_ut26_consumer_naks_malformed_command_without_delivery() -> None:
    value, _, handler = consumer()
    handler.handle.side_effect = ValueError("invalid")
    message = Mock(data=b"not-json", headers={})
    message.ack, message.nak = AsyncMock(), AsyncMock()
    await value._process_message(message)
    message.nak.assert_awaited_once()
    message.ack.assert_not_awaited()


async def test_ut39_start_stop_are_idempotent() -> None:
    value, client, _ = consumer()
    subscription = AsyncMock()
    subscription.fetch.side_effect = asyncio.CancelledError
    client.jetstream.pull_subscribe.return_value = subscription
    await value.start()
    task = value._task
    await value.start()
    assert value._task is task
    assert client.jetstream.pull_subscribe.await_count == 1
    await value.stop()
    await value.stop()
    assert value.is_running is False


async def test_ut_065_vn_01_builtin_timeout_is_idle(caplog: pytest.LogCaptureFixture) -> None:
    value, _, handler = consumer()
    subscription = AsyncMock()
    subscription.fetch.side_effect = [TimeoutError(), asyncio.CancelledError()]
    value._subscription = subscription

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError):
            await value._consume()

    assert subscription.fetch.await_count == 2
    handler.handle.assert_not_awaited()
    assert not caplog.records


async def test_ut_065_vn_02_command_is_processed_after_idle_timeout() -> None:
    value, _, handler = consumer()
    message = Mock(data=b"{}", headers={})
    message.ack, message.nak = AsyncMock(), AsyncMock()
    subscription = AsyncMock()
    subscription.fetch.side_effect = [TimeoutError(), [message], asyncio.CancelledError()]
    value._subscription = subscription

    with pytest.raises(asyncio.CancelledError):
        await value._consume()

    assert subscription.fetch.await_count == 3
    handler.handle.assert_awaited_once_with(payload=b"{}", headers={})
    message.ack.assert_awaited_once_with()
    message.nak.assert_not_awaited()


async def test_ut_065_vn_03_cancellation_and_broker_error_remain_visible(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, _, handler = consumer()
    broker_error = RuntimeError("broker unavailable")
    subscription = AsyncMock()
    subscription.fetch.side_effect = [broker_error, asyncio.CancelledError()]
    value._subscription = subscription
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(asyncio.CancelledError):
            await value._consume()

    assert subscription.fetch.await_count == 2
    sleep.assert_awaited_once_with(1)
    handler.handle.assert_not_awaited()
    assert "Failed to fetch VK notification commands" in caplog.text
