import asyncio
import logging

import nats.errors
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, PubAck
from nats.js.errors import NotFoundError

from clients.nats.lifecycle import NatsConnectionErrorPolicy
from settings import NatsSettings

logger = logging.getLogger(__name__)


class NatsJetstreamClient:
    def __init__(self, settings: NatsSettings) -> None:
        self._settings = settings

        self._connection: NATS | None = None
        self._jetstream: JetStreamContext | None = None
        self._error_policy = NatsConnectionErrorPolicy(
            service_name="vk-service",
            report_after_attempts=settings.nats_error_report_after_attempts,
        )

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and self._connection.is_connected

    def _get_jetstream(self) -> JetStreamContext:
        if self._jetstream is None or not self.is_connected:
            raise RuntimeError("NATS JetStream client is not connected")

        return self._jetstream

    @property
    def jetstream(self) -> JetStreamContext:
        return self._get_jetstream()

    async def connect(self) -> None:
        if self.is_connected:
            return

        self._connection = NATS()
        self._error_policy.reset()

        await self._connection.connect(
            servers=self._settings.nats_servers,
            name="vk-service",
            connect_timeout=5,
            reconnect_time_wait=2,
            max_reconnect_attempts=-1,
            error_cb=self._error_policy.on_error,
            disconnected_cb=self._error_policy.on_disconnected,
            reconnected_cb=self._error_policy.on_reconnected,
            closed_cb=self._error_policy.on_closed,
        )

        self._jetstream = self._connection.jetstream()

    async def close(self) -> None:
        if self._connection is None:
            return

        try:
            if not self._connection.is_closed:
                try:
                    await self._connection.drain()
                except (TimeoutError, nats.errors.Error) as error:
                    logger.warning("NATS drain failed on shutdown, closing connection: %s", error)
                    await self._connection.close()
        finally:
            self._connection = None
            self._jetstream = None

    async def setup(self) -> None:
        """
        Точка входа настройки JetStream.

        Скелет `vk-service` не владеет топологией stream `NOTIFICATION_COMMANDS`:
        stream и durable consumers создаются сервисами-владельцами канала.
        Поэтому обе фазы настройки остаются no-op до активации VK-канала.
        """
        if not self.is_connected:
            raise RuntimeError("NATS client must be connected before setup")

        await self.setup_streams()
        await self.setup_consumers()

    async def setup_streams(self) -> None:
        """No-op: сервис не является владельцем ни одного stream."""
        return None

    async def setup_consumers(self) -> None:
        """
        Create/update only the durable owned by VK Service.

        Durable регистрируется на stream `NOTIFICATION_COMMANDS`, которым
        владеет другой сервис. Если владелец ещё не создал stream, JetStream
        отвечает 404; это штатная гонка деплоя, поэтому попытка повторяется
        с backoff, а не роняет startup.
        """
        stream = self._settings.nats_stream_notification_commands
        max_attempts = self._settings.nats_setup_max_attempts
        backoff_seconds = self._settings.nats_setup_backoff_seconds

        for attempt in range(1, max_attempts + 1):
            try:
                await self._add_notification_commands_send_vk_consumer()
                return
            except NotFoundError:
                if attempt >= max_attempts:
                    logger.error(
                        "Stream %s не появился после %s попыток: durable %s не зарегистрирован",
                        stream,
                        max_attempts,
                        self._settings.nats_consumer_notification_commands_send_vk,
                    )
                    raise

                wait_time = backoff_seconds * attempt if backoff_seconds > 0 else 0
                logger.warning(
                    "Stream %s ещё не создан владельцем (попытка %s из %s). Повтор через %.1f секунд.",
                    stream,
                    attempt,
                    max_attempts,
                    wait_time,
                )
                if wait_time:
                    await asyncio.sleep(wait_time)

    async def _add_notification_commands_send_vk_consumer(self) -> None:
        await self.jetstream.add_consumer(
            stream=self._settings.nats_stream_notification_commands,
            config=ConsumerConfig(
                durable_name=self._settings.nats_consumer_notification_commands_send_vk,
                filter_subject=self._settings.nats_subject_notification_commands_send_vk,
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=self._settings.nats_consumer_ack_wait_seconds,
                max_deliver=self._settings.nats_consumer_max_deliver,
            ),
        )

    async def publish(
        self,
        *,
        subject: str,
        payload: bytes,
        headers: dict[str, str] | None = None,
    ) -> PubAck:
        jetstream = self._get_jetstream()

        return await jetstream.publish(
            subject=subject,
            payload=payload,
            headers=headers,
        )
