"""Выдача контрольной строки и отвязка аккаунта VK."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core.exceptions import ConflictError
from core.services.vk_binding import ACTION_ISSUE, ACTION_UNLINK, VkBindingService
from repositories.user_vk import STATE_ACTIVE, STATE_BLOCKED, STATE_PENDING
from tests.fakes import (
    FakeUserVkRepository,
    FakeVkConfirmationRepository,
    FakeVkLogRepository,
    RecordingMessenger,
)

TTL_MINUTES = 30
CODE_LENGTH = 8


def _service(
    *,
    bindings: FakeUserVkRepository | None = None,
    confirmations: FakeVkConfirmationRepository | None = None,
    logs: FakeVkLogRepository | None = None,
    messenger: RecordingMessenger | None = None,
) -> tuple[VkBindingService, FakeUserVkRepository, FakeVkConfirmationRepository, FakeVkLogRepository]:
    bindings = bindings or FakeUserVkRepository()
    confirmations = confirmations or FakeVkConfirmationRepository()
    logs = logs or FakeVkLogRepository()
    service = VkBindingService(
        user_vk_repo=bindings,
        confirmation_repo=confirmations,
        vk_log_repo=logs,
        ttl_minutes=TTL_MINUTES,
        code_length=CODE_LENGTH,
        messenger=messenger,
    )
    return service, bindings, confirmations, logs


async def test_first_issue_creates_pending_binding_and_returns_code() -> None:
    service, bindings, confirmations, logs = _service()
    user_id = uuid4()

    issued = await service.issue_confirmation(user_id=user_id)

    binding = await bindings.get_by_user_id(user_id=user_id)
    assert binding is not None
    assert binding["state"] == STATE_PENDING
    assert binding["vk_peer_id"] is None
    assert len(issued["code"]) == CODE_LENGTH
    assert issued["state"] == STATE_PENDING
    assert len(confirmations.rows) == 1
    assert logs.statuses_for(ACTION_ISSUE) == ["success"]


async def test_issued_code_expires_after_the_configured_ttl() -> None:
    service, _, confirmations, _ = _service()

    await service.issue_confirmation(user_id=uuid4())

    row = confirmations.rows[0]
    delta = row["expires_at"] - row["created_at"]
    assert timedelta(minutes=TTL_MINUTES) - timedelta(seconds=5) <= delta <= timedelta(minutes=TTL_MINUTES)


async def test_reissue_invalidates_the_previous_code_and_returns_a_new_one() -> None:
    service, _, confirmations, _ = _service()
    user_id = uuid4()

    first = await service.issue_confirmation(user_id=user_id)
    second = await service.issue_confirmation(user_id=user_id)

    assert first["code"] != second["code"]
    previous = await confirmations.get_by_code(code=first["code"])
    current = await confirmations.get_by_code(code=second["code"])
    assert previous is not None and previous["used_at"] is not None
    assert current is not None and current["used_at"] is None


async def test_reissue_reuses_the_same_binding_row() -> None:
    service, bindings, _, _ = _service()
    user_id = uuid4()

    await service.issue_confirmation(user_id=user_id)
    await service.issue_confirmation(user_id=user_id)

    assert len([row for row in bindings.rows.values() if row["deleted_at"] is None]) == 1


async def test_expired_pending_code_does_not_block_a_new_issue() -> None:
    bindings = FakeUserVkRepository()
    confirmations = FakeVkConfirmationRepository()
    user_id = uuid4()
    binding = bindings.seed(user_id=user_id, state=STATE_PENDING)
    await confirmations.create(
        user_vk_id=binding["id"],
        code="EXPIRED1",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    service, _, _, _ = _service(bindings=bindings, confirmations=confirmations)

    issued = await service.issue_confirmation(user_id=user_id)

    assert issued["code"] != "EXPIRED1"
    stale = await confirmations.get_by_code(code="EXPIRED1")
    assert stale is not None and stale["used_at"] is not None


async def test_issue_is_rejected_for_an_already_active_binding() -> None:
    bindings = FakeUserVkRepository()
    user_id = uuid4()
    bindings.seed(user_id=user_id, state=STATE_ACTIVE, vk_peer_id=777)
    service, _, _, logs = _service(bindings=bindings)

    with pytest.raises(ConflictError, match="уже привязан"):
        await service.issue_confirmation(user_id=user_id)

    assert logs.statuses_for(ACTION_ISSUE) == ["conflict_active"]


async def test_issue_is_rejected_when_the_bot_is_blocked() -> None:
    bindings = FakeUserVkRepository()
    user_id = uuid4()
    bindings.seed(user_id=user_id, state=STATE_BLOCKED, vk_peer_id=888)
    service, _, _, logs = _service(bindings=bindings)

    with pytest.raises(ConflictError, match="разрешите сообщения"):
        await service.issue_confirmation(user_id=user_id)

    assert logs.statuses_for(ACTION_ISSUE) == ["conflict_blocked"]


async def test_code_collision_triggers_regeneration() -> None:
    confirmations = FakeVkConfirmationRepository()
    service, _, _, _ = _service(confirmations=confirmations)
    issued_first = await service.issue_confirmation(user_id=uuid4())
    confirmations.reserved_codes.add(issued_first["code"])

    issued = await service.issue_confirmation(user_id=uuid4())

    assert issued["code"] != issued_first["code"]


async def test_persistent_collision_raises_a_domain_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    confirmations = FakeVkConfirmationRepository()
    confirmations.reserved_codes.add("AAAAAAAA")
    service, _, _, _ = _service(confirmations=confirmations)
    monkeypatch.setattr("core.services.vk_binding.generate_code", lambda _length: "AAAAAAAA")

    with pytest.raises(ConflictError, match="уникальный код"):
        await service.issue_confirmation(user_id=uuid4())


async def test_full_code_never_reaches_the_action_journal() -> None:
    service, _, _, logs = _service()

    issued = await service.issue_confirmation(user_id=uuid4())

    journalled = [str(entry["details"]) for entry in logs.entries]
    assert not [entry for entry in journalled if issued["code"] in entry]


async def test_unlink_soft_deletes_and_invalidates_codes() -> None:
    service, bindings, confirmations, logs = _service()
    user_id = uuid4()
    await service.issue_confirmation(user_id=user_id)

    unlinked = await service.unlink(user_id=user_id)

    assert unlinked is True
    assert await bindings.get_by_user_id(user_id=user_id) is None
    assert [row["used_at"] is not None for row in confirmations.rows] == [True]
    assert logs.statuses_for(ACTION_UNLINK) == ["success"]


async def test_unlink_is_idempotent_for_a_missing_binding() -> None:
    service, _, _, logs = _service()

    assert await service.unlink(user_id=uuid4()) is False
    assert logs.statuses_for(ACTION_UNLINK) == ["noop"]


async def test_unlink_notifies_an_active_user_in_vk() -> None:
    bindings = FakeUserVkRepository()
    user_id = uuid4()
    bindings.seed(user_id=user_id, state=STATE_ACTIVE, vk_peer_id=555)
    messenger = RecordingMessenger()
    service, _, _, _ = _service(bindings=bindings, messenger=messenger)

    await service.unlink(user_id=user_id)

    assert [peer for peer, _ in messenger.sent] == [555]


async def test_unlink_does_not_notify_a_blocked_user() -> None:
    bindings = FakeUserVkRepository()
    user_id = uuid4()
    bindings.seed(user_id=user_id, state=STATE_BLOCKED, vk_peer_id=556)
    messenger = RecordingMessenger()
    service, _, _, _ = _service(bindings=bindings, messenger=messenger)

    assert await service.unlink(user_id=user_id) is True
    assert messenger.sent == []


async def test_unlink_survives_a_failing_notification() -> None:
    bindings = FakeUserVkRepository()
    user_id = uuid4()
    bindings.seed(user_id=user_id, state=STATE_ACTIVE, vk_peer_id=557)
    service, _, _, _ = _service(bindings=bindings, messenger=RecordingMessenger(fail=True))

    assert await service.unlink(user_id=user_id) is True
    assert await bindings.get_by_user_id(user_id=user_id) is None


async def test_rebinding_after_unlink_creates_a_new_row() -> None:
    service, bindings, _, _ = _service()
    user_id = uuid4()
    await service.issue_confirmation(user_id=user_id)
    await service.unlink(user_id=user_id)

    issued = await service.issue_confirmation(user_id=user_id)

    assert issued["state"] == STATE_PENDING
    assert len(bindings.rows) == 2


async def test_ensure_binding_recovers_from_a_concurrent_insert() -> None:
    bindings = FakeUserVkRepository()
    user_id = uuid4()
    service, _, _, _ = _service(bindings=bindings)

    created, is_new = await service.ensure_binding(user_id=user_id)
    existing, is_new_again = await service.ensure_binding(user_id=user_id)

    assert (is_new, is_new_again) == (True, False)
    assert created["id"] == existing["id"]


async def test_get_bindings_filters_by_state() -> None:
    bindings = FakeUserVkRepository()
    active = bindings.seed(state=STATE_ACTIVE, vk_peer_id=1)
    pending = bindings.seed(state=STATE_PENDING)
    service, _, _, _ = _service(bindings=bindings)
    user_ids = [active["user_id"], pending["user_id"]]

    assert len(await service.get_bindings(user_ids=user_ids)) == 2
    assert [row["id"] for row in await service.get_bindings(user_ids=user_ids, state=STATE_ACTIVE)] == [active["id"]]
