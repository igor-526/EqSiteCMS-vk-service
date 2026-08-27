"""Сверка контрольной строки: успех, отказы, идемпотентность, лимит попыток."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core.exceptions import ConflictError, GoneError, NotFoundError, RateLimitedError
from core.services.vk_confirmation import ACTION_CONFIRMATION, VkConfirmationService
from repositories.user_vk import STATE_ACTIVE, STATE_PENDING
from tests.fakes import FakeUserVkRepository, FakeVkConfirmationRepository, FakeVkLogRepository

MAX_ATTEMPTS = 5
WINDOW_MINUTES = 10
PEER_ID = 424242


def _service(
    bindings: FakeUserVkRepository,
    confirmations: FakeVkConfirmationRepository,
    logs: FakeVkLogRepository,
) -> VkConfirmationService:
    return VkConfirmationService(
        confirmation_repo=confirmations,
        user_vk_repo=bindings,
        vk_log_repo=logs,
        max_attempts=MAX_ATTEMPTS,
        attempt_window_minutes=WINDOW_MINUTES,
    )


async def _pending_setup(code: str = "ABC23XYZ", *, expires_in_minutes: int = 30):
    bindings = FakeUserVkRepository()
    confirmations = FakeVkConfirmationRepository()
    logs = FakeVkLogRepository()
    binding = bindings.seed(state=STATE_PENDING)
    confirmation = await confirmations.create(
        user_vk_id=binding["id"],
        code=code,
        expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
    )
    return _service(bindings, confirmations, logs), bindings, confirmations, logs, binding, confirmation


async def test_valid_code_activates_the_binding() -> None:
    service, bindings, confirmations, logs, binding, confirmation = await _pending_setup()

    result = await service.confirm(
        code="ABC23XYZ",
        vk_peer_id=PEER_ID,
        vk_screen_name="durov",
        vk_display_name="Pavel",
    )

    stored = await bindings.get_by_id(record_id=binding["id"])
    assert result.status == "confirmed"
    assert result.user_id == binding["user_id"]
    assert stored is not None
    assert (stored["state"], stored["vk_peer_id"]) == (STATE_ACTIVE, PEER_ID)
    assert (stored["vk_screen_name"], stored["vk_display_name"]) == ("durov", "Pavel")
    assert confirmations.rows[0]["used_at"] is not None
    assert logs.statuses_for(ACTION_CONFIRMATION) == ["success"]


async def test_code_matching_ignores_case_and_surrounding_spaces() -> None:
    service, bindings, _, _, binding, _ = await _pending_setup()

    result = await service.confirm(code="  abc23xyz  ", vk_peer_id=PEER_ID)

    stored = await bindings.get_by_id(record_id=binding["id"])
    assert result.status == "confirmed"
    assert stored is not None and stored["state"] == STATE_ACTIVE


async def test_unknown_code_is_rejected_and_journalled() -> None:
    service, _, _, logs, _, _ = await _pending_setup()

    with pytest.raises(NotFoundError):
        await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)

    assert logs.statuses_for(ACTION_CONFIRMATION) == ["not_found"]


async def test_used_code_of_another_peer_is_rejected() -> None:
    service, bindings, confirmations, logs, binding, _ = await _pending_setup()
    await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    with pytest.raises(ConflictError, match="уже использован"):
        await service.confirm(code="ABC23XYZ", vk_peer_id=999999)

    assert logs.statuses_for(ACTION_CONFIRMATION) == ["success", "used"]
    stored = await bindings.get_by_id(record_id=binding["id"])
    assert stored is not None and stored["vk_peer_id"] == PEER_ID
    assert len(confirmations.rows) == 1


async def test_expired_code_is_rejected_without_mutation() -> None:
    service, bindings, _, logs, binding, _ = await _pending_setup(expires_in_minutes=-1)

    with pytest.raises(GoneError):
        await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    stored = await bindings.get_by_id(record_id=binding["id"])
    assert stored is not None and stored["state"] == STATE_PENDING
    assert logs.statuses_for(ACTION_CONFIRMATION) == ["expired"]


async def test_peer_already_linked_to_another_user_is_rejected() -> None:
    bindings = FakeUserVkRepository()
    confirmations = FakeVkConfirmationRepository()
    logs = FakeVkLogRepository()
    bindings.seed(state=STATE_ACTIVE, vk_peer_id=PEER_ID)
    victim = bindings.seed(state=STATE_PENDING)
    await confirmations.create(
        user_vk_id=victim["id"],
        code="TAKEN123",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    service = _service(bindings, confirmations, logs)

    with pytest.raises(ConflictError, match="другому пользователю"):
        await service.confirm(code="TAKEN123", vk_peer_id=PEER_ID)

    assert confirmations.rows[0]["used_at"] is None
    stored = await bindings.get_by_id(record_id=victim["id"])
    assert stored is not None and stored["state"] == STATE_PENDING
    assert logs.statuses_for(ACTION_CONFIRMATION) == ["peer_conflict"]


async def test_repeated_delivery_of_the_same_confirmation_is_idempotent() -> None:
    service, bindings, confirmations, logs, binding, _ = await _pending_setup()
    first = await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    second = await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    assert (first.status, second.status) == ("confirmed", "already_confirmed")
    assert second.user_id == binding["user_id"]
    assert len([row for row in bindings.rows.values() if row["deleted_at"] is None]) == 1
    assert len(confirmations.rows) == 1
    assert logs.statuses_for(ACTION_CONFIRMATION) == ["success", "already_confirmed"]


async def test_missing_binding_behind_a_valid_code_is_reported_as_error() -> None:
    bindings = FakeUserVkRepository()
    confirmations = FakeVkConfirmationRepository()
    logs = FakeVkLogRepository()
    await confirmations.create(
        user_vk_id=uuid4(),
        code="ORPHAN12",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    service = _service(bindings, confirmations, logs)

    with pytest.raises(NotFoundError, match="Привязка"):
        await service.confirm(code="ORPHAN12", vk_peer_id=PEER_ID)

    assert logs.statuses_for(ACTION_CONFIRMATION) == ["error"]


async def test_attempts_below_the_limit_are_processed() -> None:
    service, _, _, logs, _, _ = await _pending_setup()

    for _ in range(MAX_ATTEMPTS - 1):
        with pytest.raises(NotFoundError):
            await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)

    result = await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)
    assert result.status == "confirmed"
    assert logs.statuses_for(ACTION_CONFIRMATION).count("rate_limited") == 0


async def test_limit_reached_stops_code_lookup() -> None:
    service, _, confirmations, logs, _, _ = await _pending_setup()
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(NotFoundError):
            await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)

    with pytest.raises(RateLimitedError):
        await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    assert confirmations.rows[0]["used_at"] is None
    assert logs.statuses_for(ACTION_CONFIRMATION)[-1] == "rate_limited"


async def test_limit_is_scoped_to_a_single_peer() -> None:
    service, _, _, _, _, _ = await _pending_setup()
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(NotFoundError):
            await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)

    result = await service.confirm(code="ABC23XYZ", vk_peer_id=111111)
    assert result.status == "confirmed"


async def test_attempts_outside_the_window_do_not_count() -> None:
    service, _, _, logs, _, _ = await _pending_setup()
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(NotFoundError):
            await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)
    stale = datetime.now(UTC) - timedelta(minutes=WINDOW_MINUTES + 1)
    for entry in logs.entries:
        entry["created_at"] = stale

    result = await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)
    assert result.status == "confirmed"


async def test_every_outcome_is_journalled_with_the_peer_id() -> None:
    service, _, _, logs, _, _ = await _pending_setup()

    with pytest.raises(NotFoundError):
        await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)
    await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    peers = {entry["details"].get("vk_peer_id") for entry in logs.entries}
    assert peers == {str(PEER_ID)}
    assert len(logs.entries) == 2


async def test_full_code_is_never_journalled() -> None:
    service, _, _, logs, _, _ = await _pending_setup()

    with pytest.raises(NotFoundError):
        await service.confirm(code="NOSUCH11", vk_peer_id=PEER_ID)
    await service.confirm(code="ABC23XYZ", vk_peer_id=PEER_ID)

    serialized = " ".join(str(entry["details"]) for entry in logs.entries)
    assert "ABC23XYZ" not in serialized
    assert "NOSUCH11" not in serialized
    assert "AB***(len=8)" in serialized
