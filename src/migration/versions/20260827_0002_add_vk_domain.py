"""Add VK domain tables: user_vks, vk_confirmations, vk_logs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260827_0002"
down_revision = "20260710_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_vks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vk_peer_id", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("vk_screen_name", sa.String(length=64), nullable=True),
        sa.Column("vk_display_name", sa.String(length=255), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "uq_user_vks_user_id_active",
        "user_vks",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_user_vks_peer_id_active",
        "user_vks",
        ["vk_peer_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND vk_peer_id IS NOT NULL"),
    )
    op.create_index("ix_user_vks_state", "user_vks", ["state"], unique=False)

    op.create_table(
        "vk_confirmations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_vk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_vk_id"], ["user_vks.id"]),
    )
    op.create_index("ix_vk_confirmations_code", "vk_confirmations", ["code"], unique=True)
    op.create_index("ix_vk_confirmations_user_vk_id", "vk_confirmations", ["user_vk_id"], unique=False)

    op.create_table(
        "vk_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_vk_logs_event_uuid", "vk_logs", ["event_uuid"], unique=True)
    op.create_index("ix_vk_logs_action", "vk_logs", ["action"], unique=False)
    op.create_index("ix_vk_logs_created_at", "vk_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vk_logs_created_at", table_name="vk_logs")
    op.drop_index("ix_vk_logs_action", table_name="vk_logs")
    op.drop_index("ix_vk_logs_event_uuid", table_name="vk_logs")
    op.drop_table("vk_logs")

    op.drop_index("ix_vk_confirmations_user_vk_id", table_name="vk_confirmations")
    op.drop_index("ix_vk_confirmations_code", table_name="vk_confirmations")
    op.drop_table("vk_confirmations")

    op.drop_index("ix_user_vks_state", table_name="user_vks")
    op.drop_index("uq_user_vks_peer_id_active", table_name="user_vks")
    op.drop_index("uq_user_vks_user_id_active", table_name="user_vks")
    op.drop_table("user_vks")
