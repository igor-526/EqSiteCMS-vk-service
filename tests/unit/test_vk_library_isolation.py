"""Границы библиотеки VK и неразглашение группового токена."""

import ast
import logging
import re
from pathlib import Path

import pytest

from clients.vk import VkbottleMessenger
from settings import VkSettings

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
LIBRARY_TOKENS = ("vkbottle", "vkbottle_types")
ALLOWED_PREFIXES = ("clients/vk", "bot")
REAL_TOKEN = "vk1.a.deadbeefcafebabe0123"


def _source_files() -> list[Path]:
    return [path for path in sorted(SRC_ROOT.rglob("*.py")) if "__pycache__" not in path.parts]


def _vk_library_imports(content: str) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(ast.parse(content)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports.intersection(LIBRARY_TOKENS)


def test_library_is_imported_only_by_the_adapter_and_the_runtime() -> None:
    offenders: list[str] = []

    for path in _source_files():
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative.startswith(ALLOWED_PREFIXES):
            continue
        imported = _vk_library_imports(path.read_text(encoding="utf-8"))
        offenders += [f"{relative}:{token}" for token in sorted(imported)]

    assert offenders == []


def test_the_domain_layers_stay_free_of_the_library() -> None:
    domain_prefixes = ("core/", "repositories/", "models/", "api/")
    offenders: list[str] = []

    for path in _source_files():
        relative = path.relative_to(SRC_ROOT).as_posix()
        if not relative.startswith(domain_prefixes):
            continue
        imported = _vk_library_imports(path.read_text(encoding="utf-8"))
        offenders += [f"{relative}:{token}" for token in sorted(imported)]

    assert offenders == []


def test_library_guard_distinguishes_imports_from_logger_name_literals() -> None:
    assert _vk_library_imports('ignore_logger("vkbottle")') == set()
    assert _vk_library_imports("from vkbottle.polling import BotPolling") == {"vkbottle"}
    assert _vk_library_imports("import vkbottle_types.codegen") == {"vkbottle_types"}


def test_forbidden_vk_libraries_are_not_imported() -> None:
    # Ищутся именно импорты: `vk_api` встречается как имя локальной переменной.
    forbidden_import = re.compile(r"^\s*(?:import|from)\s+(vk_api|aiovk|vkwave)\b", re.MULTILINE)
    offenders: list[str] = []

    for path in _source_files():
        content = path.read_text(encoding="utf-8")
        offenders += [
            f"{path.relative_to(SRC_ROOT).as_posix()}:{match}" for match in forbidden_import.findall(content)
        ]

    assert offenders == []


def test_forbidden_vk_libraries_are_absent_from_the_dependency_manifest() -> None:
    manifest = (SRC_ROOT.parent / "pyproject.toml").read_text(encoding="utf-8")

    assert "vkbottle" in manifest
    assert not [name for name in ("vk_api", "aiovk", "vkwave") if name in manifest]


def test_the_group_token_is_not_logged_on_a_failed_delivery(caplog: pytest.LogCaptureFixture) -> None:
    settings = VkSettings(_env_file=None, VK_GROUP_TOKEN=REAL_TOKEN)  # type: ignore[call-arg]
    messenger = VkbottleMessenger(settings=settings)

    class _FailingApi:
        class messages:  # noqa: N801 - имитация пространства методов клиента
            @staticmethod
            async def send(**_: object) -> None:
                raise RuntimeError("VK API rejected the request")

    messenger._api = _FailingApi()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        delivered = None

        async def _run() -> None:
            nonlocal delivered
            delivered = await messenger.send_message(peer_id=1, text="probe")

        import asyncio

        asyncio.run(_run())

    assert delivered is False
    assert REAL_TOKEN not in caplog.text


def test_a_failed_profile_lookup_returns_none_without_leaking_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = VkSettings(_env_file=None, VK_GROUP_TOKEN=REAL_TOKEN)  # type: ignore[call-arg]
    messenger = VkbottleMessenger(settings=settings)

    class _FailingApi:
        class users:  # noqa: N801 - имитация пространства методов клиента
            @staticmethod
            async def get(**_: object) -> list[object]:
                raise RuntimeError("VK API rejected the request")

    messenger._api = _FailingApi()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        import asyncio

        profile = asyncio.run(messenger.get_profile(peer_id=1))

    assert profile is None
    assert REAL_TOKEN not in caplog.text
