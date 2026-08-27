import logging

from repositories.protocols import UserVkRepositoryProtocol, VkLogRepositoryProtocol
from repositories.user_vk import STATE_ACTIVE, STATE_BLOCKED

logger = logging.getLogger(__name__)

ACTION_DENY = "vk_message_deny"
ACTION_ALLOW = "vk_message_allow"


class VkStateService:
    """Синхронизация состояния привязки с разрешением группы писать пользователю."""

    def __init__(
        self,
        *,
        user_vk_repo: UserVkRepositoryProtocol,
        vk_log_repo: VkLogRepositoryProtocol,
    ) -> None:
        self._user_vk_repo = user_vk_repo
        self._vk_log_repo = vk_log_repo

    async def block(self, *, vk_peer_id: int) -> bool:
        """Пользователь запретил сообщения от группы."""
        return await self._transition(vk_peer_id=vk_peer_id, state=STATE_BLOCKED, action=ACTION_DENY)

    async def unblock(self, *, vk_peer_id: int) -> bool:
        """Пользователь снова разрешил сообщения от группы."""
        return await self._transition(vk_peer_id=vk_peer_id, state=STATE_ACTIVE, action=ACTION_ALLOW)

    async def _transition(self, *, vk_peer_id: int, state: str, action: str) -> bool:
        binding = await self._user_vk_repo.get_by_peer_id(vk_peer_id=vk_peer_id)
        if binding is None:
            logger.info("No VK binding for peer=%s, %s ignored", vk_peer_id, action)
            await self._vk_log_repo.log_action(
                action=action,
                status="no_binding",
                details={"vk_peer_id": str(vk_peer_id)},
            )
            return False

        # Состояние читается до записи: репозиторий может вернуть тот же объект строки.
        previous_state = str(binding["state"])
        record_id = binding["id"]
        await self._user_vk_repo.set_state(record_id=record_id, state=state)
        await self._vk_log_repo.log_action(
            action=action,
            status="success",
            details={
                "vk_peer_id": str(vk_peer_id),
                "user_vk_id": str(record_id),
                "previous_state": previous_state,
                "state": state,
            },
        )
        logger.info("VK binding peer=%s moved to %s", vk_peer_id, state)
        return True
