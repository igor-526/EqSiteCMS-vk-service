import logging

from core.protocols.vk import VkMessengerProtocol
from core.schemas.messaging import NotificationCommandSendVkData
from repositories.protocols import UserVkRepositoryProtocol, VkNotificationDeliveryRepositoryProtocol

logger = logging.getLogger(__name__)


class VkDeliveryRetryableError(Exception):
    """At least one eligible recipient could not be delivered."""


class VkNotificationDeliveryService:
    def __init__(
        self,
        *,
        binding_repository: UserVkRepositoryProtocol,
        delivery_repository: VkNotificationDeliveryRepositoryProtocol,
        messenger: VkMessengerProtocol,
    ) -> None:
        self._bindings = binding_repository
        self._deliveries = delivery_repository
        self._messenger = messenger

    async def deliver(self, *, command: NotificationCommandSendVkData) -> None:
        bindings = await self._bindings.get_by_user_ids(user_ids=command.user_ids, state="ACTIVE")
        active = {
            binding["user_id"]: int(binding["vk_peer_id"])
            for binding in bindings
            if binding.get("deleted_at") is None and binding.get("vk_peer_id") is not None
        }
        failed = False
        for user_id in command.user_ids:
            peer_id = active.get(user_id)
            if peer_id is None:
                continue
            claimed = await self._deliveries.claim_attempt(
                event_uuid=command.event_uuid, user_id=user_id, vk_peer_id=peer_id
            )
            if claimed is None:
                continue
            if await self._messenger.send_message(peer_id=peer_id, text=command.text):
                await self._deliveries.mark_sent(event_uuid=command.event_uuid, user_id=user_id)
                logger.info("VK notification sent event=%s user=%s peer=%s", command.event_uuid, user_id, peer_id)
            else:
                await self._deliveries.mark_failed(
                    event_uuid=command.event_uuid, user_id=user_id, error_category="VK_API_SEND_FAILED"
                )
                logger.warning("VK notification failed event=%s user=%s peer=%s", command.event_uuid, user_id, peer_id)
                failed = True
        if failed:
            raise VkDeliveryRetryableError("VK delivery has retryable recipient failures")
