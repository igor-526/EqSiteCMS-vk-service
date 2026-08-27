"""DI-контейнер скелета vk-service."""

from dependency_injector import containers

import main
from clients.nats import NatsJetstreamClient
from containers.application import ApplicationContainer

REMOVED_PROVIDERS = ("smtp_settings", "email_sender")
REMOVED_TOKENS = ("email", "smtp")


def test_nats_client_provider_returns_the_same_singleton() -> None:
    container = ApplicationContainer()

    first = container.nats_client()
    second = container.nats_client()

    assert first is second
    assert isinstance(first, NatsJetstreamClient)


def test_container_exposes_only_skeleton_providers() -> None:
    provider_names = set(ApplicationContainer.providers)

    assert provider_names == {"nats_settings", "nats_client", "celery_settings", "celery_app"}
    assert not [name for name in REMOVED_PROVIDERS if name in provider_names]
    assert not [name for name in provider_names if any(token in name for token in REMOVED_TOKENS)]


def test_container_is_not_stored_in_app_state() -> None:
    state = vars(main.app.state).get("_state", {})

    container_types = (containers.DynamicContainer, containers.DeclarativeContainer)

    assert "container" not in state
    assert not any(isinstance(value, container_types) for value in state.values())
    assert set(main.container.providers) == set(ApplicationContainer.providers)
