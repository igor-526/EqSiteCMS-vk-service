from datetime import datetime
from typing import Protocol
from uuid import UUID


class UserVkRepositoryProtocol(Protocol):
    async def create(self, *, user_id: UUID) -> dict:
        """Создать привязку в состоянии PENDING без vk_peer_id."""
        ...

    async def get_by_id(self, *, record_id: UUID) -> dict | None:
        """Получить запись по её id (включая удалённые)."""
        ...

    async def get_by_user_id(self, *, user_id: UUID) -> dict | None:
        """Получить активную привязку пользователя."""
        ...

    async def get_by_user_ids(self, *, user_ids: list[UUID], state: str | None = None) -> list[dict]:
        """Получить активные привязки по списку пользователей."""
        ...

    async def get_by_peer_id(self, *, vk_peer_id: int) -> dict | None:
        """Получить активную привязку по идентификатору пользователя VK."""
        ...

    async def activate(
        self,
        *,
        record_id: UUID,
        vk_peer_id: int,
        vk_screen_name: str | None = None,
        vk_display_name: str | None = None,
    ) -> dict:
        """Привязать идентификатор VK и перевести запись в состояние ACTIVE."""
        ...

    async def set_state(self, *, record_id: UUID, state: str) -> dict | None:
        """Сменить состояние привязки."""
        ...

    async def soft_delete(self, *, user_id: UUID) -> bool:
        """Мягко удалить привязку. Идемпотентно: False, если активной записи нет."""
        ...


class VkConfirmationRepositoryProtocol(Protocol):
    async def create(self, *, user_vk_id: UUID, code: str, expires_at: datetime) -> dict:
        """Создать запись подтверждения."""
        ...

    async def get_by_code(self, *, code: str) -> dict | None:
        """Найти подтверждение по контрольной строке."""
        ...

    async def mark_used(self, *, confirmation_id: UUID) -> None:
        """Отметить подтверждение использованным."""
        ...

    async def invalidate_previous(self, *, user_vk_id: UUID) -> int:
        """Инвалидировать все неиспользованные подтверждения записи."""
        ...


class VkLogRepositoryProtocol(Protocol):
    async def log_action(
        self,
        *,
        action: str,
        status: str,
        details: dict | None = None,
    ) -> dict:
        """Создать запись журнала действий."""
        ...

    async def count_failed_since(
        self,
        *,
        action: str,
        vk_peer_id: int,
        since: datetime,
        failed_statuses: tuple[str, ...],
    ) -> int:
        """Посчитать неуспешные попытки по идентификатору VK начиная с момента."""
        ...
