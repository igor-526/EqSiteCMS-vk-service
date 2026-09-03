"""Устойчивость long-poll цикла: служебные коды VK, обрывы сети, останов.

Проверяется фактический цикл `vkbottle`, который использует runtime сервиса:
события подставляются stub-сервером, сеть не задействована.
"""

import asyncio
import logging
from typing import Any

import pytest
from aiohttp.client_exceptions import ClientConnectionError
from vkbottle.polling import BotPolling

WAIT = 25


class StubPolling(BotPolling):
    """Long-poll со сценарием ответов вместо реального сервера VK."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__(group_id=1, wait=WAIT)
        self.script = list(script)
        self.server_requests = 0
        self.event_requests = 0
        self.saved_ts: list[str] = []

    async def get_server(self) -> dict[str, Any]:
        self.server_requests += 1
        return {"server": "https://stub.invalid", "key": f"key-{self.server_requests}", "ts": "100"}

    async def get_event(self, server: dict[str, Any]) -> dict[str, Any]:
        self.event_requests += 1
        if not self.script:
            self.stop()
            return {"ts": server["ts"], "updates": []}
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return dict(step)

    def save_server_ts(self, server: dict[str, Any]) -> None:
        self.saved_ts.append(server["ts"])


async def _drain(polling: StubPolling, *, limit: int = 10) -> list[dict]:
    collected: list[dict] = []
    async for event in polling.listen():
        collected.append(event)
        if len(collected) >= limit:
            polling.stop()
    return collected


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff не должен растягивать тест: сон заменяется на no-op."""
    original_sleep = asyncio.sleep

    async def _no_wait(delay: float, *args: Any, **kwargs: Any) -> Any:
        return await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _no_wait)


async def test_updates_are_yielded_and_ts_is_advanced() -> None:
    polling = StubPolling([{"ts": "101", "updates": [{"type": "message_new"}]}])

    events = await _drain(polling)

    assert [event["ts"] for event in events] == ["101"]
    assert polling.saved_ts[0] == "101", "ts must be persisted only after the event is handed off"
    assert polling.server_requests == 1


async def test_outdated_history_continues_with_the_returned_ts() -> None:
    polling = StubPolling(
        [
            {"failed": 1, "ts": "555"},
            {"ts": "556", "updates": [{"type": "message_new"}]},
        ]
    )

    events = await _drain(polling)

    assert [event["ts"] for event in events] == ["556"]
    assert polling.server_requests == 1, "failed=1 must not refetch the long poll server"


@pytest.mark.parametrize("failure_code", [2, 3])
async def test_expired_key_and_lost_information_refetch_the_server(failure_code: int) -> None:
    polling = StubPolling(
        [
            {"failed": failure_code},
            {"ts": "201", "updates": [{"type": "message_new"}]},
        ]
    )

    events = await _drain(polling)

    assert [event["ts"] for event in events] == ["201"]
    assert polling.server_requests == 2, "the long poll server must be refetched"


async def test_a_network_drop_retries_without_stopping_the_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    polling = StubPolling(
        [
            ClientConnectionError("connection reset"),
            ClientConnectionError("connection reset"),
            {"ts": "301", "updates": [{"type": "message_new"}]},
        ]
    )

    with caplog.at_level(logging.ERROR, logger="vkbottle"):
        events = await _drain(polling)

    assert [event["ts"] for event in events] == ["301"]
    assert polling.event_requests >= 3, "both drops must be retried before the event is delivered"
    assert polling.server_requests == 1, "a transient error must keep the current server and ts"
    retry_records = [record for record in caplog.records if record.message.startswith("Unable to make request to ")]
    assert len(retry_records) == 2
    assert {record.name for record in retry_records} == {"vkbottle"}


async def test_a_timeout_retries_without_stopping_the_loop() -> None:
    polling = StubPolling(
        [
            TimeoutError("long poll timed out"),
            {"ts": "401", "updates": [{"type": "message_new"}]},
        ]
    )

    events = await _drain(polling)

    assert [event["ts"] for event in events] == ["401"]


async def test_an_unexpected_error_is_handled_and_the_loop_survives() -> None:
    handled: list[Exception] = []
    polling = StubPolling(
        [
            RuntimeError("unexpected handler failure"),
            {"ts": "501", "updates": [{"type": "message_new"}]},
        ]
    )

    async def _record(error: Exception) -> None:
        handled.append(error)

    polling.error_handler.handle = _record  # type: ignore[method-assign]

    events = await _drain(polling)

    assert [event["ts"] for event in events] == ["501"]
    assert [type(error) for error in handled] == [RuntimeError]


async def test_empty_updates_are_not_yielded() -> None:
    polling = StubPolling(
        [
            {"ts": "601", "updates": []},
            {"ts": "602", "updates": [{"type": "message_new"}]},
        ]
    )

    events = await _drain(polling)

    assert [event["ts"] for event in events] == ["602"]


async def test_stop_ends_the_loop() -> None:
    polling = StubPolling([{"ts": "701", "updates": [{"type": "message_new"}]}] * 5)

    events = await _drain(polling, limit=1)

    assert len(events) == 1
