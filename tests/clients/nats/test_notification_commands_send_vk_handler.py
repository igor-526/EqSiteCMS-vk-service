from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from clients.nats.handlers.notification_commands_send_vk import NotificationCommandsSendVkHandler
from core.schemas.messaging import VK_CHANNEL_CODE, NotificationCommandSendVkData, build_command_msg_id

# Фиксированный тестовый вектор. Идентичный вектор проверяется в notification-service:
# tests/unit/messaging/test_command_identity.py — расхождение означает рассинхронизацию контракта.
VECTOR_CALLBACK_ID = UUID("e317a8b9-5513-437b-ae2a-abb0a8883ca8")
VECTOR_EMAIL_MSG_ID = UUID("0a08d7c9-ac68-5c4f-8e7a-7c30d3c8c1d4")
VECTOR_VK_MSG_ID = UUID("aacfe433-467a-5b34-812d-165f7773589d")


def command(callback_request_id: UUID) -> NotificationCommandSendVkData:
    return NotificationCommandSendVkData(
        occurred_at=datetime(2026, 8, 28, tzinfo=UTC),
        event_uuid=uuid4(),
        callback_request_id=callback_request_id,
        user_ids=[uuid4()],
        text="Новый запрос",
    )


def make_handler() -> tuple[NotificationCommandsSendVkHandler, AsyncMock]:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    service = AsyncMock()
    handler = NotificationCommandsSendVkHandler(
        session_factory=MagicMock(return_value=session),
        messenger=MagicMock(),
        service_factory=lambda **_: service,
    )
    return handler, service


def headers_for(message_id: UUID) -> dict[str, str]:
    return {"Nats-Msg-Id": str(message_id)}


def test_msg_id_matches_fixed_vector() -> None:
    assert build_command_msg_id(correlation_id=VECTOR_CALLBACK_ID, channel_code=VK_CHANNEL_CODE) == VECTOR_VK_MSG_ID
    assert build_command_msg_id(correlation_id=VECTOR_CALLBACK_ID, channel_code="email") == VECTOR_EMAIL_MSG_ID


async def test_handler_accepts_channel_scoped_msg_id() -> None:
    handler, service = make_handler()
    payload = command(uuid4())
    message_id = build_command_msg_id(correlation_id=payload.callback_request_id, channel_code=VK_CHANNEL_CODE)

    await handler.handle(payload=payload.model_dump_json().encode(), headers=headers_for(message_id))

    service.deliver.assert_awaited_once()


async def test_handler_rejects_raw_callback_request_id_as_msg_id() -> None:
    handler, service = make_handler()
    payload = command(uuid4())

    with pytest.raises(ValueError):
        await handler.handle(
            payload=payload.model_dump_json().encode(),
            headers=headers_for(payload.callback_request_id),
        )

    service.deliver.assert_not_awaited()


async def test_handler_rejects_msg_id_of_another_channel() -> None:
    handler, service = make_handler()
    payload = command(uuid4())
    email_message_id = build_command_msg_id(correlation_id=payload.callback_request_id, channel_code="email")

    with pytest.raises(ValueError):
        await handler.handle(payload=payload.model_dump_json().encode(), headers=headers_for(email_message_id))

    service.deliver.assert_not_awaited()


async def test_handler_rejects_missing_header() -> None:
    handler, service = make_handler()
    payload = command(uuid4())

    with pytest.raises(ValueError):
        await handler.handle(payload=payload.model_dump_json().encode(), headers={})

    service.deliver.assert_not_awaited()


async def test_idempotency_key_does_not_depend_on_msg_id() -> None:
    """Повторная обработка опирается на `(event_uuid, user_id)`, а не на заголовок."""
    handler, service = make_handler()
    payload = command(uuid4())
    message_id = build_command_msg_id(correlation_id=payload.callback_request_id, channel_code=VK_CHANNEL_CODE)

    await handler.handle(payload=payload.model_dump_json().encode(), headers=headers_for(message_id))

    delivered: Any = service.deliver.await_args.kwargs["command"]
    assert delivered.event_uuid == payload.event_uuid
    assert delivered.callback_request_id == payload.callback_request_id
