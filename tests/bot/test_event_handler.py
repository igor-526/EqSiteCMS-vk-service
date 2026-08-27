"""Обработка событий бота против фейкового VK API и фейковых репозиториев."""

from datetime import UTC, datetime, timedelta

import pytest

import bot.replies as replies
from bot.handlers import BotEventHandler, Services
from core.services.vk_confirmation import ACTION_CONFIRMATION, VkConfirmationService
from core.services.vk_state import ACTION_ALLOW, ACTION_DENY, VkStateService
from repositories.user_vk import STATE_ACTIVE, STATE_BLOCKED, STATE_PENDING
from settings import VkSettings
from tests.fakes import (
    FakeUserVkRepository,
    FakeVkConfirmationRepository,
    FakeVkLogRepository,
    RecordingMessenger,
)

PEER_ID = 424242
CHAT_PEER_ID = 2_000_000_005
CODE = "ABC23XYZ"
MAX_ATTEMPTS = 5


class _World:
    def __init__(self) -> None:
        self.bindings = FakeUserVkRepository()
        self.confirmations = FakeVkConfirmationRepository()
        self.logs = FakeVkLogRepository()
        self.messenger = RecordingMessenger()
        self.commits = 0
        self.settings = VkSettings(_env_file=None, VK_BOT_LINK_COMMAND="/link")  # type: ignore[call-arg]

    async def services(self) -> Services:
        async def commit() -> None:
            self.commits += 1

        return Services(
            confirmation=VkConfirmationService(
                confirmation_repo=self.confirmations,
                user_vk_repo=self.bindings,
                vk_log_repo=self.logs,
                max_attempts=MAX_ATTEMPTS,
                attempt_window_minutes=10,
            ),
            state=VkStateService(user_vk_repo=self.bindings, vk_log_repo=self.logs),
            log_action=self.logs.log_action,
            commit=commit,
        )

    def handler(self) -> BotEventHandler:
        return BotEventHandler(
            settings=self.settings,
            services=self.services,
            messenger=self.messenger,
        )

    @property
    def replies_sent(self) -> list[str]:
        return [text for _, text in self.messenger.sent]


@pytest.fixture
def world() -> _World:
    return _World()


async def _seed_pending_code(world: _World, *, code: str = CODE, minutes: int = 30) -> dict:
    binding = world.bindings.seed(state=STATE_PENDING)
    await world.confirmations.create(
        user_vk_id=binding["id"],
        code=code,
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
    )
    return binding


async def test_valid_command_links_the_account_and_replies(world: _World) -> None:
    binding = await _seed_pending_code(world)

    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    stored = await world.bindings.get_by_id(record_id=binding["id"])
    assert outcome == "confirmed"
    assert stored is not None
    assert (stored["state"], stored["vk_peer_id"]) == (STATE_ACTIVE, PEER_ID)
    assert (stored["vk_screen_name"], stored["vk_display_name"]) == ("probe", "Probe User")
    assert world.replies_sent == [replies.LINKED]
    assert world.commits == 1


async def test_case_and_spacing_are_tolerated(world: _World) -> None:
    await _seed_pending_code(world)

    outcome = await world.handler().handle_message(
        peer_id=PEER_ID,
        from_id=PEER_ID,
        text=f"  /LINK   {CODE.lower()}  ",
    )

    assert outcome == "confirmed"
    assert world.replies_sent == [replies.LINKED]


async def test_unknown_command_replies_with_the_instruction(world: _World) -> None:
    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text="привет")

    assert outcome == "unknown_command"
    assert world.replies_sent == [replies.instruction("/link")]
    assert world.logs.statuses_for("vk_message") == ["unknown_command"]
    assert world.logs.statuses_for(ACTION_CONFIRMATION) == []


async def test_command_without_a_code_never_touches_storage(world: _World) -> None:
    await _seed_pending_code(world)

    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text="/link")

    assert outcome == "missing_code"
    assert world.replies_sent == [replies.instruction("/link")]
    assert world.confirmations.rows[0]["used_at"] is None
    assert world.logs.statuses_for(ACTION_CONFIRMATION) == []


async def test_unknown_code_reports_an_invalid_code(world: _World) -> None:
    await _seed_pending_code(world)

    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text="/link NOSUCH11")

    assert outcome == "not_found"
    assert world.replies_sent == [replies.CODE_INVALID]


async def test_used_code_reports_a_used_code(world: _World) -> None:
    await _seed_pending_code(world)
    handler = world.handler()
    await handler.handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    outcome = await handler.handle_message(peer_id=999, from_id=999, text=f"/link {CODE}")

    assert outcome == "used"
    assert world.replies_sent[-1] == replies.CODE_USED


async def test_expired_code_reports_an_expired_code(world: _World) -> None:
    await _seed_pending_code(world, minutes=-1)

    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    assert outcome == "expired"
    assert world.replies_sent == [replies.CODE_EXPIRED]


async def test_code_from_an_already_linked_account_is_refused(world: _World) -> None:
    world.bindings.seed(state=STATE_ACTIVE, vk_peer_id=PEER_ID)
    victim = await _seed_pending_code(world, code="TAKEN123")

    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text="/link TAKEN123")

    stored = await world.bindings.get_by_id(record_id=victim["id"])
    assert outcome == "peer_conflict"
    assert world.replies_sent == [replies.PEER_CONFLICT]
    assert stored is not None and stored["state"] == STATE_PENDING
    assert world.confirmations.rows[0]["used_at"] is None


async def test_exceeding_the_attempt_limit_stops_code_lookup(world: _World) -> None:
    await _seed_pending_code(world)
    handler = world.handler()
    for _ in range(MAX_ATTEMPTS):
        await handler.handle_message(peer_id=PEER_ID, from_id=PEER_ID, text="/link NOSUCH11")

    outcome = await handler.handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    assert outcome == "rate_limited"
    assert world.replies_sent[-1] == replies.RATE_LIMITED
    assert world.confirmations.rows[0]["used_at"] is None


async def test_conversation_messages_are_ignored(world: _World) -> None:
    await _seed_pending_code(world)

    outcome = await world.handler().handle_message(
        peer_id=CHAT_PEER_ID,
        from_id=PEER_ID,
        text=f"/link {CODE}",
    )

    assert outcome == "ignored_chat"
    assert world.replies_sent == []
    assert world.logs.statuses_for("vk_message") == ["ignored_chat"]
    assert world.confirmations.rows[0]["used_at"] is None


async def test_repeated_delivery_answers_already_linked(world: _World) -> None:
    await _seed_pending_code(world)
    handler = world.handler()
    await handler.handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    outcome = await handler.handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    assert outcome == "already_confirmed"
    assert world.replies_sent[-1] == replies.ALREADY_LINKED
    assert len([row for row in world.bindings.rows.values() if row["deleted_at"] is None]) == 1


async def test_message_deny_blocks_the_binding(world: _World) -> None:
    binding = world.bindings.seed(state=STATE_ACTIVE, vk_peer_id=PEER_ID)

    outcome = await world.handler().handle_message_deny(user_id=PEER_ID)

    stored = await world.bindings.get_by_id(record_id=binding["id"])
    assert outcome == "success"
    assert stored is not None and stored["state"] == STATE_BLOCKED
    assert world.logs.statuses_for(ACTION_DENY) == ["success"]


async def test_message_allow_reactivates_the_binding(world: _World) -> None:
    binding = world.bindings.seed(state=STATE_BLOCKED, vk_peer_id=PEER_ID)

    outcome = await world.handler().handle_message_allow(user_id=PEER_ID)

    stored = await world.bindings.get_by_id(record_id=binding["id"])
    assert outcome == "success"
    assert stored is not None and stored["state"] == STATE_ACTIVE
    assert world.logs.statuses_for(ACTION_ALLOW) == ["success"]


@pytest.mark.parametrize(
    ("method", "action"),
    [("handle_message_deny", ACTION_DENY), ("handle_message_allow", ACTION_ALLOW)],
)
async def test_permission_events_without_a_binding_do_not_raise(world: _World, method: str, action: str) -> None:
    outcome = await getattr(world.handler(), method)(user_id=PEER_ID)

    assert outcome == "no_binding"
    assert world.logs.statuses_for(action) == ["no_binding"]


async def test_a_failed_reply_does_not_break_the_confirmation(world: _World) -> None:
    binding = await _seed_pending_code(world)
    world.messenger = RecordingMessenger()

    async def refusing_send(*, peer_id: int, text: str) -> bool:
        return False

    world.messenger.send_message = refusing_send  # type: ignore[method-assign]

    outcome = await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    stored = await world.bindings.get_by_id(record_id=binding["id"])
    assert outcome == "confirmed"
    assert stored is not None and stored["state"] == STATE_ACTIVE


async def test_the_full_code_never_reaches_the_journal(world: _World) -> None:
    await _seed_pending_code(world)

    await world.handler().handle_message(peer_id=PEER_ID, from_id=PEER_ID, text=f"/link {CODE}")

    serialized = " ".join(str(entry["details"]) for entry in world.logs.entries)
    assert CODE not in serialized
