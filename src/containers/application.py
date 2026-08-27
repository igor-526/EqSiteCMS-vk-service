from dependency_injector import containers, providers

from clients.nats import NatsJetstreamClient
from settings import celery_settings as celery_settings_instance
from settings import nats_settings as nats_settings_instance


class ApplicationContainer(containers.DeclarativeContainer):
    nats_settings = providers.Object(nats_settings_instance)

    nats_client = providers.Singleton(
        NatsJetstreamClient,
        settings=nats_settings,
    )

    # Celery
    celery_settings = providers.Object(celery_settings_instance)
    celery_app = providers.Singleton(
        lambda: __import__("workers.celery_app", fromlist=["celery_app"]).celery_app,
    )
