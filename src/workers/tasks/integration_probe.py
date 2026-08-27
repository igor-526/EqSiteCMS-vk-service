import time
from typing import Protocol, cast

from redis import Redis

from settings import celery_settings
from workers.celery_app import celery_app


class _TaskOptions(Protocol):
    acks_late: bool


@celery_app.task(
    bind=True,
    name="vk.integration_probe",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def integration_probe_task(
    self,
    probe_id: str,
    *,
    fail_until: int = 0,
    delay_seconds: float = 0,
) -> dict[str, bool | int | str]:
    """Deterministic real-broker probe, invoked only by infrastructure tests."""
    redis = Redis.from_url(celery_settings.celery_app_broker, decode_responses=True)
    prefix = f"eqsitecms:integration:{probe_id}"
    attempt = cast(int, redis.incr(f"{prefix}:attempts"))
    redis.set(f"{prefix}:started", attempt)
    if delay_seconds:
        time.sleep(delay_seconds)
    if attempt <= fail_until:
        raise self.retry(countdown=0.1)
    first_effect = redis.set(f"{prefix}:effect", "1", nx=True)
    return {
        "acks_late": cast(_TaskOptions, self).acks_late,
        "attempts": attempt,
        "effect": "created" if first_effect else "duplicate",
    }
