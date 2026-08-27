from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class VkBindingCreateRequest(BaseModel):
    user_id: UUID


class VkIssueConfirmationRequest(BaseModel):
    user_id: UUID


class VkBindingResponse(BaseModel):
    id: UUID
    user_id: UUID
    vk_peer_id: int | None
    state: str
    vk_screen_name: str | None
    vk_display_name: str | None


class VkBotInfoResponse(BaseModel):
    group_id: int
    group_screen_name: str
    link_command: str
    group_url: str
    dialog_url: str


class VkIssueConfirmationResponse(BaseModel):
    code: str = Field(min_length=1)
    expires_at: datetime
    state: str
    link_command: str
    dialog_url: str
