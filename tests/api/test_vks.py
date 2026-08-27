"""HTTP-контракт VK-домена: статусы, идемпотентность, валидация, секреты."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
import settings as settings_module
from api.dependencies import get_vk_binding_service
from core.services.vk_binding import VkBindingService
from repositories.user_vk import STATE_ACTIVE, STATE_BLOCKED, STATE_PENDING
from tests.fakes import (
    FakeUserVkRepository,
    FakeVkConfirmationRepository,
    FakeVkLogRepository,
    RecordingMessenger,
)

GROUP_ID = 224466
GROUP_SCREEN_NAME = "eqsitecms_bot"
REAL_TOKEN = "vk1.a.1f2e3d4c5b6a79880011"
UNKNOWN_UUID = str(uuid4())


class _Fixture:
    def __init__(self) -> None:
        self.bindings = FakeUserVkRepository()
        self.confirmations = FakeVkConfirmationRepository()
        self.logs = FakeVkLogRepository()

    def service(self, messenger: RecordingMessenger | None = None) -> VkBindingService:
        return VkBindingService(
            user_vk_repo=self.bindings,
            confirmation_repo=self.confirmations,
            vk_log_repo=self.logs,
            ttl_minutes=30,
            code_length=8,
            messenger=messenger,
        )


@pytest.fixture
def storage() -> Iterator[_Fixture]:
    fixture = _Fixture()
    main.app.dependency_overrides[get_vk_binding_service] = lambda: fixture.service()
    try:
        yield fixture
    finally:
        main.app.dependency_overrides.pop(get_vk_binding_service, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def configured_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.vk_settings, "vk_group_id", GROUP_ID)
    monkeypatch.setattr(settings_module.vk_settings, "vk_group_screen_name", GROUP_SCREEN_NAME)
    monkeypatch.setattr(settings_module.vk_settings, "vk_group_token", REAL_TOKEN)


def test_collection_read_returns_active_bindings(storage: _Fixture, client: TestClient) -> None:
    active = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=101, vk_screen_name="one")
    pending = storage.bindings.seed(state=STATE_PENDING)
    user_ids = f"{active['user_id']},{pending['user_id']}"

    response = client.get("/vks", params={"user_ids": user_ids})

    assert response.status_code == 200
    payload = response.json()
    assert {item["state"] for item in payload} == {STATE_ACTIVE, STATE_PENDING}
    assert {item["vk_peer_id"] for item in payload} == {101, None}


def test_collection_read_filters_by_state(storage: _Fixture, client: TestClient) -> None:
    active = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=102)
    pending = storage.bindings.seed(state=STATE_PENDING)
    user_ids = f"{active['user_id']},{pending['user_id']}"

    response = client.get("/vks", params={"user_ids": user_ids, "state": STATE_ACTIVE})

    assert response.status_code == 200
    assert [item["user_id"] for item in response.json()] == [str(active["user_id"])]


def test_collection_read_rejects_an_unknown_state(storage: _Fixture, client: TestClient) -> None:
    response = client.get("/vks", params={"user_ids": UNKNOWN_UUID, "state": "UNLINKED"})

    assert response.status_code == 400
    assert "UNLINKED" in response.json()["detail"]


def test_collection_read_returns_an_empty_list_for_unknown_owners(storage: _Fixture, client: TestClient) -> None:
    response = client.get("/vks", params={"user_ids": UNKNOWN_UUID})

    assert response.status_code == 200
    assert response.json() == []


def test_collection_read_rejects_a_malformed_identifier(storage: _Fixture, client: TestClient) -> None:
    response = client.get("/vks", params={"user_ids": "not-a-uuid"})

    assert response.status_code == 400
    assert "not-a-uuid" in response.json()["detail"]


def test_collection_read_requires_the_owner_filter(storage: _Fixture, client: TestClient) -> None:
    response = client.get("/vks")

    assert response.status_code == 400


def test_collection_read_ignores_deleted_bindings(storage: _Fixture, client: TestClient) -> None:
    row = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=103)
    await_delete = storage.bindings.rows[row["id"]]
    await_delete["deleted_at"] = datetime.now(UTC)

    response = client.get("/vks", params={"user_ids": str(row["user_id"])})

    assert response.json() == []


def test_bot_info_returns_public_group_attributes(client: TestClient, configured_group: None) -> None:
    response = client.get("/vks/bot-info")

    assert response.status_code == 200
    assert response.json() == {
        "group_id": GROUP_ID,
        "group_screen_name": GROUP_SCREEN_NAME,
        "link_command": "/link",
        "group_url": f"https://vk.com/{GROUP_SCREEN_NAME}",
        "dialog_url": f"https://vk.me/{GROUP_SCREEN_NAME}",
    }


def test_bot_info_never_leaks_the_group_token(client: TestClient, configured_group: None) -> None:
    response = client.get("/vks/bot-info")

    assert REAL_TOKEN not in response.text
    assert "token" not in response.text.lower()


def test_bot_info_is_identical_for_anonymous_and_authenticated(configured_group: None) -> None:
    anonymous = TestClient(main.app).get("/vks/bot-info")
    authenticated = TestClient(main.app, cookies={"session": "cms-session-token"}).get("/vks/bot-info")

    assert anonymous.status_code == authenticated.status_code == 200
    assert anonymous.json() == authenticated.json()


def test_bot_info_reports_an_incomplete_configuration(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.vk_settings, "vk_group_screen_name", "")
    monkeypatch.setattr(settings_module.vk_settings, "vk_group_id", 0)

    response = client.get("/vks/bot-info")

    assert response.status_code == 503
    assert "VK" in response.json()["detail"]


def test_create_returns_201_for_a_new_owner(storage: _Fixture, client: TestClient) -> None:
    user_id = str(uuid4())

    response = client.post("/vks", json={"user_id": user_id})

    assert response.status_code == 201
    body = response.json()
    assert (body["user_id"], body["state"], body["vk_peer_id"]) == (user_id, STATE_PENDING, None)


def test_create_is_idempotent_for_an_existing_owner(storage: _Fixture, client: TestClient) -> None:
    user_id = str(uuid4())
    first = client.post("/vks", json={"user_id": user_id})

    second = client.post("/vks", json={"user_id": user_id})

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(storage.bindings.rows) == 1


def test_create_rejects_a_missing_owner(storage: _Fixture, client: TestClient) -> None:
    response = client.post("/vks", json={})

    assert response.status_code == 400


def test_create_rejects_a_malformed_owner(storage: _Fixture, client: TestClient) -> None:
    response = client.post("/vks", json={"user_id": "not-a-uuid"})

    assert response.status_code == 400


def test_issue_confirmation_returns_the_code_and_the_dialog_link(
    storage: _Fixture,
    client: TestClient,
    configured_group: None,
) -> None:
    user_id = str(uuid4())

    response = client.post("/vks/issue-confirmation", json={"user_id": user_id})

    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 8
    assert body["state"] == STATE_PENDING
    assert body["link_command"] == "/link"
    assert body["dialog_url"] == f"https://vk.me/{GROUP_SCREEN_NAME}"
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(UTC) + timedelta(minutes=29)


def test_reissue_invalidates_the_previous_code(storage: _Fixture, client: TestClient) -> None:
    user_id = str(uuid4())
    first = client.post("/vks/issue-confirmation", json={"user_id": user_id}).json()

    second = client.post("/vks/issue-confirmation", json={"user_id": user_id}).json()

    assert first["code"] != second["code"]
    stale = [row for row in storage.confirmations.rows if row["code"] == first["code"]][0]
    assert stale["used_at"] is not None


def test_issue_confirmation_conflicts_with_an_active_binding(storage: _Fixture, client: TestClient) -> None:
    row = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=104)

    response = client.post("/vks/issue-confirmation", json={"user_id": str(row["user_id"])})

    assert response.status_code == 409
    assert "привязан" in response.json()["detail"]


def test_issue_confirmation_conflicts_with_a_blocked_bot(storage: _Fixture, client: TestClient) -> None:
    row = storage.bindings.seed(state=STATE_BLOCKED, vk_peer_id=105)

    response = client.post("/vks/issue-confirmation", json={"user_id": str(row["user_id"])})

    assert response.status_code == 409
    assert "сообщения" in response.json()["detail"]


def test_issue_confirmation_rejects_a_malformed_body(storage: _Fixture, client: TestClient) -> None:
    response = client.post("/vks/issue-confirmation", json={"user_id": "not-a-uuid"})

    assert response.status_code == 400


def test_issue_confirmation_never_journals_the_full_code(storage: _Fixture, client: TestClient) -> None:
    code = client.post("/vks/issue-confirmation", json={"user_id": str(uuid4())}).json()["code"]

    serialized = " ".join(str(entry["details"]) for entry in storage.logs.entries)
    assert code not in serialized


def test_delete_removes_an_existing_binding(storage: _Fixture, client: TestClient) -> None:
    row = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=106)

    response = client.delete(f"/vks/{row['user_id']}")

    assert response.status_code == 204
    assert storage.bindings.rows[row["id"]]["deleted_at"] is not None


def test_delete_invalidates_pending_codes(storage: _Fixture, client: TestClient) -> None:
    user_id = str(uuid4())
    client.post("/vks/issue-confirmation", json={"user_id": user_id})

    client.delete(f"/vks/{user_id}")

    assert [row["used_at"] is not None for row in storage.confirmations.rows] == [True]


def test_delete_is_idempotent(storage: _Fixture, client: TestClient) -> None:
    user_id = str(uuid4())

    first = client.delete(f"/vks/{user_id}")
    second = client.delete(f"/vks/{user_id}")

    assert (first.status_code, second.status_code) == (204, 204)


def test_delete_rejects_a_malformed_identifier(storage: _Fixture, client: TestClient) -> None:
    response = client.delete("/vks/not-a-uuid")

    assert response.status_code == 400


def test_delete_notifies_an_active_binding_in_vk(storage: _Fixture, client: TestClient) -> None:
    messenger = RecordingMessenger()
    row = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=777001)
    main.app.dependency_overrides[get_vk_binding_service] = lambda: storage.service(messenger=messenger)

    response = client.delete(f"/vks/{row['user_id']}")

    assert response.status_code == 204
    assert [peer for peer, _ in messenger.sent] == [777001]


def test_delete_does_not_notify_a_blocked_binding(storage: _Fixture, client: TestClient) -> None:
    messenger = RecordingMessenger()
    row = storage.bindings.seed(state=STATE_BLOCKED, vk_peer_id=777002)
    main.app.dependency_overrides[get_vk_binding_service] = lambda: storage.service(messenger=messenger)

    response = client.delete(f"/vks/{row['user_id']}")

    assert response.status_code == 204
    assert messenger.sent == []


def test_delete_survives_a_failing_vk_notification(storage: _Fixture, client: TestClient) -> None:
    row = storage.bindings.seed(state=STATE_ACTIVE, vk_peer_id=777003)
    main.app.dependency_overrides[get_vk_binding_service] = lambda: storage.service(
        messenger=RecordingMessenger(fail=True)
    )

    response = client.delete(f"/vks/{row['user_id']}")

    assert response.status_code == 204
    assert storage.bindings.rows[row["id"]]["deleted_at"] is not None


def test_the_wired_dependency_supplies_a_real_messenger() -> None:
    import inspect

    from api import dependencies

    source = inspect.getsource(dependencies.get_vk_binding_service)

    assert "messenger=None" not in source
    assert "VkbottleMessenger" in source


def test_no_public_confirm_route_is_registered(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/vks/confirm" not in paths
    assert not [path for path in paths if path.endswith("/confirm")]
