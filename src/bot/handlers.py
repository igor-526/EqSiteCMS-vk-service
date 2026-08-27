"""Обработчики событий бота: разбор команды привязки и состояние привязки."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import bot.replies as replies
from core.exceptions import ConflictError, GoneError, NotFoundError, RateLimitedError
from core.protocols.vk import VkMessengerProtocol
from core.services.vk_confirmation import VkConfirmationService
from core.services.vk_state import VkStateService
from settings import VkSettings

logger = logging.getLogger(__name__)

ACTION_MESSAGE = "vk_message"

ServiceFactory = Callable[[], Awaitable["Services"]]


@dataclass
class Services:
    """Доменные сервисы и журнал, собранные на одну обработку события."""

    confirmation: VkConfirmationService
    state: VkStateService
    log_action: Callable[..., Awaitable[dict]]
    commit: Callable[[], Awaitable[None]]


def parse_link_command(*, text: str, link_command: str) -> str | None:
    """Вернуть код из сообщения или None, если это не команда привязки с кодом."""
    stripped = text.strip()
    if not stripped:
        return None
    parts = stripped.split()
    if parts[0].casefold() != link_command.casefold():
        return None
    if len(parts) < 2:
        return ""
    return parts[1]


def is_chat_peer(peer_id: int) -> bool:
    """Сообщения из беседы не участвуют в привязке."""
    return peer_id >= replies.CHAT_PEER_THRESHOLD


class BotEventHandler:
    """Реакция на события группы: `message_new`, `message_deny`, `message_allow`."""

    def __init__(
        self,
        *,
        settings: VkSettings,
        services: ServiceFactory,
        messenger: VkMessengerProtocol,
    ) -> None:
        self._settings = settings
        self._services = services
        self._messenger = messenger

    async def handle_message(self, *, peer_id: int, from_id: int, text: str) -> str:
        """Обработать входящее сообщение и ответить пользователю. Возвращает исход."""
        services = await self._services()

        if is_chat_peer(peer_id):
            await services.log_action(
                action=ACTION_MESSAGE,
                status="ignored_chat",
                details={"vk_peer_id": str(peer_id)},
            )
            await services.commit()
            return "ignored_chat"

        code = parse_link_command(text=text, link_command=self._settings.vk_bot_link_command)
        if code is None or code == "":
            status = "unknown_command" if code is None else "missing_code"
            await services.log_action(
                action=ACTION_MESSAGE,
                status=status,
                details={"vk_peer_id": str(peer_id)},
            )
            await services.commit()
            await self._reply(peer_id, replies.instruction(self._settings.vk_bot_link_command))
            return status

        outcome, reply = await self._confirm(services=services, from_id=from_id, code=code)
        await services.commit()
        await self._reply(peer_id, reply)
        return outcome

    async def _confirm(self, *, services: Services, from_id: int, code: str) -> tuple[str, str]:
        profile = await self._messenger.get_profile(peer_id=from_id)
        try:
            result = await services.confirmation.confirm(
                code=code,
                vk_peer_id=from_id,
                vk_screen_name=profile.screen_name if profile else None,
                vk_display_name=profile.display_name if profile else None,
            )
        except RateLimitedError:
            return "rate_limited", replies.RATE_LIMITED
        except NotFoundError:
            return "not_found", replies.CODE_INVALID
        except GoneError:
            return "expired", replies.CODE_EXPIRED
        except ConflictError as exc:
            if "другому пользователю" in str(exc.message):
                return "peer_conflict", replies.PEER_CONFLICT
            return "used", replies.CODE_USED
        if result.status == "already_confirmed":
            return "already_confirmed", replies.ALREADY_LINKED
        return "confirmed", replies.LINKED

    async def handle_message_deny(self, *, user_id: int) -> str:
        """Пользователь запретил сообщения от группы."""
        services = await self._services()
        changed = await services.state.block(vk_peer_id=user_id)
        await services.commit()
        return "success" if changed else "no_binding"

    async def handle_message_allow(self, *, user_id: int) -> str:
        """Пользователь снова разрешил сообщения от группы."""
        services = await self._services()
        changed = await services.state.unblock(vk_peer_id=user_id)
        await services.commit()
        return "success" if changed else "no_binding"

    async def _reply(self, peer_id: int, text: str) -> None:
        delivered = await self._messenger.send_message(peer_id=peer_id, text=text)
        if not delivered:
            logger.warning("Bot reply was not delivered to peer=%s", peer_id)
