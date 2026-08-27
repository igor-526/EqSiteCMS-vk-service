"""Celery-конфигурация домена vk."""

from kombu import Queue

from workers import tasks as tasks_package
from workers.celery_app import celery_app
from workers.tasks.integration_probe import integration_probe_task

INHERITED_QUEUE = "email"
INHERITED_TASK_PREFIX = "email."


def test_only_vk_queue_is_registered_and_is_the_default() -> None:
    queue_names = [queue.name for queue in celery_app.conf.task_queues]

    assert queue_names == ["vk"]
    assert celery_app.conf.task_default_queue == "vk"
    assert celery_app.conf.task_queues == (Queue("vk"),)


def test_donor_queue_is_absent_from_celery_configuration() -> None:
    queue_names = {queue.name for queue in celery_app.conf.task_queues}

    assert INHERITED_QUEUE not in queue_names
    assert celery_app.conf.task_default_queue != INHERITED_QUEUE
    assert celery_app.main == "vk-service"


def test_reliability_and_serialization_settings_are_preserved() -> None:
    conf = celery_app.conf

    assert (conf.task_acks_late, conf.task_reject_on_worker_lost) == (True, True)
    assert (conf.task_serializer, conf.result_serializer, conf.accept_content) == ("json", "json", ["json"])
    assert (conf.task_expires, conf.worker_prefetch_multiplier) == (3600, 1)
    assert conf.broker_transport_options == {"visibility_timeout": 5}


def test_probe_task_is_registered_under_vk_domain() -> None:
    assert integration_probe_task.name == "vk.integration_probe"
    assert integration_probe_task.name.split(".")[0] == "vk"
    assert "vk.integration_probe" in celery_app.tasks


def test_task_package_exports_only_existing_tasks() -> None:
    assert tasks_package.__all__ == ["integration_probe_task"]
    assert all(hasattr(tasks_package, name) for name in tasks_package.__all__)
    assert not [name for name in tasks_package.__all__ if INHERITED_QUEUE in name]
    assert not [name for name in celery_app.tasks if name.startswith(INHERITED_TASK_PREFIX)]
