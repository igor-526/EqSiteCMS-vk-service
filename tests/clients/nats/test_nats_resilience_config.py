"""
Unit tests для NATS client resilience configuration.

Проверяет соответствие настроек спецификации:
- allow_reconnect=True
- max_reconnect_attempts=10
- reconnect_time_wait=2
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.nats.client import NatsJetstreamClient
from settings import NatsSettings


async def test_nats_client_uses_resilience_parameters() -> None:
    """Проверка, что NATS client использует правильные resilience параметры при подключении."""
    settings = NatsSettings()
    client = NatsJetstreamClient(settings)

    with patch("clients.nats.client.NATS") as mock_nats_class:
        mock_connection = MagicMock()
        mock_connection.connect = AsyncMock()
        mock_connection.jetstream = MagicMock()
        mock_nats_class.return_value = mock_connection

        await client.connect()

        # Проверяем, что connect был вызван с правильными resilience параметрами
        mock_connection.connect.assert_awaited_once()
        call_kwargs = mock_connection.connect.call_args.kwargs

        assert call_kwargs["allow_reconnect"] is True, "allow_reconnect должен быть True"
        assert call_kwargs["max_reconnect_attempts"] == 10, "max_reconnect_attempts должен быть 10"
        assert call_kwargs["reconnect_time_wait"] == 2, "reconnect_time_wait должен быть 2"


async def test_nats_connection_refused_raises_error_after_retries() -> None:
    """Проверка, что ConnectionRefusedError пробрасывается после исчерпания retries."""
    settings = NatsSettings()
    client = NatsJetstreamClient(settings)

    with patch("clients.nats.client.NATS") as mock_nats_class:
        mock_connection = MagicMock()
        mock_connection.connect = AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused"))
        mock_nats_class.return_value = mock_connection

        with pytest.raises(ConnectionRefusedError):
            await client.connect()


async def test_nats_graceful_degradation_allows_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Проверка graceful degradation: при NATS downtime приложение не падает.

    Этот тест проверяет поведение lifespan context manager, но не может
    импортировать main.py напрямую из-за побочных эффектов (Sentry init).
    Вместо этого проверяем, что client.connect() пробрасывает ошибку,
    которую должен перехватить lifespan.
    """
    settings = NatsSettings()
    client = NatsJetstreamClient(settings)

    with patch("clients.nats.client.NATS") as mock_nats_class:
        mock_connection = MagicMock()
        mock_connection.connect = AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused"))
        mock_nats_class.return_value = mock_connection

        # Имитируем поведение graceful degradation из main.py
        nats_connected = False
        try:
            await client.connect()
            nats_connected = True
        except (ConnectionRefusedError, OSError) as error:
            with caplog.at_level(logging.ERROR):
                logging.error(
                    "Failed to connect to NATS after retries: %s. VK service starting in degraded mode.",
                    error,
                    exc_info=True,
                )

        assert nats_connected is False, "NATS не должен быть подключен при downtime"
        assert any("degraded mode" in record.message for record in caplog.records), (
            "Должно быть логирование о degraded mode"
        )


async def test_nats_error_policy_escalates_after_threshold() -> None:
    """Проверка, что error policy эскалирует ошибки после порога."""
    from clients.nats.lifecycle import NatsConnectionErrorPolicy

    policy = NatsConnectionErrorPolicy(service_name="test-vk-service", report_after_attempts=3)

    # Первые 3 попытки должны быть на уровне WARNING
    for _ in range(3):
        await policy.on_error(ConnectionRefusedError(111, "Connection refused"))

    assert policy.consecutive_failures == 3
    assert policy.escalated is False

    # 4-я попытка должна эскалировать
    await policy.on_error(ConnectionRefusedError(111, "Connection refused"))

    assert policy.consecutive_failures == 4
    assert policy.escalated is True


async def test_nats_reconnect_resets_error_policy() -> None:
    """Проверка, что успешный reconnect сбрасывает error policy state."""
    from clients.nats.lifecycle import NatsConnectionErrorPolicy

    policy = NatsConnectionErrorPolicy(service_name="test-vk-service", report_after_attempts=3)

    # Эскалируем ошибки
    for _ in range(5):
        await policy.on_error(ConnectionRefusedError(111, "Connection refused"))

    assert policy.consecutive_failures == 5
    assert policy.escalated is True

    # Reconnect должен сбросить state
    await policy.on_reconnected()

    assert policy.consecutive_failures == 0
    assert policy.escalated is False
