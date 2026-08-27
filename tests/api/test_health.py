"""HTTP-контракт vk-service: health и отсутствие унаследованной поверхности.

Унаследованные от `email-service` маршруты `/emails*` в скелете не зарегистрированы:
каждый из них обязан отвечать `404`. Пути записаны обычными литералами, потому что
guard-проверка на остатки email-домена ограничена реализацией (`src/`,
`pyproject.toml`, `.env.example`) и на `tests/**` не распространяется.
"""

from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import main
from core.exceptions import AppError, NotFoundError
from utils import database as database_module
from workers import celery_app as celery_app_module

INHERITED_COLLECTION = "/emails"

AUTHENTICATED_COOKIES = {"session": "cms-session-token", "equestrian_key": "cms-equestrian-key"}


def _client() -> TestClient:
    return TestClient(main.app)


def test_health_returns_ok_for_anonymous_client() -> None:
    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_identical_for_authenticated_client() -> None:
    authenticated_client = TestClient(main.app, cookies=AUTHENTICATED_COOKIES)

    anonymous = _client().get("/health")
    authenticated = authenticated_client.get("/health")

    assert authenticated.status_code == anonymous.status_code == 200
    assert authenticated.json() == anonymous.json() == {"status": "ok"}


def test_inherited_collection_read_is_not_registered() -> None:
    response = _client().get(INHERITED_COLLECTION, params={"user_ids": "6f1a0b6c-2f0d-4a54-9c9f-1f7b2b0e91aa"})

    assert response.status_code == 404


def test_inherited_collection_create_is_not_registered_and_touches_no_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = Mock()
    monkeypatch.setattr(database_module, "SessionFactory", session_factory)

    response = _client().post(INHERITED_COLLECTION, json={"user_id": "6f1a0b6c-2f0d-4a54-9c9f-1f7b2b0e91aa"})

    assert response.status_code == 404
    session_factory.assert_not_called()


def test_inherited_collection_update_is_not_registered() -> None:
    response = _client().patch(INHERITED_COLLECTION, json={"address": "someone@example.invalid"})

    assert response.status_code == 404


def test_inherited_collection_delete_is_not_registered() -> None:
    response = _client().delete("/emails/6f1a0b6c-2f0d-4a54-9c9f-1f7b2b0e91aa")

    assert response.status_code == 404


def test_inherited_confirmation_request_is_not_registered_and_dispatches_no_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_task = Mock()
    monkeypatch.setattr(celery_app_module.celery_app, "send_task", send_task)

    response = _client().post(
        "/emails/send-confirmation",
        json={"user_id": "6f1a0b6c-2f0d-4a54-9c9f-1f7b2b0e91aa"},
    )

    assert response.status_code == 404
    send_task.assert_not_called()


def test_inherited_confirmation_apply_is_not_registered() -> None:
    response = _client().patch("/emails/confirm", json={"token": "any-token"})

    assert response.status_code == 404


def test_auth_routes_are_not_registered() -> None:
    response = _client().post("/api/auth/register")

    assert response.status_code == 404


def test_runtime_has_no_cors_configuration_or_headers() -> None:
    response = _client().get("/health", headers={"Origin": "https://frontend.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


async def test_app_error_handler_returns_status_and_detail_from_exception() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/health", "headers": []})

    response = await main.app_error_handler(request, NotFoundError("resource is gone"))
    generic = await main.app_error_handler(request, AppError("unexpected"))

    assert (response.status_code, response.body) == (404, b'{"detail":"resource is gone"}')
    assert generic.status_code == 500


def test_validation_error_handler_returns_400_with_error_list() -> None:
    @main.app.get("/_validation-probe")
    async def _validation_probe(required_value: int) -> dict[str, int]:  # pragma: no cover - route body
        return {"value": required_value}

    try:
        response = _client().get("/_validation-probe")
    finally:
        main.app.router.routes = [
            route for route in main.app.router.routes if getattr(route, "path", None) != "/_validation-probe"
        ]
        main.app.openapi_schema = None

    assert response.status_code == 400
    detail: list[dict[str, Any]] = response.json()["detail"]
    assert isinstance(detail, list) and detail
    assert detail[0]["type"] == "missing"


def test_openapi_document_exposes_health_and_the_vk_domain() -> None:
    response = _client().get("/openapi.json")

    assert response.status_code == 200
    assert set(response.json()["paths"]) == {
        "/health",
        "/vks",
        "/vks/bot-info",
        "/vks/issue-confirmation",
        "/vks/{user_id}",
    }


def test_openapi_document_exposes_no_inherited_collection_paths() -> None:
    response = _client().get("/openapi.json")

    assert not [path for path in response.json()["paths"] if path.startswith(INHERITED_COLLECTION)]
