import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from core.exceptions import AlreadyExistsError, ConflictError
from core.protocols.vk import VkMessengerProtocol
from core.services.vk_code import MAX_GENERATION_ATTEMPTS, generate_code, mask_code
from repositories.protocols import (
    UserVkRepositoryProtocol,
    VkConfirmationRepositoryProtocol,
    VkLogRepositoryProtocol,
)
from repositories.user_vk import STATE_ACTIVE, STATE_BLOCKED

logger = logging.getLogger(__name__)

ACTION_ISSUE = "vk_issue_confirmation"
ACTION_UNLINK = "vk_unlink"

UNLINK_MESSAGE = (
    "Привязка этого аккаунта VK к профилю EqSiteCMS отменена. "
    "Чтобы привязать аккаунт снова, получите новый код в настройках уведомлений."
)


class VkBindingService:
    """Выдача контрольной строки и отвязка аккаунта VK владельцем."""

    def __init__(
        self,
        *,
        user_vk_repo: UserVkRepositoryProtocol,
        confirmation_repo: VkConfirmationRepositoryProtocol,
        vk_log_repo: VkLogRepositoryProtocol,
        ttl_minutes: int,
        code_length: int,
        messenger: VkMessengerProtocol | None = None,
    ) -> None:
        self._user_vk_repo = user_vk_repo
        self._confirmation_repo = confirmation_repo
        self._vk_log_repo = vk_log_repo
        self._ttl_minutes = ttl_minutes
        self._code_length = code_length
        self._messenger = messenger

    async def get_binding(self, *, user_id: UUID) -> dict | None:
        """Получить активную привязку владельца."""
        return await self._user_vk_repo.get_by_user_id(user_id=user_id)

    async def get_bindings(self, *, user_ids: list[UUID], state: str | None = None) -> list[dict]:
        """Получить активные привязки по списку владельцев."""
        return await self._user_vk_repo.get_by_user_ids(user_ids=user_ids, state=state)

    async def ensure_binding(self, *, user_id: UUID) -> tuple[dict, bool]:
        """Вернуть активную привязку владельца, создав её при отсутствии."""
        current = await self._user_vk_repo.get_by_user_id(user_id=user_id)
        if current is not None:
            return current, False
        try:
            return await self._user_vk_repo.create(user_id=user_id), True
        except AlreadyExistsError:
            # Конкурентный запрос того же владельца мог выиграть вставку.
            existing = await self._user_vk_repo.get_by_user_id(user_id=user_id)
            if existing is None:
                raise
            return existing, False

    async def issue_confirmation(self, *, user_id: UUID) -> dict:
        """Выдать новую контрольную строку владельцу, инвалидировав предыдущие."""
        logger.info("Issuing VK confirmation for user_id=%s", user_id)
        binding, _ = await self.ensure_binding(user_id=user_id)

        state = str(binding["state"])
        if state == STATE_ACTIVE:
            await self._vk_log_repo.log_action(
                action=ACTION_ISSUE,
                status="conflict_active",
                details={"user_id": str(user_id), "user_vk_id": str(binding["id"])},
            )
            raise ConflictError("Аккаунт VK уже привязан")
        if state == STATE_BLOCKED:
            await self._vk_log_repo.log_action(
                action=ACTION_ISSUE,
                status="conflict_blocked",
                details={"user_id": str(user_id), "user_vk_id": str(binding["id"])},
            )
            raise ConflictError("Бот заблокирован: разрешите сообщения от группы в диалоге с ботом")

        record_id = binding["id"]
        await self._confirmation_repo.invalidate_previous(user_vk_id=record_id)

        code = await self._create_unique_code(user_vk_id=record_id)
        expires_at = datetime.now(UTC) + timedelta(minutes=self._ttl_minutes)
        confirmation = await self._confirmation_repo.create(
            user_vk_id=record_id,
            code=code,
            expires_at=expires_at,
        )

        await self._vk_log_repo.log_action(
            action=ACTION_ISSUE,
            status="success",
            details={
                "user_id": str(user_id),
                "user_vk_id": str(record_id),
                "code": mask_code(code),
            },
        )
        return {
            "code": code,
            "expires_at": confirmation["expires_at"],
            "state": state,
        }

    async def _create_unique_code(self, *, user_vk_id: UUID) -> str:
        """Сгенерировать код, не занятый другой записью."""
        for _ in range(MAX_GENERATION_ATTEMPTS):
            code = generate_code(self._code_length)
            if await self._confirmation_repo.get_by_code(code=code) is None:
                return code
            logger.warning("Confirmation code collision for user_vk_id=%s, regenerating", user_vk_id)
        raise ConflictError("Не удалось сгенерировать уникальный код подтверждения, повторите попытку")

    async def unlink(self, *, user_id: UUID) -> bool:
        """Отвязать аккаунт VK владельца. Идемпотентно."""
        logger.info("Unlinking VK binding for user_id=%s", user_id)
        binding = await self._user_vk_repo.get_by_user_id(user_id=user_id)
        if binding is None:
            await self._vk_log_repo.log_action(
                action=ACTION_UNLINK,
                status="noop",
                details={"user_id": str(user_id)},
            )
            return False

        # Снимок читается до записи: репозиторий может вернуть тот же объект строки.
        record_id = binding["id"]
        previous_state = str(binding["state"])
        raw_peer_id = binding.get("vk_peer_id")
        peer_id = int(raw_peer_id) if raw_peer_id is not None else None

        await self._confirmation_repo.invalidate_previous(user_vk_id=record_id)
        await self._user_vk_repo.soft_delete(user_id=user_id)
        await self._vk_log_repo.log_action(
            action=ACTION_UNLINK,
            status="success",
            details={
                "user_id": str(user_id),
                "user_vk_id": str(record_id),
                "previous_state": previous_state,
            },
        )
        await self._notify_unlinked(previous_state=previous_state, peer_id=peer_id)
        return True

    async def _notify_unlinked(self, *, previous_state: str, peer_id: int | None) -> None:
        """Сообщить пользователю об отвязке; сбой доставки не влияет на результат."""
        if self._messenger is None or peer_id is None or previous_state != STATE_ACTIVE:
            return
        try:
            await self._messenger.send_message(peer_id=peer_id, text=UNLINK_MESSAGE)
        except Exception:
            logger.warning("Failed to notify VK user about unlink, peer=%s", peer_id, exc_info=True)
