import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from api.dependencies import get_vk_binding_service
from api.schemas.vk import (
    VkBindingCreateRequest,
    VkBindingResponse,
    VkBotInfoResponse,
    VkIssueConfirmationRequest,
    VkIssueConfirmationResponse,
)
from core.exceptions import ClientError, ServiceUnavailableError
from core.services.vk_binding import VkBindingService
from repositories.user_vk import ALLOWED_STATES
from settings import vk_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vks", tags=["VK bindings"])


def _parse_user_ids(raw: str) -> list[UUID]:
    """Разобрать список идентификаторов владельцев из query-параметра."""
    parsed: list[UUID] = []
    for chunk in raw.split(","):
        candidate = chunk.strip()
        if not candidate:
            continue
        try:
            parsed.append(UUID(candidate))
        except ValueError as exc:
            raise ClientError(f"Некорректный идентификатор пользователя: {candidate}") from exc
    if not parsed:
        raise ClientError("Параметр user_ids не содержит идентификаторов")
    return parsed


def _validate_state(state: str | None) -> str | None:
    if state is None:
        return None
    if state not in ALLOWED_STATES:
        raise ClientError(f"Неизвестное состояние привязки: {state}")
    return state


@router.get("", response_model=list[VkBindingResponse])
async def get_bindings(
    user_ids: str = Query(..., description="Comma-separated list of UUIDs"),
    state: str | None = Query(None, description="Filter by binding state"),
    service: VkBindingService = Depends(get_vk_binding_service),  # noqa: B008
) -> list[dict]:
    """Получить привязки VK по списку владельцев."""
    return await service.get_bindings(user_ids=_parse_user_ids(user_ids), state=_validate_state(state))


@router.get("/bot-info", response_model=VkBotInfoResponse)
async def get_bot_info() -> dict:
    """Публичные атрибуты группы бота и шаблон команды привязки."""
    if not vk_settings.is_group_configured:
        raise ServiceUnavailableError("Конфигурация группы VK не завершена")
    return {
        "group_id": vk_settings.vk_group_id,
        "group_screen_name": vk_settings.vk_group_screen_name.strip(),
        "link_command": vk_settings.vk_bot_link_command,
        "group_url": vk_settings.group_url,
        "dialog_url": vk_settings.dialog_url,
    }


@router.post("", response_model=VkBindingResponse)
async def create_binding(
    body: VkBindingCreateRequest,
    response: Response,
    service: VkBindingService = Depends(get_vk_binding_service),  # noqa: B008
) -> dict:
    """Создать запись привязки без выдачи контрольной строки. Идемпотентно."""
    binding, created = await service.ensure_binding(user_id=body.user_id)
    response.status_code = 201 if created else 200
    return binding


@router.post("/issue-confirmation", response_model=VkIssueConfirmationResponse, status_code=201)
async def issue_confirmation(
    body: VkIssueConfirmationRequest,
    service: VkBindingService = Depends(get_vk_binding_service),  # noqa: B008
) -> dict:
    """Выдать владельцу новую контрольную строку привязки."""
    issued = await service.issue_confirmation(user_id=body.user_id)
    return {
        **issued,
        "link_command": vk_settings.vk_bot_link_command,
        "dialog_url": vk_settings.dialog_url,
    }


@router.delete("/{user_id}", status_code=204)
async def delete_binding(
    user_id: UUID,
    service: VkBindingService = Depends(get_vk_binding_service),  # noqa: B008
) -> None:
    """Отвязать аккаунт VK владельца. Идемпотентно."""
    await service.unlink(user_id=user_id)
