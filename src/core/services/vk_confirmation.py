import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from core.exceptions import AlreadyExistsError, ConflictError, GoneError, NotFoundError, RateLimitedError
from core.services.vk_code import mask_code, normalize_code
from repositories.protocols import (
    UserVkRepositoryProtocol,
    VkConfirmationRepositoryProtocol,
    VkLogRepositoryProtocol,
)
from repositories.user_vk import STATE_ACTIVE

logger = logging.getLogger(__name__)

ACTION_CONFIRMATION = "vk_confirmation"
FAILED_STATUSES: tuple[str, ...] = ("not_found", "used", "expired", "peer_conflict")


@dataclass(frozen=True)
class ConfirmationResult:
    status: str
    user_id: UUID
    user_vk_id: UUID


class VkConfirmationService:
    """Сверка контрольной строки и привязка аккаунта VK к пользователю."""

    def __init__(
        self,
        *,
        confirmation_repo: VkConfirmationRepositoryProtocol,
        user_vk_repo: UserVkRepositoryProtocol,
        vk_log_repo: VkLogRepositoryProtocol,
        max_attempts: int,
        attempt_window_minutes: int,
    ) -> None:
        self._confirmation_repo = confirmation_repo
        self._user_vk_repo = user_vk_repo
        self._vk_log_repo = vk_log_repo
        self._max_attempts = max_attempts
        self._attempt_window_minutes = attempt_window_minutes

    async def confirm(
        self,
        *,
        code: str,
        vk_peer_id: int,
        vk_screen_name: str | None = None,
        vk_display_name: str | None = None,
    ) -> ConfirmationResult:
        """Подтвердить привязку по контрольной строке, полученной от пользователя VK."""
        normalized = normalize_code(code)
        masked = mask_code(normalized)
        logger.info("Confirming VK binding peer=%s code=%s", vk_peer_id, masked)

        await self._enforce_rate_limit(vk_peer_id=vk_peer_id, masked=masked)

        confirmation = await self._confirmation_repo.get_by_code(code=normalized)
        if confirmation is None:
            await self._log(status="not_found", vk_peer_id=vk_peer_id, details={"code": masked})
            raise NotFoundError("Код подтверждения недействителен")

        confirmation_id = confirmation["id"]
        record_id = confirmation["user_vk_id"]
        binding = await self._user_vk_repo.get_by_id(record_id=record_id)

        if confirmation["used_at"] is not None:
            if self._is_same_peer_active(binding=binding, vk_peer_id=vk_peer_id):
                await self._log(
                    status="already_confirmed",
                    vk_peer_id=vk_peer_id,
                    details={"code": masked, "user_vk_id": str(record_id)},
                )
                return ConfirmationResult(
                    status="already_confirmed",
                    user_id=UUID(str(binding["user_id"])),  # type: ignore[index]
                    user_vk_id=UUID(str(record_id)),
                )
            await self._log(
                status="used",
                vk_peer_id=vk_peer_id,
                details={"code": masked, "user_vk_id": str(record_id)},
            )
            raise ConflictError("Код подтверждения уже использован")

        if confirmation["expires_at"] <= datetime.now(UTC):
            await self._log(
                status="expired",
                vk_peer_id=vk_peer_id,
                details={"code": masked, "user_vk_id": str(record_id)},
            )
            raise GoneError("Срок действия кода подтверждения истёк")

        if binding is None or binding["deleted_at"] is not None:
            await self._log(
                status="error",
                vk_peer_id=vk_peer_id,
                details={"code": masked, "user_vk_id": str(record_id), "reason": "binding_missing"},
            )
            raise NotFoundError("Привязка не найдена")

        occupied = await self._user_vk_repo.get_by_peer_id(vk_peer_id=vk_peer_id)
        if occupied is not None and UUID(str(occupied["id"])) != UUID(str(record_id)):
            await self._log(
                status="peer_conflict",
                vk_peer_id=vk_peer_id,
                details={"code": masked, "user_vk_id": str(record_id)},
            )
            raise ConflictError("Этот аккаунт VK уже привязан к другому пользователю")

        try:
            await self._user_vk_repo.activate(
                record_id=UUID(str(record_id)),
                vk_peer_id=vk_peer_id,
                vk_screen_name=vk_screen_name,
                vk_display_name=vk_display_name,
            )
        except AlreadyExistsError:
            await self._log(
                status="peer_conflict",
                vk_peer_id=vk_peer_id,
                details={"code": masked, "user_vk_id": str(record_id)},
            )
            raise ConflictError("Этот аккаунт VK уже привязан к другому пользователю") from None

        await self._confirmation_repo.mark_used(confirmation_id=UUID(str(confirmation_id)))
        user_id = UUID(str(binding["user_id"]))
        await self._log(
            status="success",
            vk_peer_id=vk_peer_id,
            details={
                "code": masked,
                "user_vk_id": str(record_id),
                "user_id": str(user_id),
            },
        )
        logger.info("VK binding confirmed user_id=%s peer=%s", user_id, vk_peer_id)
        return ConfirmationResult(status="confirmed", user_id=user_id, user_vk_id=UUID(str(record_id)))

    @staticmethod
    def _is_same_peer_active(*, binding: dict | None, vk_peer_id: int) -> bool:
        """Повторная доставка того же подтверждения не должна выглядеть ошибкой."""
        if binding is None or binding["deleted_at"] is not None:
            return False
        if str(binding["state"]) != STATE_ACTIVE:
            return False
        stored_peer = binding.get("vk_peer_id")
        return stored_peer is not None and int(stored_peer) == vk_peer_id

    async def _enforce_rate_limit(self, *, vk_peer_id: int, masked: str) -> None:
        since = datetime.now(UTC) - timedelta(minutes=self._attempt_window_minutes)
        failures = await self._vk_log_repo.count_failed_since(
            action=ACTION_CONFIRMATION,
            vk_peer_id=vk_peer_id,
            since=since,
            failed_statuses=FAILED_STATUSES,
        )
        if failures >= self._max_attempts:
            await self._log(status="rate_limited", vk_peer_id=vk_peer_id, details={"code": masked})
            raise RateLimitedError("Слишком много неудачных попыток, повторите позже")

    async def _log(self, *, status: str, vk_peer_id: int, details: dict) -> None:
        await self._vk_log_repo.log_action(
            action=ACTION_CONFIRMATION,
            status=status,
            details={"vk_peer_id": str(vk_peer_id), **details},
        )
