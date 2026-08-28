from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from core.schemas.messaging.base_event_data import MessagingBaseEventData


class NotificationCommandSendVkData(MessagingBaseEventData):
    """Canonical command consumed from Notification Service."""

    event_uuid: UUID = Field(..., description="Stable event identity for recipient idempotency")
    callback_request_id: UUID = Field(..., description="Callback correlation identity")
    user_ids: list[UUID] = Field(..., min_length=1, description="Eligible Notification-owned recipients")
    text: str = Field(..., min_length=1, max_length=4096, description="Plain-text callback message")

    model_config = ConfigDict(extra="forbid")

    @field_validator("user_ids")
    @classmethod
    def deduplicate_user_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must contain non-whitespace characters")
        return value
