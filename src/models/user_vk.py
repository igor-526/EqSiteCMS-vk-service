from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from utils.basemodel import metadata

user_vks = Table(
    "user_vks",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("vk_peer_id", BigInteger, nullable=True),
    Column("state", String(16), nullable=False, server_default=text("'PENDING'")),
    Column("vk_screen_name", String(64), nullable=True),
    Column("vk_display_name", String(255), nullable=True),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Index(
        "uq_user_vks_user_id_active",
        "user_id",
        unique=True,
        postgresql_where=text("deleted_at IS NULL"),
    ),
    Index(
        "uq_user_vks_peer_id_active",
        "vk_peer_id",
        unique=True,
        postgresql_where=text("deleted_at IS NULL AND vk_peer_id IS NOT NULL"),
    ),
    Index("ix_user_vks_state", "state"),
)
