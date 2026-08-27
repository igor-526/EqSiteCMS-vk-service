from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from clients.vk import VkbottleMessenger
from core.services.vk_binding import VkBindingService
from core.services.vk_confirmation import VkConfirmationService
from core.services.vk_state import VkStateService
from repositories.user_vk import SQLAlchemyUserVkRepository
from repositories.vk_confirmation import SQLAlchemyVkConfirmationRepository
from repositories.vk_log import SQLAlchemyVkLogRepository
from settings import vk_settings
from utils.database import get_session


async def get_vk_binding_service(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> VkBindingService:
    return VkBindingService(
        user_vk_repo=SQLAlchemyUserVkRepository(session),
        confirmation_repo=SQLAlchemyVkConfirmationRepository(session),
        vk_log_repo=SQLAlchemyVkLogRepository(session),
        ttl_minutes=vk_settings.vk_confirmation_ttl_minutes,
        code_length=vk_settings.vk_confirmation_code_length,
        # Отвязка активной привязки уведомляет пользователя в VK; сбой доставки
        # не влияет на результат операции.
        messenger=VkbottleMessenger(settings=vk_settings),
    )


async def get_vk_confirmation_service(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> VkConfirmationService:
    return VkConfirmationService(
        confirmation_repo=SQLAlchemyVkConfirmationRepository(session),
        user_vk_repo=SQLAlchemyUserVkRepository(session),
        vk_log_repo=SQLAlchemyVkLogRepository(session),
        max_attempts=vk_settings.vk_confirmation_max_attempts,
        attempt_window_minutes=vk_settings.vk_confirmation_attempt_window_minutes,
    )


async def get_vk_state_service(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> VkStateService:
    return VkStateService(
        user_vk_repo=SQLAlchemyUserVkRepository(session),
        vk_log_repo=SQLAlchemyVkLogRepository(session),
    )
