import asyncio
import json
import sys

from smoke_harness.config import SmokeHarnessConfig, SmokeHarnessGuardError
from smoke_harness.runner import SmokeHarnessResult, run


def sanitized_result(result: SmokeHarnessResult) -> dict:
    return {
        "scenario": result.scenario,
        "recipients": [
            {"recipient": item.recipient, "status": item.status, "attempts": item.attempts}
            for item in result.recipients
        ],
        "redeliveries": result.redeliveries,
    }


def main() -> int:
    try:
        config = SmokeHarnessConfig.from_environment()
    except SmokeHarnessGuardError as exc:
        print(f"VK smoke harness guard rejected configuration: {exc}", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(run(config))
    except Exception:
        print("VK smoke harness failed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            sanitized_result(result),
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
