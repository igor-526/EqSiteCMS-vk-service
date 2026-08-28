from pathlib import Path

import yaml  # type: ignore[import-untyped]

from core.schemas.messaging import NotificationCommandSendVkData
from tests.support.cross_repo import load_sibling_asyncapi


def test_ut09_notification_and_vk_asyncapi_payload_and_headers_are_equal() -> None:
    service_root = Path(__file__).parents[3]
    notification = load_sibling_asyncapi("notification-service")
    vk = yaml.safe_load((service_root / "docs/asyncapi.yaml").read_text())
    notification_schemas = notification["components"]["schemas"]
    vk_schemas = vk["components"]["schemas"]
    assert notification_schemas["NotificationVkPayload"] == vk_schemas["NotificationVkPayload"]
    assert notification_schemas["IdempotencyHeaders"] == vk_schemas["IdempotencyHeaders"]
    schema = vk["components"]["schemas"]["NotificationVkPayload"]
    assert set(NotificationCommandSendVkData.model_fields) == set(schema["properties"])
