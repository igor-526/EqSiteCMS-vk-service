import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from os import environ
from uuid import UUID

from smoke_harness.messenger import ScriptedPlan
from smoke_harness.topology import SmokeTopology


class SmokeHarnessGuardError(ValueError):
    """The local-only smoke harness guard rejected its configuration."""


class SmokeScenario(StrEnum):
    MALFORMED = "malformed"
    UNKNOWN_USER = "unknown-user"
    PENDING_BINDING = "pending-binding"
    BLOCKED_BINDING = "blocked-binding"
    SOFT_DELETED_BINDING = "soft-deleted-binding"
    REPEATED_EVENT = "repeated-event"
    CONCURRENT_DUPLICATE = "concurrent-duplicate"
    DELIVERY_FAILURE = "delivery-failure"
    PARTIAL_RETRY = "partial-retry"


@dataclass(frozen=True)
class SmokeHarnessConfig:
    scenario: SmokeScenario
    run_id: UUID
    target_user_ids: tuple[UUID, ...]
    plans: Mapping[UUID, ScriptedPlan]
    event_uuid: UUID
    callback_request_id: UUID

    @property
    def topology(self) -> SmokeTopology:
        return SmokeTopology.for_run(self.run_id)

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> SmokeHarnessConfig:
        values = environ if env is None else env
        if values.get("EQSITECMS_SMOKE_HARNESS") != "1":
            raise SmokeHarnessGuardError("smoke harness is disabled")
        if values.get("EQSITECMS_ENVIRONMENT", "").strip().lower() != "local":
            raise SmokeHarnessGuardError("smoke harness requires local environment")

        run_id = _required_uuid(values, "EQSITECMS_SMOKE_RUN_ID")
        event_uuid = _required_uuid(values, "EQSITECMS_SMOKE_EVENT_ID")
        callback_request_id = _required_uuid(values, "EQSITECMS_SMOKE_CALLBACK_REQUEST_ID")
        try:
            scenario = SmokeScenario(values.get("EQSITECMS_SMOKE_SCENARIO", ""))
        except ValueError as exc:
            raise SmokeHarnessGuardError("a supported exact smoke scenario is required") from exc
        targets = _uuid_list(values.get("EQSITECMS_SMOKE_SYNTHETIC_TARGETS", ""))
        if not targets:
            raise SmokeHarnessGuardError("at least one exact synthetic target is required")

        raw_plans = values.get("EQSITECMS_SMOKE_SCRIPTED_PLANS", "")
        try:
            decoded = json.loads(raw_plans)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SmokeHarnessGuardError("scripted plans must be a JSON object") from exc
        if not isinstance(decoded, dict) or set(decoded) != {str(target) for target in targets}:
            raise SmokeHarnessGuardError("scripted plans must exactly match synthetic targets")
        try:
            plans = {UUID(user_id): ScriptedPlan(plan) for user_id, plan in decoded.items()}
        except (ValueError, TypeError) as exc:
            raise SmokeHarnessGuardError("scripted plans contain an invalid target or outcome") from exc
        _validate_scenario_plan(scenario=scenario, targets=targets, plans=plans)

        return cls(
            scenario=scenario,
            run_id=run_id,
            target_user_ids=targets,
            plans=plans,
            event_uuid=event_uuid,
            callback_request_id=callback_request_id,
        )


def _required_uuid(values: Mapping[str, str], name: str) -> UUID:
    raw = values.get(name, "").strip()
    try:
        value = UUID(raw)
    except ValueError as exc:
        raise SmokeHarnessGuardError(f"{name} must be an exact UUID") from exc
    if value.int == 0:
        raise SmokeHarnessGuardError(f"{name} must not be nil")
    return value


def _uuid_list(raw: str) -> tuple[UUID, ...]:
    if not raw.strip() or any(marker in raw for marker in ("*", ">")):
        return ()
    try:
        values = tuple(UUID(item.strip()) for item in raw.split(","))
    except ValueError as exc:
        raise SmokeHarnessGuardError("synthetic targets must be exact UUIDs") from exc
    if any(value.int == 0 for value in values) or len(set(values)) != len(values):
        raise SmokeHarnessGuardError("synthetic targets must be unique non-nil UUIDs")
    return values


def _validate_scenario_plan(
    *, scenario: SmokeScenario, targets: tuple[UUID, ...], plans: Mapping[UUID, ScriptedPlan]
) -> None:
    if scenario is SmokeScenario.PARTIAL_RETRY:
        if len(targets) != 2 or sorted(plans.values()) != sorted(
            (ScriptedPlan.SUCCESS, ScriptedPlan.FAIL_FIRST_THEN_SUCCESS)
        ):
            raise SmokeHarnessGuardError("partial-retry requires one success and one fail-first target")
    elif scenario is SmokeScenario.DELIVERY_FAILURE:
        if len(targets) != 1 or plans[targets[0]] is not ScriptedPlan.FAIL_ALWAYS:
            raise SmokeHarnessGuardError("delivery-failure requires one fail-always target")
    elif any(plan is not ScriptedPlan.SUCCESS for plan in plans.values()):
        raise SmokeHarnessGuardError("this scenario permits only success scripted plans")
