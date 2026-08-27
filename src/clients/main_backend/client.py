"""HTTP-клиент для взаимодействия с main backend сервисом."""

from uuid import UUID

import aiohttp
from pydantic import BaseModel, Field

from core.exceptions.base import AppError

# === Exceptions ===


class MainBackendClientError(AppError):
    """Базовая ошибка HTTP-клиента main backend."""

    status_code = 500


class MainBackendConnectionError(MainBackendClientError):
    """Ошибка соединения с main backend."""

    status_code = 500

    def __init__(self, detail: str = "Не удалось подключиться к main backend") -> None:
        super().__init__(message=detail)


class MainBackendTimeoutError(MainBackendClientError):
    """Таймаут запроса к main backend."""

    status_code = 500

    def __init__(self, detail: str = "Превышено время ожидания ответа от main backend") -> None:
        super().__init__(message=detail)


class MainBackendResponseError(MainBackendClientError):
    """Ошибка ответа от main backend."""

    status_code = 500

    def __init__(self, status: int, detail: str = "Ошибка при запросе к main backend") -> None:
        self.status = status
        super().__init__(message=f"{detail} (HTTP {status})")


# === DTOs ===


class UserScope(BaseModel):
    """Модель scope пользователя."""

    id: UUID
    scope_name: str


class UserOutDto(BaseModel):
    """DTO пользователя из main backend."""

    id: UUID
    equestrian_id: UUID
    username: str
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    created_at: str
    updated_at: str | None = None
    scopes: list[UserScope] = Field(default_factory=list)


class PaginatedUsers(BaseModel):
    """Пагинированный список пользователей."""

    items: list[UserOutDto] = Field(default_factory=list)
    total: int = 0


# === Client ===


class MainBackendClient:
    """HTTP-клиент для main backend сервиса."""

    def __init__(self, base_url: str, service_key: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def get_users(
        self,
        *,
        equestrian_ids: list[UUID] | None = None,
        equestrian_service_keys: list[str] | None = None,
        role: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> PaginatedUsers:
        """
        Получить список пользователей с фильтрацией и пагинацией.

        Args:
            equestrian_ids: Фильтр по ID наездников
            equestrian_service_keys: Фильтр по service key наездников
            role: Фильтр по ролям (scope names)
            limit: Количество элементов на странице
            offset: Смещение

        Returns:
            PaginatedUsers с items и total

        Raises:
            MainBackendConnectionError: Ошибка соединения
            MainBackendTimeoutError: Таймаут запроса
            MainBackendResponseError: Ошибка ответа от сервера
        """
        url = f"{self._base_url}/api/service/users"

        # Формируем query параметры
        params: dict = {
            "limit": limit,
            "offset": offset,
        }

        if equestrian_ids:
            params["equestrian_ids"] = [str(id) for id in equestrian_ids]
        if equestrian_service_keys:
            params["equestrian_service_keys"] = equestrian_service_keys
        if role:
            params["role"] = role

        headers = {
            "X-Service-Key": self._service_key,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        raise MainBackendResponseError(
                            status=response.status,
                            detail=f"Ошибка получения пользователей: {text}",
                        )

                    data = await response.json()
                    return PaginatedUsers.model_validate(data)

        except aiohttp.ClientError as e:
            raise MainBackendConnectionError(detail=f"Ошибка соединения с main backend: {e!s}") from e
        except TimeoutError as e:
            raise MainBackendTimeoutError(detail="Превышено время ожидания ответа от main backend") from e
