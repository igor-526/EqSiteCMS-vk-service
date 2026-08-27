"""HTTP-клиент для main backend."""

from .client import (
    MainBackendClient,
    MainBackendClientError,
    MainBackendConnectionError,
    MainBackendResponseError,
    MainBackendTimeoutError,
    PaginatedUsers,
    UserOutDto,
    UserScope,
)

__all__ = [
    "MainBackendClient",
    "MainBackendClientError",
    "MainBackendConnectionError",
    "MainBackendResponseError",
    "MainBackendTimeoutError",
    "PaginatedUsers",
    "UserOutDto",
    "UserScope",
]
