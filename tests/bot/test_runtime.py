"""Runtime бота: fail-fast без токена, регистрация обработчиков, отсутствие сети."""

from collections.abc import Callable
from typing import Any, cast

import pytest
from vkbottle import API

import bot.main as bot_main
from settings import VkSettings

REAL_TOKEN = "vk1.a.1f2e3d4c5b6a79880011"

_vk_settings_factory = cast(Callable[..., VkSettings], VkSettings)


def _vk_settings(**overrides: Any) -> VkSettings:
    return _vk_settings_factory(_env_file=None, **overrides)


def test_entry_point_fails_fast_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_main, "vk_settings_instance", _vk_settings(VK_GROUP_TOKEN=""))
    started = False

    def _never_run(*_: object, **__: object) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(bot_main.asyncio, "run", _never_run)

    assert bot_main.main() == 1
    assert started is False


def test_entry_point_fails_fast_on_a_placeholder_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bot_main,
        "vk_settings_instance",
        _vk_settings(VK_GROUP_TOKEN="<set-vk-group-access-token>"),
    )
    monkeypatch.setattr(
        bot_main.asyncio,
        "run",
        lambda *_, **__: pytest.fail("runtime must not start"),
    )

    assert bot_main.main() == 1


def test_the_failure_message_names_the_variable_without_revealing_a_value(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot_main, "vk_settings_instance", _vk_settings(VK_GROUP_TOKEN=""))

    assert bot_main.main() == 1
    assert "VK_GROUP_TOKEN" in bot_main.TOKEN_REQUIRED_MESSAGE
    assert REAL_TOKEN not in caplog.text


def test_entry_point_starts_the_loop_with_a_real_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_main, "vk_settings_instance", _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN))
    started: list[Any] = []

    def _capture(coroutine: Any) -> None:
        started.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(bot_main.asyncio, "run", _capture)

    assert bot_main.main() == 0
    assert len(started) == 1


def test_build_bot_registers_message_and_permission_handlers() -> None:
    api = API(token=REAL_TOKEN)

    built, handler = bot_main.build_bot(vk_settings=_vk_settings(VK_GROUP_TOKEN=REAL_TOKEN), api=api)

    assert built.labeler.message_view.handlers
    assert built.labeler.raw_event_view.handlers
    assert handler is not None


@pytest.mark.parametrize("configured_wait", [1, 25, 42, 90])
def test_build_bot_applies_the_configured_long_poll_wait(configured_wait: int) -> None:
    api = API(token=REAL_TOKEN)
    settings = _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_LONGPOLL_WAIT_SECONDS=configured_wait)

    built, _ = bot_main.build_bot(vk_settings=settings, api=api)

    assert cast(Any, built.polling).wait == configured_wait


def test_build_bot_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    api = API(token=REAL_TOKEN)

    def _forbidden(*_: object, **__: object) -> None:
        pytest.fail("bot construction must not touch the network")

    monkeypatch.setattr(api.http_client, "request_text", _forbidden)
    monkeypatch.setattr(api.http_client, "request_json", _forbidden)

    bot_main.build_bot(vk_settings=_vk_settings(VK_GROUP_TOKEN=REAL_TOKEN), api=api)


def test_http_application_does_not_start_the_long_poll_loop() -> None:
    import pathlib

    import main as http_main

    source = pathlib.Path(str(http_main.__file__)).read_text(encoding="utf-8")

    assert "run_polling" not in source
    assert "vkbottle" not in source
    assert "bot" not in {line.strip() for line in source.splitlines()}


class _StubApi:
    """Заглушка VK API: отвечает на groups.getLongPollServer заданным исходом."""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"response": {"key": "k", "server": "https://lp.invalid", "ts": "1"}}


def _bot_with(outcome: object) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(api=_StubApi(outcome))


def _vk_error(code: int) -> Exception:
    from vkbottle.exception_factory import VKAPIError

    return VKAPIError[code](error_msg="denied")


async def test_preflight_passes_when_vk_grants_long_poll_access() -> None:
    bot = _bot_with(None)

    problem = await bot_main.preflight(bot, _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_GROUP_ID=1))

    assert problem is None
    assert bot.api.calls == [("groups.getLongPollServer", {"group_id": 1})]


async def test_preflight_rejects_an_unset_group_id_without_calling_vk() -> None:
    bot = _bot_with(None)

    problem = await bot_main.preflight(bot, _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN))

    assert problem == bot_main.GROUP_ID_REQUIRED_MESSAGE
    assert bot.api.calls == []


@pytest.mark.parametrize("code", [15, 100])
async def test_preflight_explains_a_missing_scope(code: int) -> None:
    bot = _bot_with(_vk_error(code))

    problem = await bot_main.preflight(bot, _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_GROUP_ID=1))

    assert problem is not None
    assert "manage" in problem
    assert "Long Poll API" in problem
    assert f"VK error {code}" in problem


@pytest.mark.parametrize("code", [5, 27, 28])
async def test_preflight_explains_an_invalid_token(code: int) -> None:
    bot = _bot_with(_vk_error(code))

    problem = await bot_main.preflight(bot, _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_GROUP_ID=1))

    assert problem is not None
    assert "invalid or expired" in problem
    assert f"VK error {code}" in problem


async def test_preflight_reports_an_unexpected_vk_error_with_its_code() -> None:
    bot = _bot_with(_vk_error(6))

    problem = await bot_main.preflight(bot, _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_GROUP_ID=1))

    assert problem is not None
    assert "error 6" in problem


async def test_preflight_tolerates_a_transient_network_failure() -> None:
    bot = _bot_with(OSError("connection reset"))

    problem = await bot_main.preflight(bot, _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_GROUP_ID=1))

    assert problem is None, "сетевой сбой не должен мешать старту: цикл сам переподключится"


def test_preflight_failure_makes_the_process_exit_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bot_main,
        "vk_settings_instance",
        _vk_settings(VK_GROUP_TOKEN=REAL_TOKEN, VK_GROUP_ID=1),
    )

    async def _failing_run(_settings: Any = None) -> None:
        raise bot_main.PreflightError("scopes are missing")

    monkeypatch.setattr(bot_main, "run", _failing_run)

    assert bot_main.main() == 1


def test_the_scope_hint_never_reveals_the_token() -> None:
    assert REAL_TOKEN not in bot_main.LONGPOLL_ACCESS_MESSAGE
    assert "VK_GROUP_TOKEN" in bot_main.TOKEN_REQUIRED_MESSAGE
