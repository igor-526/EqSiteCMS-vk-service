"""Infrastructure-тесты реального брокера очереди `vk`.

Запускаются только целью `make test-infra` при поднятом стеке `eqsitecms-vk`.
"""

import os
import subprocess
import time
import uuid
from collections.abc import Generator
from typing import cast

import pytest
from celery import Celery
from redis import Redis

PROBE_TASK = "vk.integration_probe"
QUEUE = "vk"
WORKER_CONTAINER = "eqsitecms-vk-celery-worker"
WORKER_DESTINATION = "vk-worker@vk-worker"
INHERITED_TASK_PREFIX = "email."


def _wait_until(predicate: object, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.1)
    raise AssertionError("condition was not met before the bounded timeout")


@pytest.fixture
def real_celery() -> Generator[tuple[Celery, Redis, str]]:
    broker = os.environ["VK_TEST_CELERY_BROKER"]
    backend = os.environ["VK_TEST_CELERY_BACKEND"]
    app = Celery("vk-integration-test", broker=broker, backend=backend)
    app.conf.broker_transport_options = {"visibility_timeout": 5}
    redis = Redis.from_url(broker, decode_responses=True)
    probe_id = uuid.uuid4().hex
    try:
        assert redis.ping()
        yield app, redis, probe_id
    finally:
        for key in redis.scan_iter(f"eqsitecms:integration:{probe_id}:*"):
            redis.delete(key)


@pytest.mark.infrastructure
def test_real_delivery_retry_acks_late_and_idempotency(real_celery: tuple[Celery, Redis, str]) -> None:
    app, redis, probe_id = real_celery
    first = app.send_task(
        PROBE_TASK,
        args=[probe_id],
        kwargs={"fail_until": 1},
        queue=QUEUE,
    ).get(timeout=15)
    second = app.send_task(PROBE_TASK, args=[probe_id], queue=QUEUE).get(timeout=15)

    assert first == {"acks_late": True, "attempts": 2, "effect": "created"}
    assert second == {"acks_late": True, "attempts": 3, "effect": "duplicate"}
    assert redis.get(f"eqsitecms:integration:{probe_id}:effect") == "1"


@pytest.mark.infrastructure
def test_unacked_task_is_redelivered_after_worker_restart(real_celery: tuple[Celery, Redis, str]) -> None:
    app, redis, probe_id = real_celery
    result = app.send_task(
        PROBE_TASK,
        args=[probe_id],
        kwargs={"delay_seconds": 8},
        queue=QUEUE,
    )
    _wait_until(lambda: redis.exists(f"eqsitecms:integration:{probe_id}:started") == 1)

    subprocess.run(["docker", "kill", WORKER_CONTAINER], check=True, timeout=10)
    subprocess.run(["docker", "start", WORKER_CONTAINER], check=True, timeout=10)

    def worker_is_ready() -> bool:
        probe = subprocess.run(
            [
                "docker",
                "exec",
                WORKER_CONTAINER,
                "uv",
                "run",
                "--no-sync",
                "celery",
                "-A",
                "workers.celery_app",
                "inspect",
                "ping",
                "--destination",
                WORKER_DESTINATION,
                "--timeout",
                "2",
            ],
            capture_output=True,
            timeout=5,
        )
        return probe.returncode == 0

    _wait_until(worker_is_ready, timeout=20)
    # Deterministically trigger the Redis transport's expired-unacked sweep.
    time.sleep(6)
    with app.connection_for_read() as connection:
        channel = connection.channel()
        channel.qos.restore_visible(start=0, num=100, interval=1)

    assert result.get(timeout=15)["effect"] == "created"
    attempts = cast(str | None, redis.get(f"eqsitecms:integration:{probe_id}:attempts"))
    assert int(attempts or 0) >= 2


@pytest.mark.infrastructure
def test_worker_serves_only_the_vk_queue(real_celery: tuple[Celery, Redis, str]) -> None:
    app, _, _ = real_celery

    active_queues = app.control.inspect(destination=[WORKER_DESTINATION], timeout=5).active_queues() or {}
    registered = app.control.inspect(destination=[WORKER_DESTINATION], timeout=5).registered() or {}

    assert active_queues, "worker did not answer the addressed inspect call"
    for queues in active_queues.values():
        assert [queue["name"] for queue in queues] == [QUEUE]
    for names in registered.values():
        assert PROBE_TASK in names
        assert not [name for name in names if name.startswith(INHERITED_TASK_PREFIX)]
