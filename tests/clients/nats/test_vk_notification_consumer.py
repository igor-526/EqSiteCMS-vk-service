import asyncio
from unittest.mock import AsyncMock, Mock

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
