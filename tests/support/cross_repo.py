"""
Доступ к AsyncAPI-документам соседних сервисов.

Контрактные тесты сверяют схемы двух сервисов, но сервисы живут в отдельных
git-репозиториях. В монорепе соседний документ доступен по относительному
пути, в CI отдельного репозитория его нет. Чтобы отсутствие соседа не роняло
`make test` и одновременно не превращалось в молча пропущенную проверку,
поведение зависит от контекста запуска:

* сосед недоступен, `EQCMS_MONOREPO != "1"` -> явный `skip` с указанием пути;
* сосед недоступен, `EQCMS_MONOREPO == "1"` -> падение, потому что в монорепе
  документ обязан существовать.
"""

import os
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

MONOREPO_ENV_VAR = "EQCMS_MONOREPO"

SERVICE_ROOT = Path(__file__).resolve().parents[2]


def is_monorepo_run() -> bool:
    return os.getenv(MONOREPO_ENV_VAR) == "1"


def sibling_service_root(service_name: str) -> Path:
    return SERVICE_ROOT.parent / service_name


def load_sibling_asyncapi(service_name: str) -> dict[str, Any]:
    """AsyncAPI соседнего сервиса; skip вне монорепы, падение внутри неё."""
    path = sibling_service_root(service_name) / "docs" / "asyncapi.yaml"

    if not path.exists():
        message = f"AsyncAPI сервиса {service_name} недоступен по пути {path}"
        if is_monorepo_run():
            raise AssertionError(f"{message}: в монорепе документ обязан существовать")
        pytest.skip(f"{message}: кросс-репозиторная проверка пропущена вне монорепы")

    document: dict[str, Any] = yaml.safe_load(path.read_text())
    return document
