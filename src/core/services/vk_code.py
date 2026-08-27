"""Генерация человекочитаемой контрольной строки привязки VK."""

import secrets

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_GENERATION_ATTEMPTS = 5
MASK_VISIBLE_CHARS = 2


def generate_code(length: int) -> str:
    """Сгенерировать код заданной длины из однозначно читаемого алфавита."""
    if length <= 0:
        raise ValueError("Confirmation code length must be positive")
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def normalize_code(raw: str) -> str:
    """Привести введённый пользователем код к каноническому виду."""
    return raw.strip().upper()


def mask_code(code: str) -> str:
    """Замаскировать код для журнала: видимы только первые символы и длина."""
    visible = code[:MASK_VISIBLE_CHARS]
    return f"{visible}***(len={len(code)})"
