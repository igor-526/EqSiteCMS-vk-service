"""Контрольная строка привязки: алфавит, длина, нормализация, маскирование."""

import pytest

from core.services.vk_code import (
    CODE_ALPHABET,
    generate_code,
    mask_code,
    normalize_code,
)

AMBIGUOUS_CHARS = ("I", "O", "0", "1")


def test_alphabet_excludes_ambiguous_characters() -> None:
    assert not [char for char in AMBIGUOUS_CHARS if char in CODE_ALPHABET]
    assert len(CODE_ALPHABET) == 32


def test_generated_code_has_requested_length_and_allowed_alphabet() -> None:
    code = generate_code(8)

    assert len(code) == 8
    assert set(code) <= set(CODE_ALPHABET)


def test_thousand_codes_never_contain_ambiguous_characters() -> None:
    codes = [generate_code(8) for _ in range(1000)]

    assert not [code for code in codes if any(char in code for char in AMBIGUOUS_CHARS)]


def test_consecutive_codes_differ() -> None:
    assert generate_code(8) != generate_code(8)


def test_generation_uses_the_cryptographic_secrets_module() -> None:
    source = (__import__("pathlib").Path(__file__).resolve().parents[2] / "src/core/services/vk_code.py").read_text(
        encoding="utf-8"
    )

    assert "import secrets" in source
    assert "secrets.choice" in source
    assert "import random" not in source


@pytest.mark.parametrize("length", [0, -1])
def test_non_positive_length_is_rejected(length: int) -> None:
    with pytest.raises(ValueError):
        generate_code(length)


def test_normalization_strips_spaces_and_upcases() -> None:
    assert normalize_code("  abc23xyz  ") == "ABC23XYZ"


def test_masking_hides_everything_but_the_first_characters() -> None:
    masked = mask_code("ABC23XYZ")

    assert masked.startswith("AB")
    assert "C23XYZ" not in masked
    assert "len=8" in masked
