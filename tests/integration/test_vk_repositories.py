"""Infrastructure-тесты VK-репозиториев на реальной PostgreSQL.

Запускаются только целью `make test-infra` при поднятой БД `eqsitecmsvk`.
Адрес берётся из `VK_TEST_DATABASE_URL`, иначе из настроек сервиса.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from core.exceptions import AlreadyExistsError
from models import user_vks, vk_logs, vk_notification_deliveries
from repositories.user_vk import (
    STATE_ACTIVE,
    STATE_BLOCKED,
    STATE_PENDING,
    SQLAlchemyUserVkRepository,
)
from repositories.vk_confirmation import SQLAlchemyVkConfirmationRepository
from repositories.vk_log import SQLAlchemyVkLogRepository
from repositories.vk_notification_delivery import SQLAlchemyVkNotificationDeliveryRepository
from settings import settings

pytestmark = pytest.mark.infrastructure

EXPECTED_TABLES = {"user_vks", "vk_confirmations", "vk_logs", "vk_notification_deliveries"}
DONOR_TABLES = {"user_emails", "email_confirmations", "email_logs"}


def _database_url() -> str:
    return os.getenv("VK_TEST_DATABASE_URL", "").strip() or settings.database_url


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    try:
        async with _isolated_session(engine) as active_session:
            yield active_session
    finally:
        await engine.dispose()


@asynccontextmanager
async def _isolated_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Keep every test write inside an outer transaction owned by the fixture."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as active_session:
                yield active_session
        finally:
            await transaction.rollback()


async def test_fixture_rollback_preserves_preexisting_active_binding() -> None:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    preserved_id, preserved_user_id = uuid4(), uuid4()
    test_user_id = uuid4()
    preserved_peer_id = 1_000_000_000 + uuid4().int % 8_000_000_000
    try:
        async with factory() as setup:
            await setup.execute(
                user_vks.insert().values(
                    id=preserved_id,
                    user_id=preserved_user_id,
                    vk_peer_id=preserved_peer_id,
                    state=STATE_ACTIVE,
                )
            )
            await setup.commit()

        async with _isolated_session(engine) as isolated:
            created = await SQLAlchemyUserVkRepository(isolated).create(user_id=test_user_id)
            await isolated.commit()
            assert created["user_id"] == test_user_id

        async with factory() as verification:
            preserved = (
                await verification.execute(select(user_vks.c.state).where(user_vks.c.id == preserved_id))
            ).scalar_one()
            rolled_back = (
                await verification.execute(select(user_vks.c.id).where(user_vks.c.user_id == test_user_id))
            ).scalar_one_or_none()

            assert preserved == STATE_ACTIVE
            assert rolled_back is None
    finally:
        async with factory() as cleanup:
            await cleanup.execute(delete(user_vks).where(user_vks.c.id == preserved_id))
            await cleanup.commit()
        await engine.dispose()


async def test_schema_contains_only_the_vk_domain(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))
    tables = {row[0] for row in result.all()}

    assert EXPECTED_TABLES <= tables
    assert not tables & DONOR_TABLES
    assert tables <= EXPECTED_TABLES | {"alembic_version"}


async def test_create_defaults_to_pending_without_peer(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)

    row = await repo.create(user_id=uuid4())

    assert row["state"] == STATE_PENDING
    assert row["vk_peer_id"] is None
    assert row["deleted_at"] is None


async def test_second_active_binding_for_the_same_user_is_rejected(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    user_id = uuid4()
    await repo.create(user_id=user_id)

    with pytest.raises(AlreadyExistsError):
        await repo.create(user_id=user_id)


async def test_two_pending_rows_without_peer_do_not_conflict(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)

    first = await repo.create(user_id=uuid4())
    second = await repo.create(user_id=uuid4())

    assert first["vk_peer_id"] is second["vk_peer_id"] is None


async def test_peer_id_is_unique_across_active_bindings(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    owner = await repo.create(user_id=uuid4())
    intruder = await repo.create(user_id=uuid4())
    await repo.activate(record_id=owner["id"], vk_peer_id=10101)

    with pytest.raises(AlreadyExistsError):
        await repo.activate(record_id=intruder["id"], vk_peer_id=10101)


async def test_soft_delete_frees_user_and_peer_for_reuse(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    user_id = uuid4()
    first = await repo.create(user_id=user_id)
    await repo.activate(record_id=first["id"], vk_peer_id=20202)

    assert await repo.soft_delete(user_id=user_id) is True
    recreated = await repo.create(user_id=user_id)
    activated = await repo.activate(record_id=recreated["id"], vk_peer_id=20202)

    assert activated["state"] == STATE_ACTIVE
    assert await repo.get_by_id(record_id=first["id"]) is not None


async def test_soft_delete_is_idempotent(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    user_id = uuid4()
    await repo.create(user_id=user_id)

    assert await repo.soft_delete(user_id=user_id) is True
    assert await repo.soft_delete(user_id=user_id) is False
    assert await repo.soft_delete(user_id=uuid4()) is False


async def test_reads_ignore_deleted_rows(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    user_id = uuid4()
    row = await repo.create(user_id=user_id)
    await repo.activate(record_id=row["id"], vk_peer_id=30303)
    await repo.soft_delete(user_id=user_id)

    assert await repo.get_by_user_id(user_id=user_id) is None
    assert await repo.get_by_peer_id(vk_peer_id=30303) is None
    assert await repo.get_by_user_ids(user_ids=[user_id]) == []


async def test_get_by_user_ids_filters_by_state(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    active_owner, pending_owner = uuid4(), uuid4()
    active = await repo.create(user_id=active_owner)
    await repo.create(user_id=pending_owner)
    await repo.activate(record_id=active["id"], vk_peer_id=40404)
    user_ids = [active_owner, pending_owner]

    assert len(await repo.get_by_user_ids(user_ids=user_ids)) == 2
    assert [row["user_id"] for row in await repo.get_by_user_ids(user_ids=user_ids, state=STATE_ACTIVE)] == [
        active_owner
    ]
    assert [row["user_id"] for row in await repo.get_by_user_ids(user_ids=user_ids, state=STATE_PENDING)] == [
        pending_owner
    ]


async def test_set_state_updates_only_active_rows(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    user_id = uuid4()
    row = await repo.create(user_id=user_id)

    updated = await repo.set_state(record_id=row["id"], state=STATE_BLOCKED)
    assert updated is not None and updated["state"] == STATE_BLOCKED

    await repo.soft_delete(user_id=user_id)
    assert await repo.set_state(record_id=row["id"], state=STATE_ACTIVE) is None


async def test_set_state_rejects_unknown_values(session: AsyncSession) -> None:
    repo = SQLAlchemyUserVkRepository(session)
    row = await repo.create(user_id=uuid4())

    with pytest.raises(ValueError):
        await repo.set_state(record_id=row["id"], state="UNLINKED")


async def test_confirmation_code_is_unique(session: AsyncSession) -> None:
    bindings = SQLAlchemyUserVkRepository(session)
    codes = SQLAlchemyVkConfirmationRepository(session)
    first = await bindings.create(user_id=uuid4())
    second = await bindings.create(user_id=uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    await codes.create(user_vk_id=first["id"], code="UNIQUE01", expires_at=expires_at)

    with pytest.raises(IntegrityError):
        await codes.create(user_vk_id=second["id"], code="UNIQUE01", expires_at=expires_at)


async def test_invalidate_previous_marks_only_unused_codes(session: AsyncSession) -> None:
    bindings = SQLAlchemyUserVkRepository(session)
    codes = SQLAlchemyVkConfirmationRepository(session)
    binding = await bindings.create(user_id=uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    used = await codes.create(user_vk_id=binding["id"], code="USED0001", expires_at=expires_at)
    await codes.create(user_vk_id=binding["id"], code="FRESH001", expires_at=expires_at)
    await codes.mark_used(confirmation_id=used["id"])

    assert await codes.invalidate_previous(user_vk_id=binding["id"]) == 1
    assert await codes.invalidate_previous(user_vk_id=binding["id"]) == 0


async def test_log_action_persists_details_and_unique_event_uuid(session: AsyncSession) -> None:
    logs = SQLAlchemyVkLogRepository(session)

    first = await logs.log_action(action="vk_confirmation", status="success", details={"vk_peer_id": "5"})
    second = await logs.log_action(action="vk_confirmation", status="not_found", details={"vk_peer_id": "5"})

    assert first["event_uuid"] != second["event_uuid"]
    assert first["details"] == {"vk_peer_id": "5"}
    assert first["created_at"] is not None


async def test_duplicate_event_uuid_is_rejected(session: AsyncSession) -> None:
    shared = uuid4()
    await session.execute(
        vk_logs.insert().values(id=uuid4(), event_uuid=shared, action="probe", status="success", details={})
    )

    with pytest.raises(IntegrityError):
        await session.execute(
            vk_logs.insert().values(id=uuid4(), event_uuid=shared, action="probe", status="success", details={})
        )


async def test_count_failed_since_is_scoped_to_peer_status_and_window(session: AsyncSession) -> None:
    logs = SQLAlchemyVkLogRepository(session)
    for status in ("not_found", "expired", "success"):
        await logs.log_action(action="vk_confirmation", status=status, details={"vk_peer_id": "77"})
    await logs.log_action(action="vk_confirmation", status="not_found", details={"vk_peer_id": "88"})
    since = datetime.now(UTC) - timedelta(minutes=10)
    failed = ("not_found", "used", "expired", "peer_conflict")

    in_window = await logs.count_failed_since(
        action="vk_confirmation", vk_peer_id=77, since=since, failed_statuses=failed
    )
    outside_window = await logs.count_failed_since(
        action="vk_confirmation",
        vk_peer_id=77,
        since=datetime.now(UTC) + timedelta(minutes=1),
        failed_statuses=failed,
    )
    other_peer = await logs.count_failed_since(
        action="vk_confirmation", vk_peer_id=88, since=since, failed_statuses=failed
    )

    assert (in_window, outside_window, other_peer) == (2, 0, 1)


async def test_ut37_delivery_claim_serializes_concurrent_duplicate() -> None:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    event_uuid, user_id = uuid4(), uuid4()
    try:
        async with factory() as first, factory() as second:
            first_repo = SQLAlchemyVkNotificationDeliveryRepository(first)
            second_repo = SQLAlchemyVkNotificationDeliveryRepository(second)
            assert await first_repo.claim_attempt(event_uuid=event_uuid, user_id=user_id, vk_peer_id=50505)
            await first_repo.mark_sent(event_uuid=event_uuid, user_id=user_id)
            competing = asyncio.create_task(
                second_repo.claim_attempt(event_uuid=event_uuid, user_id=user_id, vk_peer_id=50505)
            )
            await asyncio.sleep(0)
            assert competing.done() is False
            await first.commit()
            assert await competing is None
            await second.commit()
        async with factory() as cleanup:
            await cleanup.execute(
                delete(vk_notification_deliveries).where(
                    vk_notification_deliveries.c.event_uuid == event_uuid,
                    vk_notification_deliveries.c.user_id == user_id,
                )
            )
            await cleanup.commit()
    finally:
        await engine.dispose()
