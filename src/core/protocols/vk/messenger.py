from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VkUserProfile:
    """Публичные атрибуты пользователя VK, кэшируемые в привязке."""

    peer_id: int
    screen_name: str | None = None
    display_name: str | None = None


class VkMessengerProtocol(Protocol):
    async def send_message(self, *, peer_id: int, text: str) -> bool:
        """Отправить текстовое сообщение пользователю. False, если доставка невозможна."""
        ...

    async def get_profile(self, *, peer_id: int) -> VkUserProfile | None:
        """Получить публичный профиль пользователя или None, если он недоступен."""
        ...
