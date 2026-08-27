from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessagingBaseEventData(BaseModel):
    occurred_at: datetime = Field(default_factory=datetime.now, description="Когда произошло событие")

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )
