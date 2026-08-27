import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.vk_confirmation import vk_confirmations

logger = logging.getLogger(__name__)


class SQLAlchemyVkConfirmationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_vk_id: UUID, code: str, expires_at: datetime) -> dict:
        new_id = uuid4()
        now = datetime.now(UTC)
        stmt = (
            vk_confirmations.insert()
            .values(
                id=new_id,
                user_vk_id=user_vk_id,
                code=code,
                expires_at=expires_at,
                created_at=now,
                used_at=None,
            )
            .returning(vk_confirmations)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().one()
        logger.info("Created vk_confirmation id=%s user_vk_id=%s", new_id, user_vk_id)
        return dict(row)

    async def get_by_code(self, *, code: str) -> dict | None:
        stmt = select(vk_confirmations).where(vk_confirmations.c.code == code)
        result = await self._session.execute(stmt)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def mark_used(self, *, confirmation_id: UUID) -> None:
        now = datetime.now(UTC)
        stmt = update(vk_confirmations).where(vk_confirmations.c.id == confirmation_id).values(used_at=now)
        result = await self._session.execute(stmt)
        if result.rowcount:  # type: ignore[attr-defined]
            logger.info("Marked vk_confirmation id=%s as used", confirmation_id)
        else:
            logger.warning("No vk_confirmation found to mark used id=%s", confirmation_id)

    async def invalidate_previous(self, *, user_vk_id: UUID) -> int:
        now = datetime.now(UTC)
        stmt = (
            update(vk_confirmations)
            .where(
                vk_confirmations.c.user_vk_id == user_vk_id,
                vk_confirmations.c.used_at.is_(None),
            )
            .values(used_at=now)
        )
        result = await self._session.execute(stmt)
        invalidated = int(result.rowcount or 0)  # type: ignore[attr-defined]
        logger.info("Invalidated %d previous confirmations for user_vk_id=%s", invalidated, user_vk_id)
        return invalidated
