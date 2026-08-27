"""Long-poll runtime бота VK.

Самостоятельный процесс: HTTP-приложение сервиса его не запускает. Bots Long Poll
допускает одного слушателя на группу, поэтому экземпляр должен быть единственным.
"""

import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable

from vkbottle import API, Bot, GroupEventType
from vkbottle.bot import Message
from vkbottle.exception_factory import VKAPIError

from clients.vk import VkbottleMessenger, build_api
from core.services.vk_confirmation import VkConfirmationService
from core.services.vk_state import VkStateService
from repositories.user_vk import SQLAlchemyUserVkRepository
from repositories.vk_confirmation import SQLAlchemyVkConfirmationRepository
from repositories.vk_log import SQLAlchemyVkLogRepository
from settings import VkSettings, settings
from settings import vk_settings as vk_settings_instance
from utils.configure_sentry import configure_sentry
from utils.database import SessionFactory, close_database

from .handlers import BotEventHandler, Services

logger = logging.getLogger(__name__)


class PreflightError(RuntimeError):
    """Конфигурация VK не позволяет запустить long-poll цикл."""


TOKEN_REQUIRED_MESSAGE = (
    "VK_GROUP_TOKEN is empty or still holds a placeholder value. "
    "Fill it in services/vk-service/.env before starting the bot runtime."
)
GROUP_ID_REQUIRED_MESSAGE = (
    "VK_GROUP_ID is not set to a positive community id. "
    "Fill it in services/vk-service/.env before starting the bot runtime."
)
LONGPOLL_ACCESS_MESSAGE = (
    "VK rejected groups.getLongPollServer. The community token needs BOTH the "
    "'messages' and the 'manage' (Управление сообществом) scopes, and Long Poll API "
    "must be enabled in the community settings "
    "(Управление -> Работа с API -> Long Poll API) with the message_new, "
    "message_allow and message_deny event types switched on. "
    "Re-issue the token with both scopes and restart the bot."
)
INVALID_TOKEN_MESSAGE = (
    "VK rejected the community token as invalid or expired. "
    "Re-issue VK_GROUP_TOKEN for the community and restart the bot."
)

ACCESS_DENIED_CODES = frozenset({15, 100})
INVALID_TOKEN_CODES = frozenset({5, 27, 28})


def make_services_factory(vk_settings: VkSettings) -> Callable[[], Awaitable[Services]]:
    """Собрать доменные сервисы на каждое событие в своей сессии."""

    async def factory() -> Services:
        session = SessionFactory()
        user_vk_repo = SQLAlchemyUserVkRepository(session)
        vk_log_repo = SQLAlchemyVkLogRepository(session)
        confirmation = VkConfirmationService(
            confirmation_repo=SQLAlchemyVkConfirmationRepository(session),
            user_vk_repo=user_vk_repo,
            vk_log_repo=vk_log_repo,
            max_attempts=vk_settings.vk_confirmation_max_attempts,
            attempt_window_minutes=vk_settings.vk_confirmation_attempt_window_minutes,
        )
        state = VkStateService(user_vk_repo=user_vk_repo, vk_log_repo=vk_log_repo)

        async def commit() -> None:
            try:
                await session.commit()
            finally:
                await session.close()

        return Services(
            confirmation=confirmation,
            state=state,
            log_action=vk_log_repo.log_action,
            commit=commit,
        )

    return factory


def build_bot(*, vk_settings: VkSettings, api: API | None = None) -> tuple[Bot, BotEventHandler]:
    """Создать бота с зарегистрированными обработчиками событий группы."""
    vk_api = api or build_api(vk_settings)
    bot = Bot(api=vk_api)
    bot.polling.wait = min(vk_settings.vk_longpoll_wait_seconds, 90)  # type: ignore[attr-defined]

    handler = BotEventHandler(
        settings=vk_settings,
        services=make_services_factory(vk_settings),
        messenger=VkbottleMessenger(settings=vk_settings, api=vk_api),
    )

    @bot.on.message()
    async def on_message(message: Message) -> None:
        await handler.handle_message(
            peer_id=message.peer_id,
            from_id=message.from_id,
            text=message.text or "",
        )

    @bot.on.raw_event(GroupEventType.MESSAGE_DENY, dict)
    async def on_message_deny(event: dict) -> None:
        await handler.handle_message_deny(user_id=int(event["object"]["user_id"]))

    @bot.on.raw_event(GroupEventType.MESSAGE_ALLOW, dict)
    async def on_message_allow(event: dict) -> None:
        await handler.handle_message_allow(user_id=int(event["object"]["user_id"]))

    return bot, handler


async def preflight(bot: Bot, vk_settings: VkSettings) -> str | None:
    """Один раз проверить доступ к Bots Long Poll. Возвращает текст ошибки или None.

    Без этой проверки нехватка прав всплывает уже внутри long-poll цикла сырым
    `VKAPIError`, а `restart: always` превращает её в бесконечный поток трейсбеков.
    """
    if vk_settings.vk_group_id <= 0:
        return GROUP_ID_REQUIRED_MESSAGE
    try:
        await bot.api.request(
            "groups.getLongPollServer",
            {"group_id": vk_settings.vk_group_id},
        )
    except VKAPIError as exc:  # type: ignore[misc]
        code = getattr(exc, "code", None)
        if code in INVALID_TOKEN_CODES:
            return f"{INVALID_TOKEN_MESSAGE} (VK error {code})"
        if code in ACCESS_DENIED_CODES:
            return f"{LONGPOLL_ACCESS_MESSAGE} (VK error {code})"
        return f"VK rejected groups.getLongPollServer with error {code}: {exc}"
    except Exception as exc:
        logger.warning("Long poll preflight could not reach VK: %s", exc)
        return None
    return None


def _install_shutdown_handlers(bot: Bot) -> None:
    """Останов long-poll цикла по сигналам оркестратора."""
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, bot.polling.stop)
        except NotImplementedError, RuntimeError:
            logger.warning("Signal %s cannot be installed on this platform", sig)


async def run(vk_settings: VkSettings | None = None) -> None:
    """Запустить long-poll цикл до остановки процесса."""
    active_settings = vk_settings or vk_settings_instance
    bot, _ = build_bot(vk_settings=active_settings)

    problem = await preflight(bot, active_settings)
    if problem is not None:
        logger.error(problem)
        await close_database()
        raise PreflightError(problem)

    _install_shutdown_handlers(bot)
    logger.info("VK bot runtime started, long poll wait=%ss", active_settings.vk_longpoll_wait_seconds)
    try:
        await bot.run_polling()
    finally:
        await close_database()
        logger.info("VK bot runtime stopped")


def main(argv: list[str] | None = None) -> int:
    """Точка входа процесса. Возвращает код выхода."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    configure_sentry()

    if not vk_settings_instance.is_token_usable:
        logger.error(TOKEN_REQUIRED_MESSAGE)
        return 1

    logger.info("Starting VK bot runtime in %s environment", settings.environment)
    try:
        asyncio.run(run(vk_settings_instance))
    except PreflightError:
        return 1
    except KeyboardInterrupt:
        logger.info("VK bot runtime interrupted")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
