from sqlalchemy import (
    Column,
    DateTime,
    Index,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.schema import ForeignKey

from utils.basemodel import metadata

vk_confirmations = Table(
    "vk_confirmations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("user_vk_id", UUID(as_uuid=True), ForeignKey("user_vks.id"), nullable=False),
    Column("code", String(16), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("used_at", DateTime(timezone=True), nullable=True),
    Index("ix_vk_confirmations_code", "code", unique=True),
    Index("ix_vk_confirmations_user_vk_id", "user_vk_id"),
)
