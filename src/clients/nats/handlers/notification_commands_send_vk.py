from collections.abc import Callable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.protocols.vk import VkMessengerProtocol
from core.schemas.messaging import VK_CHANNEL_CODE, NotificationCommandSendVkData, build_command_msg_id
from core.services import VkDeliveryRetryableError, VkNotificationDeliveryService
from repositories import SQLAlchemyUserVkRepository, SQLAlchemyVkNotificationDeliveryRepository


class NotificationCommandsSendVkHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        messenger: VkMessengerProtocol,
        service_factory: Callable[..., VkNotificationDeliveryService] = VkNotificationDeliveryService,
    ) -> None:
        self._session_factory = session_factory
        self._messenger = messenger
        self._service_factory = service_factory

    async def handle(self, *, payload: bytes, headers: dict[str, str]) -> None:
        try:
            command = NotificationCommandSendVkData.model_validate_json(payload)
            message_id = UUID(headers["Nats-Msg-Id"])
        except (ValidationError, ValueError, KeyError) as exc:
            raise ValueError("Invalid VK notification command") from exc
        expected_message_id = build_command_msg_id(
            correlation_id=command.callback_request_id,
            channel_code=VK_CHANNEL_CODE,
        )
        if message_id != expected_message_id:
            raise ValueError("Nats-Msg-Id does not match the VK command identity")

        async with self._session_factory() as session:
            service = self._service_factory(
                binding_repository=SQLAlchemyUserVkRepository(session),
                delivery_repository=SQLAlchemyVkNotificationDeliveryRepository(session),
                messenger=self._messenger,
            )
            try:
                await service.deliver(command=command)
            except VkDeliveryRetryableError:
                await session.commit()
                raise
            except Exception:
                await session.rollback()
                raise
            await session.commit()
