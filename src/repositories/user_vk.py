import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AlreadyExistsError
from models.user_vk import user_vks

logger = logging.getLogger(__name__)

STATE_PENDING = "PENDING"
STATE_ACTIVE = "ACTIVE"
STATE_BLOCKED = "BLOCKED"
ALLOWED_STATES: tuple[str, ...] = (STATE_PENDING, STATE_ACTIVE, STATE_BLOCKED)


class SQLAlchemyUserVkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: UUID) -> dict:
        """Создать привязку в состоянии PENDING без идентификатора VK."""
        now = datetime.now(UTC)
        stmt = (
            user_vks.insert()
            .values(
                user_id=user_id,
                vk_peer_id=None,
                state=STATE_PENDING,
                vk_screen_name=None,
                vk_display_name=None,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            )
            .returning(user_vks)
        )
        try:
            result = await self._session.execute(stmt)
        except IntegrityError as exc:
            await self._session.rollback()
            logger.warning("IntegrityError creating user_vk user_id=%s: %s", user_id, exc)
            raise AlreadyExistsError(f"VK binding already exists for user_id={user_id}") from exc
        row = result.mappings().one()
        logger.info("Created user_vk id=%s user_id=%s", row["id"], user_id)
        return dict(row)

    async def get_by_id(self, *, record_id: UUID) -> dict | None:
        stmt = select(user_vks).where(user_vks.c.id == record_id)
        result = await self._session.execute(stmt)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_by_user_id(self, *, user_id: UUID) -> dict | None:
        stmt = select(user_vks).where(
            user_vks.c.user_id == user_id,
            user_vks.c.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_by_user_ids(self, *, user_ids: list[UUID], state: str | None = None) -> list[dict]:
        stmt = select(user_vks).where(
            user_vks.c.user_id.in_(user_ids),
            user_vks.c.deleted_at.is_(None),
        )
        if state is not None:
            stmt = stmt.where(user_vks.c.state == state)
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_by_peer_id(self, *, vk_peer_id: int) -> dict | None:
        stmt = select(user_vks).where(
            user_vks.c.vk_peer_id == vk_peer_id,
            user_vks.c.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def activate(
        self,
        *,
        record_id: UUID,
        vk_peer_id: int,
        vk_screen_name: str | None = None,
        vk_display_name: str | None = None,
    ) -> dict:
        """Привязать идентификатор VK и перевести запись в ACTIVE."""
        now = datetime.now(UTC)
        stmt = (
            update(user_vks)
            .where(
                user_vks.c.id == record_id,
                user_vks.c.deleted_at.is_(None),
            )
            .values(
                vk_peer_id=vk_peer_id,
                state=STATE_ACTIVE,
                vk_screen_name=vk_screen_name,
                vk_display_name=vk_display_name,
                updated_at=now,
            )
            .returning(user_vks)
        )
        try:
            result = await self._session.execute(stmt)
        except IntegrityError as exc:
            await self._session.rollback()
            logger.warning("IntegrityError activating user_vk id=%s peer=%s: %s", record_id, vk_peer_id, exc)
            raise AlreadyExistsError(f"VK account already linked: vk_peer_id={vk_peer_id}") from exc
        row = result.mappings().one()
        logger.info("Activated user_vk id=%s peer=%s", record_id, vk_peer_id)
        return dict(row)

    async def set_state(self, *, record_id: UUID, state: str) -> dict | None:
        if state not in ALLOWED_STATES:
            raise ValueError(f"Unsupported VK binding state: {state}")
        now = datetime.now(UTC)
        stmt = (
            update(user_vks)
            .where(
                user_vks.c.id == record_id,
                user_vks.c.deleted_at.is_(None),
            )
            .values(state=state, updated_at=now)
            .returning(user_vks)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().one_or_none()
        if row is None:
            logger.warning("No active user_vk to set state id=%s", record_id)
            return None
        logger.info("Set user_vk id=%s state=%s", record_id, state)
        return dict(row)

    async def soft_delete(self, *, user_id: UUID) -> bool:
        """Мягко удалить привязку. Идемпотентно."""
        now = datetime.now(UTC)
        stmt = (
            update(user_vks)
            .where(
                user_vks.c.user_id == user_id,
                user_vks.c.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_at=now)
        )
        result = await self._session.execute(stmt)
        deleted = bool(result.rowcount)  # type: ignore[attr-defined]
        if deleted:
            logger.info("Soft deleted user_vk user_id=%s", user_id)
        else:
            logger.info("No active user_vk to delete user_id=%s", user_id)
        return deleted
