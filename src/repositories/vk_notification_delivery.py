from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.vk_notification_delivery import vk_notification_deliveries


class SQLAlchemyVkNotificationDeliveryRepository:
    """PostgreSQL ledger with a transaction-scoped lock per event/recipient."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_attempt(self, *, event_uuid: UUID, user_id: UUID, vk_peer_id: int) -> dict | None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:event_uuid), hashtext(:user_id))"),
            {"event_uuid": str(event_uuid), "user_id": str(user_id)},
        )
        existing_result = await self._session.execute(
            select(vk_notification_deliveries).where(
                vk_notification_deliveries.c.event_uuid == event_uuid,
                vk_notification_deliveries.c.user_id == user_id,
            )
        )
        existing = existing_result.mappings().one_or_none()
        if existing is not None and existing["status"] == "SENT":
            return None

        now = datetime.now(UTC)
        stmt = (
            insert(vk_notification_deliveries)
            .values(
                event_uuid=event_uuid,
                user_id=user_id,
                vk_peer_id=vk_peer_id,
                status="PENDING",
                attempts=1,
                last_error=None,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_vk_notification_deliveries_event_user",
                set_={
                    "vk_peer_id": vk_peer_id,
                    "status": "PENDING",
                    "attempts": vk_notification_deliveries.c.attempts + 1,
                    "last_error": None,
                    "updated_at": now,
                },
            )
            .returning(vk_notification_deliveries)
        )
        return dict((await self._session.execute(stmt)).mappings().one())

    async def mark_sent(self, *, event_uuid: UUID, user_id: UUID) -> dict:
        now = datetime.now(UTC)
        stmt = (
            update(vk_notification_deliveries)
            .where(
                vk_notification_deliveries.c.event_uuid == event_uuid,
                vk_notification_deliveries.c.user_id == user_id,
            )
            .values(status="SENT", last_error=None, sent_at=now, updated_at=now)
            .returning(vk_notification_deliveries)
        )
        return dict((await self._session.execute(stmt)).mappings().one())

    async def mark_failed(self, *, event_uuid: UUID, user_id: UUID, error_category: str) -> dict:
        stmt = (
            update(vk_notification_deliveries)
            .where(
                vk_notification_deliveries.c.event_uuid == event_uuid,
                vk_notification_deliveries.c.user_id == user_id,
            )
            .values(status="FAILED", last_error=error_category[:64], updated_at=datetime.now(UTC))
            .returning(vk_notification_deliveries)
        )
        return dict((await self._session.execute(stmt)).mappings().one())
