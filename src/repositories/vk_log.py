import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.vk_log import vk_logs

logger = logging.getLogger(__name__)


class SQLAlchemyVkLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log_action(
        self,
        *,
        action: str,
        status: str,
        details: dict | None = None,
    ) -> dict:
        """Создать запись журнала действий VK-домена."""
        stmt = (
            vk_logs.insert()
            .values(
                id=uuid4(),
                event_uuid=uuid4(),
                action=action,
                status=status,
                details=details or {},
            )
            .returning(vk_logs)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().one()
        logger.info("Logged action=%s status=%s id=%s", action, status, row["id"])
        return dict(row)

    async def count_failed_since(
        self,
        *,
        action: str,
        vk_peer_id: int,
        since: datetime,
        failed_statuses: tuple[str, ...],
    ) -> int:
        """Посчитать неуспешные попытки по идентификатору VK начиная с момента."""
        stmt = select(func.count()).where(
            vk_logs.c.action == action,
            vk_logs.c.status.in_(failed_statuses),
            vk_logs.c.created_at >= since,
            vk_logs.c.details["vk_peer_id"].astext == str(vk_peer_id),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())
