from collections import defaultdict
from collections.abc import Mapping
from enum import StrEnum

from core.protocols.vk import VkUserProfile


class ScriptedPlan(StrEnum):
    SUCCESS = "success"
    FAIL_ALWAYS = "fail-always"
    FAIL_FIRST_THEN_SUCCESS = "fail-first-then-success"


class ScriptedVkMessenger:
    """Deterministic in-memory messenger. It never imports or calls a VK client."""

    def __init__(self, *, peer_plans: Mapping[int, ScriptedPlan]) -> None:
        self._plans = dict(peer_plans)
        self._attempts: defaultdict[int, int] = defaultdict(int)

    @property
    def total_attempts(self) -> int:
        return sum(self._attempts.values())

    def attempts_for(self, peer_id: int) -> int:
        return self._attempts[peer_id]

    async def send_message(self, *, peer_id: int, text: str) -> bool:
        del text
        plan = self._plans.get(peer_id)
        if plan is None:
            return False
        self._attempts[peer_id] += 1
        if plan is ScriptedPlan.SUCCESS:
            return True
        if plan is ScriptedPlan.FAIL_FIRST_THEN_SUCCESS:
            return self._attempts[peer_id] > 1
        return False

    async def get_profile(self, *, peer_id: int) -> VkUserProfile | None:
        del peer_id
        return None
