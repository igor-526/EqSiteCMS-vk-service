"""Синхронизация состояния привязки с разрешением группы писать пользователю."""

import pytest

from core.services.vk_state import ACTION_ALLOW, ACTION_DENY, VkStateService
from repositories.user_vk import STATE_ACTIVE, STATE_BLOCKED, STATE_PENDING
from tests.fakes import FakeUserVkRepository, FakeVkLogRepository

PEER_ID = 606060


def _service() -> tuple[VkStateService, FakeUserVkRepository, FakeVkLogRepository]:
    bindings = FakeUserVkRepository()
    logs = FakeVkLogRepository()
    return VkStateService(user_vk_repo=bindings, vk_log_repo=logs), bindings, logs


async def test_deny_moves_an_active_binding_to_blocked() -> None:
    service, bindings, logs = _service()
    binding = bindings.seed(state=STATE_ACTIVE, vk_peer_id=PEER_ID)

    assert await service.block(vk_peer_id=PEER_ID) is True

    stored = await bindings.get_by_id(record_id=binding["id"])
    assert stored is not None and stored["state"] == STATE_BLOCKED
    assert logs.statuses_for(ACTION_DENY) == ["success"]


async def test_allow_returns_a_blocked_binding_to_active() -> None:
    service, bindings, logs = _service()
    binding = bindings.seed(state=STATE_BLOCKED, vk_peer_id=PEER_ID)

    assert await service.unblock(vk_peer_id=PEER_ID) is True

    stored = await bindings.get_by_id(record_id=binding["id"])
    assert stored is not None and stored["state"] == STATE_ACTIVE
    assert logs.statuses_for(ACTION_ALLOW) == ["success"]


@pytest.mark.parametrize("action", ["block", "unblock"])
async def test_events_without_a_binding_are_journalled_without_raising(action: str) -> None:
    service, _, logs = _service()

    assert await getattr(service, action)(vk_peer_id=PEER_ID) is False

    journalled = ACTION_DENY if action == "block" else ACTION_ALLOW
    assert logs.statuses_for(journalled) == ["no_binding"]


async def test_deleted_binding_is_treated_as_absent() -> None:
    service, bindings, logs = _service()
    binding = bindings.seed(state=STATE_ACTIVE, vk_peer_id=PEER_ID)
    await bindings.soft_delete(user_id=binding["user_id"])

    assert await service.block(vk_peer_id=PEER_ID) is False
    assert logs.statuses_for(ACTION_DENY) == ["no_binding"]


async def test_previous_state_is_recorded_in_the_journal() -> None:
    service, bindings, logs = _service()
    bindings.seed(state=STATE_PENDING, vk_peer_id=PEER_ID)

    await service.block(vk_peer_id=PEER_ID)

    entry = logs.entries[-1]
    assert entry["details"]["previous_state"] == STATE_PENDING
    assert entry["details"]["state"] == STATE_BLOCKED
