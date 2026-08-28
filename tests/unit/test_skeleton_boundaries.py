"""Границы сервиса: реестр таблиц VK-домена, состав точек расширения, чистый `src/`.

Самосканирующий тест использует искомые токены как данные и записывает их обычными
литералами: guard-проверка ограничена реализацией и `tests/**` не покрывает.
"""

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import models
import repositories
from utils.basemodel import metadata

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

FORBIDDEN_TOKENS = ("email", "smtp", "aiosmtplib")

EXPECTED_TABLES = {"user_vks", "vk_confirmations", "vk_logs", "vk_notification_deliveries"}


def test_models_package_registers_only_vk_domain_tables() -> None:
    importlib.reload(models)

    assert set(models.__all__) == EXPECTED_TABLES
    assert set(metadata.tables) == EXPECTED_TABLES


def test_repositories_package_exports_only_vk_domain_members() -> None:
    module = importlib.reload(repositories)

    assert set(module.__all__) == {
        "SQLAlchemyUserVkRepository",
        "SQLAlchemyVkConfirmationRepository",
        "SQLAlchemyVkLogRepository",
        "SQLAlchemyVkNotificationDeliveryRepository",
        "UserVkRepositoryProtocol",
        "VkConfirmationRepositoryProtocol",
        "VkLogRepositoryProtocol",
        "VkNotificationDeliveryRepositoryProtocol",
    }
    assert sorted(module.__all__) == list(module.__all__)


def test_migration_env_binds_target_metadata_to_service_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import context as alembic_context

    configured: dict[str, Any] = {}

    class _Transaction:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(
        alembic_context,
        "config",
        SimpleNamespace(set_main_option=lambda *_: None, config_file_name=None),
        raising=False,
    )
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: True, raising=False)
    monkeypatch.setattr(alembic_context, "configure", lambda **kwargs: configured.update(kwargs), raising=False)
    monkeypatch.setattr(alembic_context, "begin_transaction", _Transaction, raising=False)
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: None, raising=False)

    env = importlib.import_module("migration.env")
    importlib.reload(env)

    assert env.target_metadata is metadata
    assert configured["target_metadata"] is metadata
    assert configured["url"].startswith("postgresql+asyncpg://")


def test_source_tree_has_no_residue_of_the_donor_domain() -> None:
    offenders: list[str] = []

    for path in sorted(SRC_ROOT.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore").lower()
        offenders += [f"{path.relative_to(SRC_ROOT)}:{token}" for token in FORBIDDEN_TOKENS if token in content]

    assert offenders == []
