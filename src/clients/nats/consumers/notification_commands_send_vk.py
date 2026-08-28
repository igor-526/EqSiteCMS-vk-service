import asyncio
import logging
from typing import Protocol

from nats.aio.msg import Msg
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext

from clients.nats.client import NatsJetstreamClient
from settings import NatsSettings

logger = logging.getLogger(__name__)


class VkCommandHandlerProtocol(Protocol):
    async def handle(self, *, payload: bytes, headers: dict[str, str]) -> None: ...


class NotificationCommandsSendVkConsumer:
    def __init__(
        self,
        *,
        client: NatsJetstreamClient,
        settings: NatsSettings,
        handler: VkCommandHandlerProtocol,
    ) -> None:
        self._client = client
        self._settings = settings
        self._handler = handler
        self._subscription: JetStreamContext.PullSubscription | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._subscription = await self._client.jetstream.pull_subscribe(
            subject=self._settings.nats_subject_notification_commands_send_vk,
            durable=self._settings.nats_consumer_notification_commands_send_vk,
            stream=self._settings.nats_stream_notification_commands,
        )
        self._task = asyncio.create_task(self._consume(), name="notification-commands-send-vk-consumer")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._subscription = None

    async def _consume(self) -> None:
        if self._subscription is None:
            raise RuntimeError("VK notification consumer has no subscription")
        while True:
            try:
                messages = await self._subscription.fetch(
                    batch=self._settings.nats_consumer_fetch_batch_size,
                    timeout=self._settings.nats_consumer_fetch_timeout_seconds,
                )
            except NatsTimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to fetch VK notification commands")
                await asyncio.sleep(1)
                continue
            for message in messages:
                await self._process_message(message)

    async def _process_message(self, message: Msg) -> None:
        headers = dict(message.headers) if message.headers is not None else {}
        try:
            await self._handler.handle(payload=message.data, headers=headers)
        except Exception:
            logger.warning("VK notification command processing failed")
            await message.nak()
            return
        await message.ack()
