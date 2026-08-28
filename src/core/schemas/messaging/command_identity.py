import uuid

# Зеркало `notification-service/src/core/schemas/messaging/command_identity.py`: producer и consumer
# обязаны вычислять `Nats-Msg-Id` одинаково. Дедупликация JetStream действует на уровне stream,
# поэтому идентификатор выводится из пары «корреляция + канал», а не из одного `callback_request_id`.
NAMESPACE_NOTIFICATION_COMMAND = uuid.uuid5(uuid.NAMESPACE_DNS, "notification-commands.eqcms")

VK_CHANNEL_CODE = "vk"


def build_command_msg_id(*, correlation_id: uuid.UUID, channel_code: str) -> uuid.UUID:
    """Детерминированный `Nats-Msg-Id` команды канала."""
    return uuid.uuid5(NAMESPACE_NOTIFICATION_COMMAND, f"{correlation_id}:{channel_code}")
