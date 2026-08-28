"""Контракт NATS-адаптера скелета: клиент подключается, но не владеет топологией."""

from unittest.mock import AsyncMock, Mock

import pytest

from clients.nats import client as client_module
from clients.nats.client import NatsJetstreamClient
from settings import NatsSettings


def _connected_client(monkeypatch: pytest.MonkeyPatch) -> tuple[NatsJetstreamClient, Mock, AsyncMock]:
    jetstream = AsyncMock()
    connection = Mock(is_connected=True, is_closed=False)
    connection.connect = AsyncMock()
    connection.drain = AsyncMock()
    connection.jetstream = Mock(return_value=jetstream)
    monkeypatch.setattr(client_module, "NATS", Mock(return_value=connection))

    return NatsJetstreamClient(settings=NatsSettings()), connection, jetstream


async def test_setup_streams_never_creates_a_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, jetstream = _connected_client(monkeypatch)
    await client.connect()

    await client.setup_streams()

    jetstream.add_stream.assert_not_awaited()
    assert jetstream.mock_calls == []


async def test_setup_registers_only_vk_delivery_durable(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, jetstream = _connected_client(monkeypatch)
    await client.connect()

    await client.setup()

    jetstream.add_consumer.assert_awaited_once()
    jetstream.pull_subscribe.assert_not_awaited()
    config = jetstream.add_consumer.await_args.kwargs["config"]
    assert jetstream.add_consumer.await_args.kwargs["stream"] == "NOTIFICATION_COMMANDS"
    assert config.durable_name == "vk-service-commands-send-vk"
    assert config.filter_subject == "commands.notification.vk.send"
    assert config.max_deliver == 5


async def test_connect_announces_the_service_client_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client, connection, _ = _connected_client(monkeypatch)

    await client.connect()

    assert connection.connect.await_args.kwargs["name"] == "vk-service"
    assert connection.connect.await_args.kwargs["servers"] == NatsSettings().nats_servers
    assert client.is_connected is True


async def test_setup_without_connection_raises_runtime_error() -> None:
    client = NatsJetstreamClient(settings=NatsSettings())

    with pytest.raises(RuntimeError, match="must be connected"):
        await client.setup()

    with pytest.raises(RuntimeError, match="not connected"):
        _ = client.jetstream


async def test_repeated_close_without_connection_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    client, connection, _ = _connected_client(monkeypatch)

    await client.close()
    await client.connect()
    await client.close()
    await client.close()

    connection.drain.assert_awaited_once_with()
    assert client.is_connected is False
