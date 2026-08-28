"""Адаптер `vkbottle` под протоколы домена.

Единственное место (вместе с `bot/`), где разрешён импорт `vkbottle`: домен
работает только с `core.protocols.vk`.
"""

import logging
import random

from vkbottle import API
from vkbottle_types.objects import UsersFields

from core.protocols.vk import VkUserProfile
from settings import VkSettings

logger = logging.getLogger(__name__)

RANDOM_ID_BOUND = 2**31 - 1


class VersionedAPI(API):
    """Клиент VK API с версией из настроек.

    Базовый класс `vkbottle` объявляет `API_VERSION` литералом версии, на которую
    сгенерированы типы, поэтому переопределение объявляется здесь как обычная строка.
    """

    API_VERSION: str  # type: ignore[assignment]


def build_api(settings: VkSettings) -> VersionedAPI:
    """Создать типизированный клиент VK API из настроек сервиса."""
    api = VersionedAPI(token=settings.vk_group_token)
    api.API_VERSION = settings.vk_api_version
    return api


class VkbottleMessenger:
    """Отправка сообщений и чтение публичного профиля через VK API."""

    def __init__(self, *, settings: VkSettings, api: API | None = None) -> None:
        self._settings = settings
        self._api = api

    @property
    def api(self) -> API:
        if self._api is None:
            self._api = build_api(self._settings)
        return self._api

    async def send_message(self, *, peer_id: int, text: str) -> bool:
        """Отправить текстовое сообщение. False, если VK отказал в доставке."""
        try:
            await self.api.messages.send(
                peer_id=peer_id,
                message=text,
                random_id=random.randint(1, RANDOM_ID_BOUND),  # noqa: S311
            )
        except Exception:
            logger.warning("VK message delivery failed for peer=%s", peer_id)
            return False
        return True

    async def get_profile(self, *, peer_id: int) -> VkUserProfile | None:
        """Получить публичный профиль пользователя VK."""
        try:
            users = await self.api.users.get(user_ids=[str(peer_id)], fields=[UsersFields.SCREEN_NAME])
        except Exception:
            logger.warning("VK profile lookup failed for peer=%s", peer_id, exc_info=True)
            return None
        if not users:
            return None
        user = users[0]
        first_name = (getattr(user, "first_name", "") or "").strip()
        last_name = (getattr(user, "last_name", "") or "").strip()
        display_name = " ".join(part for part in (first_name, last_name) if part) or None
        return VkUserProfile(
            peer_id=peer_id,
            screen_name=getattr(user, "screen_name", None),
            display_name=display_name,
        )
