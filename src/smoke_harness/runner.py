import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clients.nats import NatsJetstreamClient
from clients.nats.consumers import NotificationCommandsSendVkConsumer
from clients.nats.handlers import NotificationCommandsSendVkHandler
from core.schemas.messaging import NotificationCommandSendVkData
from models.user_vk import user_vks
from models.vk_notification_delivery import vk_notification_deliveries
from settings import NatsSettings, Settings
from smoke_harness.config import SmokeHarnessConfig, SmokeScenario
from smoke_harness.messenger import ScriptedVkMessenger

SMOKE_MAX_DELIVER = 3


@dataclass(frozen=True)
class RecipientEvidence:
    recipient: int
    status: str
    attempts: int


@dataclass(frozen=True)
class SmokeHarnessResult:
    scenario: str
    recipients: tuple[RecipientEvidence, ...]
    redeliveries: int


@dataclass(frozen=True)
class CleanupStage:
    name: str
    action: Callable[[], Awaitable[None]]


async def run(config: SmokeHarnessConfig) -> SmokeHarnessResult:
    topology = config.topology
    topology.assert_isolated()
    db_settings = Settings()
    nats_settings = NatsSettings(
        NATS_STREAM_NOTIFICATION_COMMANDS=topology.stream,
        NATS_SUBJECT_NOTIFICATION_COMMANDS_SEND_VK=topology.subject,
        NATS_CONSUMER_NOTIFICATION_COMMANDS_SEND_VK=topology.durable,
        NATS_CONSUMER_ACK_WAIT_SECONDS=1,
        NATS_CONSUMER_MAX_DELIVER=SMOKE_MAX_DELIVER,
        NATS_CONSUMER_FETCH_BATCH_SIZE=10,
        NATS_CONSUMER_FETCH_TIMEOUT_SECONDS=0.25,
    )
    engine = create_async_engine(db_settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    client = NatsJetstreamClient(nats_settings)
    consumer: NotificationCommandsSendVkConsumer | None = None
    stream_created = False
    result: SmokeHarnessResult | None = None
    primary_error: BaseException | None = None
    try:
        async with session_factory() as session:
            binding_rows = (
                (
                    await session.execute(
                        select(
                            user_vks.c.user_id,
                            user_vks.c.vk_peer_id,
                            user_vks.c.state,
                            user_vks.c.deleted_at,
                        ).where(
                            user_vks.c.user_id.in_(config.target_user_ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
        _validate_fixture_state(config=config, rows=binding_rows)
        peer_by_user = {
            row["user_id"]: int(row["vk_peer_id"])
            for row in binding_rows
            if row["state"] == "ACTIVE" and row["deleted_at"] is None and row["vk_peer_id"] is not None
        }
        messenger = ScriptedVkMessenger(
            peer_plans={peer_id: config.plans[user_id] for user_id, peer_id in peer_by_user.items()}
        )
        handler = NotificationCommandsSendVkHandler(session_factory=session_factory, messenger=messenger)

        await client.connect()
        await client.jetstream.add_stream(
            StreamConfig(
                name=topology.stream,
                subjects=[topology.subject],
                retention=RetentionPolicy.WORK_QUEUE,
                storage=StorageType.MEMORY,
                max_age=300,
            )
        )
        stream_created = True
        await client.setup_consumers()
        consumer = NotificationCommandsSendVkConsumer(client=client, settings=nats_settings, handler=handler)
        await consumer.start()
        command = NotificationCommandSendVkData(
            occurred_at=datetime.now(UTC),
            event_uuid=config.event_uuid,
            callback_request_id=config.callback_request_id,
            user_ids=list(config.target_user_ids),
            text="Synthetic local smoke notification",
        )
        payload = b"{" if config.scenario is SmokeScenario.MALFORMED else command.model_dump_json().encode()

        async def publish() -> None:
            await client.publish(
                subject=topology.subject,
                payload=payload,
                headers={"Nats-Msg-Id": str(config.callback_request_id)},
            )

        if config.scenario is SmokeScenario.CONCURRENT_DUPLICATE:
            await asyncio.gather(publish(), publish())
        else:
            await publish()

        if config.scenario is SmokeScenario.DELIVERY_FAILURE:
            await _wait_for_failed_redelivery_exhaustion(
                session_factory=session_factory,
                config=config,
                messenger=messenger,
                peer_by_user=peer_by_user,
                max_deliver=SMOKE_MAX_DELIVER,
                ack_wait_seconds=nats_settings.nats_consumer_ack_wait_seconds,
            )
        elif config.scenario in {
            SmokeScenario.PARTIAL_RETRY,
            SmokeScenario.REPEATED_EVENT,
            SmokeScenario.CONCURRENT_DUPLICATE,
        }:
            await _wait_for_sent_rows(session_factory, config)
        else:
            await asyncio.sleep(1.25 if config.scenario is SmokeScenario.MALFORMED else 0.5)

        if config.scenario is SmokeScenario.REPEATED_EVENT:
            await publish()
            await asyncio.sleep(0.5)
        result = await _collect_result(
            session_factory=session_factory,
            config=config,
            messenger=messenger,
            peer_by_user=peer_by_user,
            client=client,
        )
    except BaseException as exc:
        primary_error = exc

    async def stop_consumer() -> None:
        if consumer is not None:
            await consumer.stop()

    async def delete_stream() -> None:
        await _delete_run_stream(client=client, stream_created=stream_created, stream=topology.stream)

    cleanup_stages = (
        CleanupStage("consumer.stop", stop_consumer),
        CleanupStage("jetstream.delete_run_stream", delete_stream),
        CleanupStage(
            "database.delete_exact_fixtures",
            lambda: _cleanup_database(
                session_factory=session_factory,
                event_uuid=config.event_uuid,
                target_user_ids=config.target_user_ids,
            ),
        ),
        CleanupStage("nats.close", client.close),
        CleanupStage("database.dispose", engine.dispose),
    )
    await _finish_with_cleanup(primary_error=primary_error, stages=cleanup_stages)
    if result is None:
        raise RuntimeError("smoke harness completed without a result")
    return result


async def _finish_with_cleanup(*, primary_error: BaseException | None, stages: Sequence[CleanupStage]) -> None:
    cleanup_errors: list[BaseException] = []
    for stage in stages:
        try:
            await stage.action()
        except BaseException as exc:
            exc.add_note(f"cleanup stage: {stage.name}")
            cleanup_errors.append(exc)

    if primary_error is not None and cleanup_errors:
        raise BaseExceptionGroup("smoke harness body and cleanup failed", [primary_error, *cleanup_errors])
    if cleanup_errors:
        raise BaseExceptionGroup("smoke harness cleanup failed", cleanup_errors)
    if primary_error is not None:
        raise primary_error.with_traceback(primary_error.__traceback__)


async def _cleanup_database(
    *, session_factory: async_sessionmaker, event_uuid: UUID, target_user_ids: tuple[UUID, ...]
) -> None:
    async with session_factory() as session:
        await session.execute(
            delete(vk_notification_deliveries).where(vk_notification_deliveries.c.event_uuid == event_uuid)
        )
        await session.execute(delete(user_vks).where(user_vks.c.user_id.in_(target_user_ids)))
        await session.commit()


async def _delete_run_stream(*, client: NatsJetstreamClient, stream_created: bool, stream: str) -> None:
    if stream_created:
        await client.jetstream.delete_stream(stream)


async def _wait_for_sent_rows(session_factory: async_sessionmaker, config: SmokeHarnessConfig) -> None:
    for _ in range(80):
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(vk_notification_deliveries.c.status).where(
                            vk_notification_deliveries.c.event_uuid == config.event_uuid,
                            vk_notification_deliveries.c.user_id.in_(config.target_user_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
        if len(rows) == len(config.target_user_ids) and rows.count("SENT") == len(rows):
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("smoke harness did not reach a bounded terminal state")


async def _wait_for_failed_redelivery_exhaustion(
    *,
    session_factory: async_sessionmaker,
    config: SmokeHarnessConfig,
    messenger: ScriptedVkMessenger,
    peer_by_user: dict[UUID, int],
    max_deliver: int,
    ack_wait_seconds: float,
) -> None:
    user_id = config.target_user_ids[0]
    peer_id = peer_by_user[user_id]
    for _ in range(80):
        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(
                            vk_notification_deliveries.c.status,
                            vk_notification_deliveries.c.attempts,
                        ).where(
                            vk_notification_deliveries.c.event_uuid == config.event_uuid,
                            vk_notification_deliveries.c.user_id == user_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        exhausted = row is not None and all(
            (
                row["status"] == "FAILED",
                row["attempts"] == max_deliver,
                messenger.attempts_for(peer_id) == max_deliver,
            )
        )
        if exhausted:
            await asyncio.sleep(ack_wait_seconds + 0.25)
            if messenger.attempts_for(peer_id) != max_deliver:
                raise RuntimeError("smoke harness exceeded bounded max-deliver")
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("smoke harness did not exhaust bounded failed redelivery")


def _validate_fixture_state(*, config: SmokeHarnessConfig, rows: Sequence) -> None:
    expected = {
        SmokeScenario.UNKNOWN_USER: None,
        SmokeScenario.PENDING_BINDING: "PENDING",
        SmokeScenario.BLOCKED_BINDING: "BLOCKED",
        SmokeScenario.SOFT_DELETED_BINDING: "DELETED",
    }.get(config.scenario, "ACTIVE")
    if config.scenario is SmokeScenario.MALFORMED:
        return
    if expected is None:
        valid = not rows
    elif expected == "DELETED":
        valid = len(rows) == len(config.target_user_ids) and all(row["deleted_at"] is not None for row in rows)
    else:
        valid = len(rows) == len(config.target_user_ids) and all(
            row["state"] == expected and row["deleted_at"] is None for row in rows
        )
    if not valid:
        raise RuntimeError("synthetic fixtures do not match the guarded scenario")


async def _collect_result(
    *,
    session_factory: async_sessionmaker,
    config: SmokeHarnessConfig,
    messenger: ScriptedVkMessenger,
    peer_by_user: dict[UUID, int],
    client: NatsJetstreamClient,
) -> SmokeHarnessResult:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(
                        vk_notification_deliveries.c.user_id,
                        vk_notification_deliveries.c.status,
                        vk_notification_deliveries.c.attempts,
                    ).where(
                        vk_notification_deliveries.c.event_uuid == config.event_uuid,
                        vk_notification_deliveries.c.user_id.in_(config.target_user_ids),
                    )
                )
            )
            .mappings()
            .all()
        )
    ledger = {row["user_id"]: row for row in rows}
    evidence = tuple(
        RecipientEvidence(
            recipient=index,
            status=str(ledger[user_id]["status"]) if user_id in ledger else "SKIPPED",
            attempts=messenger.attempts_for(peer_by_user[user_id]) if user_id in peer_by_user else 0,
        )
        for index, user_id in enumerate(config.target_user_ids, start=1)
    )
    info = await client.jetstream.consumer_info(config.topology.stream, config.topology.durable)
    return SmokeHarnessResult(
        scenario=config.scenario.value,
        recipients=evidence,
        redeliveries=int(getattr(info, "num_redelivered", 0)),
    )
