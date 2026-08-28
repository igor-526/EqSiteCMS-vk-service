"""Add per-recipient VK notification delivery ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260827_0003"
down_revision = "20260827_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vk_notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vk_peer_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_uuid", "user_id", name="uq_vk_notification_deliveries_event_user"),
        sa.CheckConstraint("status IN ('PENDING', 'SENT', 'FAILED')", name="ck_vk_notification_deliveries_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_vk_notification_deliveries_attempts"),
    )
    op.create_index("ix_vk_notification_deliveries_status", "vk_notification_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_vk_notification_deliveries_status", table_name="vk_notification_deliveries")
    op.drop_table("vk_notification_deliveries")
