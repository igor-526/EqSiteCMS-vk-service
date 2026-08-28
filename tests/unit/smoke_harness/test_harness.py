import json
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from core.schemas.messaging import NotificationCommandSendVkData
from core.services import VkDeliveryRetryableError, VkNotificationDeliveryService
from repositories.protocols import UserVkRepositoryProtocol
from smoke_harness.__main__ import sanitized_result
from smoke_harness.config import SmokeHarnessConfig, SmokeHarnessGuardError, SmokeScenario
from smoke_harness.messenger import ScriptedPlan, ScriptedVkMessenger
from smoke_harness.runner import (
    CleanupStage,
    RecipientEvidence,
    SmokeHarnessResult,
    _cleanup_database,
    _delete_run_stream,
    _finish_with_cleanup,
    _validate_fixture_state,
    _wait_for_failed_redelivery_exhaustion,
)
from smoke_harness.topology import SmokeTopology


def valid_env() -> dict[str, str]:
    target = uuid4()
    return {
        "EQSITECMS_SMOKE_HARNESS": "1",
        "EQSITECMS_ENVIRONMENT": "local",
        "EQSITECMS_SMOKE_RUN_ID": str(uuid4()),
        "EQSITECMS_SMOKE_EVENT_ID": str(uuid4()),
        "EQSITECMS_SMOKE_CALLBACK_REQUEST_ID": str(uuid4()),
        "EQSITECMS_SMOKE_SCENARIO": "repeated-event",
        "EQSITECMS_SMOKE_SYNTHETIC_TARGETS": str(target),
        "EQSITECMS_SMOKE_SCRIPTED_PLANS": json.dumps({str(target): "success"}),
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EQSITECMS_SMOKE_HARNESS", None),
        ("EQSITECMS_ENVIRONMENT", "production"),
        ("EQSITECMS_SMOKE_RUN_ID", "not-a-uuid"),
        ("EQSITECMS_SMOKE_SYNTHETIC_TARGETS", "*"),
    ],
)
def test_ht_vk_01_guard_fails_closed_before_composition(key: str, value: str | None) -> None:
    env = valid_env()
    if value is None:
        env.pop(key)
    else:
        env[key] = value

    with pytest.raises(SmokeHarnessGuardError):
        SmokeHarnessConfig.from_environment(env)


def test_ht_vk_02_topology_is_unique_and_never_uses_production_names() -> None:
    first = SmokeTopology.for_run(uuid4())
    second = SmokeTopology.for_run(uuid4())

    first.assert_isolated()
    assert first != second
    assert first.stream != "NOTIFICATION_COMMANDS"
    assert first.subject != "commands.notification.vk.send"
    assert first.durable != "vk-service-commands-send-vk"


@pytest.mark.asyncio
async def test_ht_vk_03_scripted_provider_never_constructs_vk_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    peer_id = 101
    messenger = ScriptedVkMessenger(peer_plans={peer_id: ScriptedPlan.SUCCESS})

    assert await messenger.send_message(peer_id=peer_id, text="not observed") is True
    assert messenger.attempts_for(peer_id) == 1
    assert "clients.vk" not in type(messenger).__module__


class BindingRepository:
    def __init__(self, peers: dict[UUID, int]) -> None:
        self.peers = peers

    async def get_by_user_ids(self, *, user_ids: list[UUID], state: str | None = None) -> list[dict]:
        return [{"user_id": user_id, "vk_peer_id": self.peers[user_id], "deleted_at": None} for user_id in user_ids]


class DeliveryRepository:
    def __init__(self) -> None:
        self.statuses: dict[tuple[UUID, UUID], str] = {}
        self.attempts: dict[tuple[UUID, UUID], int] = {}

    async def claim_attempt(self, *, event_uuid: UUID, user_id: UUID, vk_peer_id: int) -> dict | None:
        del vk_peer_id
        key = (event_uuid, user_id)
        if self.statuses.get(key) == "SENT":
            return None
        self.attempts[key] = self.attempts.get(key, 0) + 1
        self.statuses[key] = "PENDING"
        return {"status": "PENDING"}

    async def mark_sent(self, *, event_uuid: UUID, user_id: UUID) -> dict:
        self.statuses[(event_uuid, user_id)] = "SENT"
        return {"status": "SENT"}

    async def mark_failed(self, *, event_uuid: UUID, user_id: UUID, error_category: str) -> dict:
        del error_category
        self.statuses[(event_uuid, user_id)] = "FAILED"
        return {"status": "FAILED"}


def command(*user_ids: UUID) -> NotificationCommandSendVkData:
    return NotificationCommandSendVkData(
        occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
        event_uuid=uuid4(),
        callback_request_id=uuid4(),
        user_ids=list(user_ids),
        text="synthetic",
    )


@pytest.mark.asyncio
async def test_ht_vk_04_partial_retry_only_retries_failed_recipient() -> None:
    successful, flaky = uuid4(), uuid4()
    peers = {successful: 101, flaky: 202}
    deliveries = DeliveryRepository()
    messenger = ScriptedVkMessenger(peer_plans={101: ScriptedPlan.SUCCESS, 202: ScriptedPlan.FAIL_FIRST_THEN_SUCCESS})
    service = VkNotificationDeliveryService(
        binding_repository=cast(UserVkRepositoryProtocol, BindingRepository(peers)),
        delivery_repository=deliveries,
        messenger=messenger,
    )
    payload = command(successful, flaky)

    with pytest.raises(VkDeliveryRetryableError):
        await service.deliver(command=payload)
    await service.deliver(command=payload)

    assert messenger.attempts_for(101) == 1
    assert messenger.attempts_for(202) == 2
    assert deliveries.statuses[(payload.event_uuid, successful)] == "SENT"
    assert deliveries.statuses[(payload.event_uuid, flaky)] == "SENT"


@pytest.mark.asyncio
async def test_ht_vk_05_duplicate_delivery_does_not_resend_sent_recipient() -> None:
    user_id = uuid4()
    deliveries = DeliveryRepository()
    messenger = ScriptedVkMessenger(peer_plans={101: ScriptedPlan.SUCCESS})
    service = VkNotificationDeliveryService(
        binding_repository=cast(UserVkRepositoryProtocol, BindingRepository({user_id: 101})),
        delivery_repository=deliveries,
        messenger=messenger,
    )
    payload = command(user_id)

    await service.deliver(command=payload)
    await service.deliver(command=payload)

    assert messenger.attempts_for(101) == 1
    assert deliveries.attempts[(payload.event_uuid, user_id)] == 1


def test_ht_vk_06_guard_and_topology_are_safe_for_sanitized_observability() -> None:
    env = valid_env()
    config = SmokeHarnessConfig.from_environment(env)
    rendered = repr(config.topology)

    assert env["EQSITECMS_SMOKE_SYNTHETIC_TARGETS"] not in rendered
    assert "token" not in rendered.lower()
    assert "peer" not in rendered.lower()
    assert "Synthetic local smoke notification" not in rendered


@pytest.mark.asyncio
async def test_ht_vk_06_runner_cleanup_executes_every_stage_on_success() -> None:
    calls: list[str] = []

    def stage(name: str) -> CleanupStage:
        async def action() -> None:
            calls.append(name)

        return CleanupStage(name, action)

    await _finish_with_cleanup(primary_error=None, stages=[stage("consumer"), stage("stream"), stage("fixtures")])

    assert calls == ["consumer", "stream", "fixtures"]


@pytest.mark.asyncio
async def test_ht_vk_06_runner_preserves_body_error_and_runs_all_cleanup() -> None:
    calls: list[str] = []
    primary = RuntimeError("body failed")

    async def record(name: str) -> None:
        calls.append(name)

    with pytest.raises(RuntimeError, match="body failed") as caught:
        await _finish_with_cleanup(
            primary_error=primary,
            stages=[
                CleanupStage("consumer", lambda: record("consumer")),
                CleanupStage("stream", lambda: record("stream")),
                CleanupStage("fixtures", lambda: record("fixtures")),
            ],
        )

    assert caught.value is primary
    assert calls == ["consumer", "stream", "fixtures"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_index", range(5))
async def test_ht_vk_06_cleanup_failure_does_not_skip_later_stages(failing_index: int) -> None:
    calls: list[int] = []

    def stage(index: int) -> CleanupStage:
        async def action() -> None:
            calls.append(index)
            if index == failing_index:
                raise RuntimeError(f"cleanup-{index}")

        return CleanupStage(f"stage-{index}", action)

    with pytest.raises(BaseExceptionGroup) as caught:
        await _finish_with_cleanup(primary_error=ValueError("primary"), stages=[stage(index) for index in range(5)])

    assert calls == list(range(5))
    assert caught.value.exceptions[0].args == ("primary",)
    assert caught.value.exceptions[1].args == (f"cleanup-{failing_index}",)
    assert caught.value.exceptions[1].__notes__ == [f"cleanup stage: stage-{failing_index}"]


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.committed = False

    async def execute(self, statement: Any) -> None:
        self.statements.append(statement)

    async def commit(self) -> None:
        self.committed = True


class SessionContext:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session

    async def __aenter__(self) -> RecordingSession:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_ht_vk_06_database_cleanup_targets_only_exact_event_and_users() -> None:
    event_uuid = uuid4()
    targets = (uuid4(), uuid4())
    unrelated = uuid4()
    session = RecordingSession()

    await _cleanup_database(
        session_factory=cast(Any, lambda: SessionContext(session)),
        event_uuid=event_uuid,
        target_user_ids=targets,
    )

    rendered = [str(statement.compile(compile_kwargs={"literal_binds": True})) for statement in session.statements]
    assert event_uuid.hex in rendered[0]
    assert all(target.hex in rendered[1] for target in targets)
    assert unrelated.hex not in " ".join(rendered)
    assert session.committed is True


@pytest.mark.asyncio
async def test_ht_vk_06_topology_cleanup_deletes_only_run_scoped_stream() -> None:
    topology = SmokeTopology.for_run(uuid4())
    jetstream = type("JetStream", (), {"delete_stream": AsyncMock()})()
    client = type("Client", (), {"jetstream": jetstream})()

    await _delete_run_stream(client=cast(Any, client), stream_created=True, stream=topology.stream)

    jetstream.delete_stream.assert_awaited_once_with(topology.stream)
    assert topology.stream != "NOTIFICATION_COMMANDS"


@pytest.mark.parametrize("scenario", list(SmokeScenario))
def test_scenario_mode_guard_accepts_only_exact_supported_modes(scenario: SmokeScenario) -> None:
    env = valid_env()
    env["EQSITECMS_SMOKE_SCENARIO"] = scenario.value
    if scenario is SmokeScenario.PARTIAL_RETRY:
        targets = (uuid4(), uuid4())
        env["EQSITECMS_SMOKE_SYNTHETIC_TARGETS"] = ",".join(map(str, targets))
        env["EQSITECMS_SMOKE_SCRIPTED_PLANS"] = json.dumps(
            {str(targets[0]): "success", str(targets[1]): "fail-first-then-success"}
        )
    elif scenario is SmokeScenario.DELIVERY_FAILURE:
        target = env["EQSITECMS_SMOKE_SYNTHETIC_TARGETS"]
        env["EQSITECMS_SMOKE_SCRIPTED_PLANS"] = json.dumps({target: "fail-always"})

    assert SmokeHarnessConfig.from_environment(env).scenario is scenario


def test_scenario_mode_guard_rejects_unknown_mode_and_invalid_partial_plan() -> None:
    env = valid_env()
    env["EQSITECMS_SMOKE_SCENARIO"] = "all"
    with pytest.raises(SmokeHarnessGuardError):
        SmokeHarnessConfig.from_environment(env)

    env = valid_env()
    env["EQSITECMS_SMOKE_SCENARIO"] = "partial-retry"
    with pytest.raises(SmokeHarnessGuardError):
        SmokeHarnessConfig.from_environment(env)


def test_delivery_failure_requires_one_exact_fail_always_target() -> None:
    env = valid_env()
    target = env["EQSITECMS_SMOKE_SYNTHETIC_TARGETS"]
    env["EQSITECMS_SMOKE_SCENARIO"] = "delivery-failure"
    env["EQSITECMS_SMOKE_SCRIPTED_PLANS"] = json.dumps({target: "fail-always"})

    config = SmokeHarnessConfig.from_environment(env)

    assert config.scenario is SmokeScenario.DELIVERY_FAILURE
    assert config.plans[UUID(target)] is ScriptedPlan.FAIL_ALWAYS


@pytest.mark.parametrize("plan", ["success", "fail-first-then-success"])
def test_delivery_failure_rejects_non_fail_always_plan(plan: str) -> None:
    env = valid_env()
    target = env["EQSITECMS_SMOKE_SYNTHETIC_TARGETS"]
    env["EQSITECMS_SMOKE_SCENARIO"] = "delivery-failure"
    env["EQSITECMS_SMOKE_SCRIPTED_PLANS"] = json.dumps({target: plan})

    with pytest.raises(SmokeHarnessGuardError, match="one fail-always target"):
        SmokeHarnessConfig.from_environment(env)


class FailedDeliveryResult:
    def mappings(self) -> FailedDeliveryResult:
        return self

    def one_or_none(self) -> dict[str, object]:
        return {"status": "FAILED", "attempts": 3}


class FailedDeliverySession:
    async def execute(self, _statement: object) -> FailedDeliveryResult:
        return FailedDeliveryResult()


@pytest.mark.asyncio
async def test_delivery_failure_waits_for_exact_max_deliver_without_extra_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = valid_env()
    target = env["EQSITECMS_SMOKE_SYNTHETIC_TARGETS"]
    env["EQSITECMS_SMOKE_SCENARIO"] = "delivery-failure"
    env["EQSITECMS_SMOKE_SCRIPTED_PLANS"] = json.dumps({target: "fail-always"})
    config = SmokeHarnessConfig.from_environment(env)
    messenger = ScriptedVkMessenger(peer_plans={101: ScriptedPlan.FAIL_ALWAYS})
    for _ in range(3):
        assert await messenger.send_message(peer_id=101, text="not observed") is False
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("smoke_harness.runner.asyncio.sleep", record_sleep)
    await _wait_for_failed_redelivery_exhaustion(
        session_factory=cast(Any, lambda: SessionContext(cast(Any, FailedDeliverySession()))),
        config=config,
        messenger=messenger,
        peer_by_user={UUID(target): 101},
        max_deliver=3,
        ack_wait_seconds=1,
    )

    assert messenger.attempts_for(101) == 3
    assert sleeps == [1.25]


@pytest.mark.parametrize(
    ("scenario", "rows"),
    [
        (SmokeScenario.UNKNOWN_USER, []),
        (SmokeScenario.PENDING_BINDING, [{"state": "PENDING", "deleted_at": None}]),
        (SmokeScenario.BLOCKED_BINDING, [{"state": "BLOCKED", "deleted_at": None}]),
        (SmokeScenario.SOFT_DELETED_BINDING, [{"state": "ACTIVE", "deleted_at": datetime.now(UTC)}]),
        (SmokeScenario.REPEATED_EVENT, [{"state": "ACTIVE", "deleted_at": None}]),
        (SmokeScenario.CONCURRENT_DUPLICATE, [{"state": "ACTIVE", "deleted_at": None}]),
    ],
)
def test_scenario_fixture_guard_matches_exact_state(scenario: SmokeScenario, rows: list[dict]) -> None:
    env = valid_env()
    env["EQSITECMS_SMOKE_SCENARIO"] = scenario.value
    config = SmokeHarnessConfig.from_environment(env)

    _validate_fixture_state(config=config, rows=rows)
    with pytest.raises(RuntimeError):
        _validate_fixture_state(config=config, rows=[] if rows else [{"state": "ACTIVE", "deleted_at": None}])


def test_sanitized_recipient_evidence_contains_no_identifiers_or_payload() -> None:
    secret_values = [str(uuid4()), "987654321", "private callback text", "vk-token"]
    payload = sanitized_result(
        SmokeHarnessResult(
            scenario="partial-retry",
            recipients=(
                RecipientEvidence(recipient=1, status="SENT", attempts=1),
                RecipientEvidence(recipient=2, status="SENT", attempts=2),
            ),
            redeliveries=1,
        )
    )
    rendered = json.dumps(payload)

    assert payload["recipients"] == [
        {"recipient": 1, "status": "SENT", "attempts": 1},
        {"recipient": 2, "status": "SENT", "attempts": 2},
    ]
    assert all(secret not in rendered for secret in secret_values)
