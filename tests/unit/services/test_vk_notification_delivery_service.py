from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from core.schemas.messaging import NotificationCommandSendVkData
from core.services import VkDeliveryRetryableError, VkNotificationDeliveryService


class BindingRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def get_by_user_ids(self, *, user_ids: list[UUID], state: str | None = None) -> list[dict]:
        return [row for row in self.rows if row["user_id"] in user_ids and row["state"] == state]


class DeliveryRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], dict] = {}

    async def claim_attempt(self, *, event_uuid: UUID, user_id: UUID, vk_peer_id: int) -> dict | None:
        key = (event_uuid, user_id)
        row = self.rows.get(key)
        if row and row["status"] == "SENT":
            return None
        row = {**(row or {}), "event_uuid": event_uuid, "user_id": user_id, "vk_peer_id": vk_peer_id}
        row.update(status="PENDING", attempts=row.get("attempts", 0) + 1, last_error=None)
        self.rows[key] = row
        return row

    async def mark_sent(self, *, event_uuid: UUID, user_id: UUID) -> dict:
        self.rows[(event_uuid, user_id)].update(status="SENT")
        return self.rows[(event_uuid, user_id)]

    async def mark_failed(self, *, event_uuid: UUID, user_id: UUID, error_category: str) -> dict:
        self.rows[(event_uuid, user_id)].update(status="FAILED", last_error=error_category)
        return self.rows[(event_uuid, user_id)]


class Messenger:
    def __init__(self, outcomes: dict[int, list[bool]] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[tuple[int, str]] = []

    async def send_message(self, *, peer_id: int, text: str) -> bool:
        self.calls.append((peer_id, text))
        values = self.outcomes.get(peer_id)
        return values.pop(0) if values else True

    async def get_profile(self, *, peer_id: int):
        return None


def command(*user_ids: UUID) -> NotificationCommandSendVkData:
    return NotificationCommandSendVkData(
        occurred_at=datetime.now(UTC),
        event_uuid=uuid4(),
        callback_request_id=uuid4(),
        user_ids=list(user_ids),
        text="Callback phone: +70000000000",
    )


def binding(user_id: UUID, peer: int, *, state: str = "ACTIVE", deleted: bool = False) -> dict:
    return {
        "user_id": user_id,
        "vk_peer_id": peer,
        "state": state,
        "deleted_at": datetime.now(UTC) if deleted else None,
    }


async def test_ut27_ut32_active_bindings_receive_one_send_each() -> None:
    first, second = uuid4(), uuid4()
    messenger, deliveries = Messenger(), DeliveryRepository()
    service = VkNotificationDeliveryService(
        binding_repository=cast(Any, BindingRepository([binding(first, 101), binding(second, 202)])),
        delivery_repository=cast(Any, deliveries),
        messenger=messenger,
    )
    await service.deliver(command=command(first, second))
    assert messenger.calls == [(101, "Callback phone: +70000000000"), (202, "Callback phone: +70000000000")]
    assert all(row["status"] == "SENT" and row["attempts"] == 1 for row in deliveries.rows.values())


@pytest.mark.parametrize("state,deleted", [("PENDING", False), ("BLOCKED", False), ("ACTIVE", True)])
async def test_ut28_ut29_ut30_ineligible_binding_is_not_sent(state: str, deleted: bool) -> None:
    user_id = uuid4()
    messenger = Messenger()
    service = VkNotificationDeliveryService(
        binding_repository=cast(Any, BindingRepository([binding(user_id, 101, state=state, deleted=deleted)])),
        delivery_repository=cast(Any, DeliveryRepository()),
        messenger=messenger,
    )
    await service.deliver(command=command(user_id))
    assert messenger.calls == []


async def test_ut31_unlisted_binding_is_not_sent() -> None:
    listed, foreign = uuid4(), uuid4()
    messenger = Messenger()
    service = VkNotificationDeliveryService(
        binding_repository=cast(Any, BindingRepository([binding(foreign, 202)])),
        delivery_repository=cast(Any, DeliveryRepository()),
        messenger=messenger,
    )
    await service.deliver(command=command(listed))
    assert messenger.calls == []


async def test_ut33_ut34_success_and_failure_persist_safe_status() -> None:
    ok_user, failed_user = uuid4(), uuid4()
    deliveries = DeliveryRepository()
    messenger = Messenger({202: [False]})
    cmd = command(ok_user, failed_user)
    service = VkNotificationDeliveryService(
        binding_repository=cast(Any, BindingRepository([binding(ok_user, 101), binding(failed_user, 202)])),
        delivery_repository=cast(Any, deliveries),
        messenger=messenger,
    )
    with pytest.raises(VkDeliveryRetryableError):
        await service.deliver(command=cmd)
    assert deliveries.rows[(cmd.event_uuid, ok_user)]["status"] == "SENT"
    assert deliveries.rows[(cmd.event_uuid, failed_user)] == {
        "event_uuid": cmd.event_uuid,
        "user_id": failed_user,
        "vk_peer_id": 202,
        "status": "FAILED",
        "attempts": 1,
        "last_error": "VK_API_SEND_FAILED",
    }


async def test_ut35_ut36_ut38_partial_redelivery_only_retries_failed() -> None:
    ok_user, retry_user = uuid4(), uuid4()
    deliveries = DeliveryRepository()
    messenger = Messenger({202: [False, True]})
    cmd = command(ok_user, retry_user)
    service = VkNotificationDeliveryService(
        binding_repository=cast(Any, BindingRepository([binding(ok_user, 101), binding(retry_user, 202)])),
        delivery_repository=cast(Any, deliveries),
        messenger=messenger,
    )
    with pytest.raises(VkDeliveryRetryableError):
        await service.deliver(command=cmd)
    await service.deliver(command=cmd)
    assert messenger.calls == [
        (101, "Callback phone: +70000000000"),
        (202, "Callback phone: +70000000000"),
        (202, "Callback phone: +70000000000"),
    ]
    assert deliveries.rows[(cmd.event_uuid, ok_user)]["attempts"] == 1
    assert deliveries.rows[(cmd.event_uuid, retry_user)]["attempts"] == 2


async def test_ut40_ledger_has_no_token_text_or_phone_fields() -> None:
    user_id = uuid4()
    deliveries = DeliveryRepository()
    service = VkNotificationDeliveryService(
        binding_repository=cast(Any, BindingRepository([binding(user_id, 101)])),
        delivery_repository=cast(Any, deliveries),
        messenger=Messenger(),
    )
    cmd = command(user_id)
    await service.deliver(command=cmd)
    assert set(deliveries.rows[(cmd.event_uuid, user_id)]) == {
        "event_uuid",
        "user_id",
        "vk_peer_id",
        "status",
        "attempts",
        "last_error",
    }
