from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SmokeTopology:
    stream: str
    subject: str
    durable: str

    @classmethod
    def for_run(cls, run_id: UUID) -> SmokeTopology:
        suffix = run_id.hex
        return cls(
            stream=f"EQSITECMS_SMOKE_VK_{suffix.upper()}",
            subject=f"smoke.eqsitecms.vk.{suffix}.send",
            durable=f"eqsitecms-smoke-vk-{suffix}",
        )

    def assert_isolated(self) -> None:
        if self.stream == "NOTIFICATION_COMMANDS":
            raise ValueError("production stream is forbidden")
        if self.subject == "commands.notification.vk.send" or not self.subject.startswith("smoke.eqsitecms.vk."):
            raise ValueError("production subject is forbidden")
        if self.durable == "vk-service-commands-send-vk" or not self.durable.startswith("eqsitecms-smoke-vk-"):
            raise ValueError("production durable is forbidden")
