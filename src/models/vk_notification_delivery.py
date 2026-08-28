from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from utils.basemodel import metadata

vk_notification_deliveries = Table(
    "vk_notification_deliveries",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("event_uuid", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("vk_peer_id", BigInteger, nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'PENDING'")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("last_error", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("sent_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("event_uuid", "user_id", name="uq_vk_notification_deliveries_event_user"),
    CheckConstraint("status IN ('PENDING', 'SENT', 'FAILED')", name="ck_vk_notification_deliveries_status"),
    CheckConstraint("attempts >= 0", name="ck_vk_notification_deliveries_attempts"),
    Index("ix_vk_notification_deliveries_status", "status"),
)
