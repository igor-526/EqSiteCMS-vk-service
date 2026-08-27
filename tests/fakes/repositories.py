"""Фейковые репозитории и мессенджер для тестов домена без PostgreSQL и VK."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from core.exceptions import AlreadyExistsError
from core.protocols.vk import VkUserProfile
from repositories.user_vk import ALLOWED_STATES, STATE_ACTIVE, STATE_PENDING


class FakeUserVkRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict] = {}

    def seed(self, **overrides: object) -> dict:
        now = datetime.now(UTC)
        row: dict = {
            "id": uuid4(),
            "user_id": uuid4(),
            "vk_peer_id": None,
            "state": STATE_PENDING,
            "vk_screen_name": None,
            "vk_display_name": None,
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        row.update(overrides)
        self.rows[row["id"]] = row
        return row

    async def create(self, *, user_id: UUID) -> dict:
        if any(r["user_id"] == user_id and r["deleted_at"] is None for r in self.rows.values()):
            raise AlreadyExistsError(f"VK binding already exists for user_id={user_id}")
        return self.seed(user_id=user_id)

    async def get_by_id(self, *, record_id: UUID) -> dict | None:
        return self.rows.get(record_id)

    async def get_by_user_id(self, *, user_id: UUID) -> dict | None:
        for row in self.rows.values():
            if row["user_id"] == user_id and row["deleted_at"] is None:
                return row
        return None

    async def get_by_user_ids(self, *, user_ids: list[UUID], state: str | None = None) -> list[dict]:
        return [
            row
            for row in self.rows.values()
            if row["user_id"] in user_ids and row["deleted_at"] is None and (state is None or row["state"] == state)
        ]

    async def get_by_peer_id(self, *, vk_peer_id: int) -> dict | None:
        for row in self.rows.values():
            if row["vk_peer_id"] == vk_peer_id and row["deleted_at"] is None:
                return row
        return None

    async def activate(
        self,
        *,
        record_id: UUID,
        vk_peer_id: int,
        vk_screen_name: str | None = None,
        vk_display_name: str | None = None,
    ) -> dict:
        occupied = await self.get_by_peer_id(vk_peer_id=vk_peer_id)
        if occupied is not None and occupied["id"] != record_id:
            raise AlreadyExistsError(f"VK account already linked: vk_peer_id={vk_peer_id}")
        row = self.rows[record_id]
        row.update(
            vk_peer_id=vk_peer_id,
            state=STATE_ACTIVE,
            vk_screen_name=vk_screen_name,
            vk_display_name=vk_display_name,
            updated_at=datetime.now(UTC),
        )
        return row

    async def set_state(self, *, record_id: UUID, state: str) -> dict | None:
        if state not in ALLOWED_STATES:
            raise ValueError(f"Unsupported VK binding state: {state}")
        row = self.rows.get(record_id)
        if row is None or row["deleted_at"] is not None:
            return None
        row.update(state=state, updated_at=datetime.now(UTC))
        return row

    async def soft_delete(self, *, user_id: UUID) -> bool:
        row = await self.get_by_user_id(user_id=user_id)
        if row is None:
            return False
        row.update(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        return True


class FakeVkConfirmationRepository:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.reserved_codes: set[str] = set()

    async def create(self, *, user_vk_id: UUID, code: str, expires_at: datetime) -> dict:
        if any(row["code"] == code for row in self.rows):
            raise AlreadyExistsError(f"Confirmation code already exists: {code}")
        row = {
            "id": uuid4(),
            "user_vk_id": user_vk_id,
            "code": code,
            "expires_at": expires_at,
            "created_at": datetime.now(UTC),
            "used_at": None,
        }
        self.rows.append(row)
        return row

    async def get_by_code(self, *, code: str) -> dict | None:
        if code in self.reserved_codes:
            return {
                "id": uuid4(),
                "user_vk_id": uuid4(),
                "code": code,
                "expires_at": datetime.now(UTC),
                "created_at": datetime.now(UTC),
                "used_at": datetime.now(UTC),
            }
        for row in self.rows:
            if row["code"] == code:
                return row
        return None

    async def mark_used(self, *, confirmation_id: UUID) -> None:
        for row in self.rows:
            if row["id"] == confirmation_id:
                row["used_at"] = datetime.now(UTC)

    async def invalidate_previous(self, *, user_vk_id: UUID) -> int:
        invalidated = 0
        for row in self.rows:
            if row["user_vk_id"] == user_vk_id and row["used_at"] is None:
                row["used_at"] = datetime.now(UTC)
                invalidated += 1
        return invalidated


class FakeVkLogRepository:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def log_action(self, *, action: str, status: str, details: dict | None = None) -> dict:
        row = {
            "id": uuid4(),
            "event_uuid": uuid4(),
            "action": action,
            "status": status,
            "details": details or {},
            "created_at": datetime.now(UTC),
        }
        self.entries.append(row)
        return row

    async def count_failed_since(
        self,
        *,
        action: str,
        vk_peer_id: int,
        since: datetime,
        failed_statuses: tuple[str, ...],
    ) -> int:
        def is_failed_attempt(row: dict) -> bool:
            if row["action"] != action or row["status"] not in failed_statuses:
                return False
            if row["created_at"] < since:
                return False
            return bool(row["details"].get("vk_peer_id") == str(vk_peer_id))

        return len([row for row in self.entries if is_failed_attempt(row)])

    def statuses_for(self, action: str) -> list[str]:
        return [row["status"] for row in self.entries if row["action"] == action]


class RecordingMessenger:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail = fail

    async def send_message(self, *, peer_id: int, text: str) -> bool:
        if self.fail:
            raise RuntimeError("VK API is unavailable")
        self.sent.append((peer_id, text))
        return True

    async def get_profile(self, *, peer_id: int) -> VkUserProfile | None:
        return VkUserProfile(peer_id=peer_id, screen_name="probe", display_name="Probe User")
