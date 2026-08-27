from celery import Celery
from kombu import Queue

from settings import celery_settings

celery_app = Celery(
    celery_settings.celery_app_main,
    broker=celery_settings.celery_app_broker,
    backend=celery_settings.celery_app_backend,
)

# Очереди
celery_app.conf.task_queues = (Queue("vk"),)
celery_app.conf.task_default_queue = "vk"

# Сериализация
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]

# Надёжность
celery_app.conf.task_expires = 3600
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.broker_transport_options = {"visibility_timeout": 5}

# Autodiscovery
celery_app.autodiscover_tasks(["workers.tasks"])
