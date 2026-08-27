from sqlalchemy import (
    Column,
    DateTime,
    Index,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from utils.basemodel import metadata

vk_logs = Table(
    "vk_logs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("event_uuid", UUID(as_uuid=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("details", JSONB, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Index("ix_vk_logs_event_uuid", "event_uuid", unique=True),
    Index("ix_vk_logs_action", "action"),
    Index("ix_vk_logs_created_at", "created_at"),
)
