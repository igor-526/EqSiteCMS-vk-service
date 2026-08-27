"""Реестр таблиц VK-домена: состав колонок, значения по умолчанию, индексы."""

from sqlalchemy import Table

from models import user_vks, vk_confirmations, vk_logs
from utils.basemodel import metadata

EXPECTED_TABLES = {"user_vks", "vk_confirmations", "vk_logs"}
DONOR_TABLES = {"user_emails", "email_confirmations", "email_logs"}


def _index(table: Table, name: str):
    return next(index for index in table.indexes if index.name == name)


def test_metadata_registers_only_vk_domain_tables() -> None:
    assert set(metadata.tables) == EXPECTED_TABLES
    assert not EXPECTED_TABLES & DONOR_TABLES
    assert not set(metadata.tables) & DONOR_TABLES


def test_user_vks_columns_and_nullability() -> None:
    columns = {column.name: column for column in user_vks.columns}

    assert set(columns) == {
        "id",
        "user_id",
        "vk_peer_id",
        "state",
        "vk_screen_name",
        "vk_display_name",
        "deleted_at",
        "created_at",
        "updated_at",
    }
    assert columns["user_id"].nullable is False
    assert columns["vk_peer_id"].nullable is True
    assert columns["state"].nullable is False
    assert columns["deleted_at"].nullable is True
    assert columns["created_at"].nullable is False
    assert columns["updated_at"].nullable is False


def test_user_vks_defaults_are_pending_and_not_deleted() -> None:
    columns = {column.name: column for column in user_vks.columns}
    state_default = columns["state"].server_default

    assert state_default is not None
    assert "PENDING" in str(getattr(state_default, "arg", ""))
    assert columns["deleted_at"].server_default is None
    assert columns["vk_peer_id"].server_default is None


def test_user_vks_partial_unique_indexes_are_declared() -> None:
    user_index = _index(user_vks, "uq_user_vks_user_id_active")
    peer_index = _index(user_vks, "uq_user_vks_peer_id_active")

    assert user_index.unique is True
    assert str(user_index.dialect_options["postgresql"]["where"]) == "deleted_at IS NULL"
    assert peer_index.unique is True
    assert str(peer_index.dialect_options["postgresql"]["where"]) == "deleted_at IS NULL AND vk_peer_id IS NOT NULL"
    assert _index(user_vks, "ix_user_vks_state").unique is False


def test_vk_confirmations_structure_and_unique_code() -> None:
    columns = {column.name: column for column in vk_confirmations.columns}

    assert set(columns) == {"id", "user_vk_id", "code", "expires_at", "created_at", "used_at"}
    assert getattr(columns["code"].type, "length", None) == 16
    assert columns["used_at"].nullable is True
    assert _index(vk_confirmations, "ix_vk_confirmations_code").unique is True
    assert [fk.target_fullname for fk in columns["user_vk_id"].foreign_keys] == ["user_vks.id"]


def test_vk_logs_structure_and_indexes() -> None:
    columns = {column.name: column for column in vk_logs.columns}

    assert set(columns) == {"id", "event_uuid", "action", "status", "details", "created_at"}
    assert columns["details"].nullable is True
    assert _index(vk_logs, "ix_vk_logs_event_uuid").unique is True
    assert _index(vk_logs, "ix_vk_logs_action").unique is False
    assert _index(vk_logs, "ix_vk_logs_created_at").unique is False
