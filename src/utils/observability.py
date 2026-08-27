from dataclasses import dataclass
from threading import Thread
from typing import Protocol

from prometheus_client import REGISTRY, CollectorRegistry, start_http_server


class MetricsServer(Protocol):
    def shutdown(self) -> None: ...
    def server_close(self) -> None: ...


@dataclass
class MetricsRuntime:
    server: MetricsServer
    thread: Thread
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def start_metrics_runtime(*, environment: str, registry: CollectorRegistry = REGISTRY) -> MetricsRuntime | None:
    if environment.lower() != "production":
        return None
    server, thread = start_http_server(port=9000, addr="0.0.0.0", registry=registry)
    return MetricsRuntime(server=server, thread=thread)
