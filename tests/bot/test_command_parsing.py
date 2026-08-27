"""Разбор команды привязки и распознавание беседы."""

import pytest

from bot.handlers import is_chat_peer, parse_link_command

COMMAND = "/link"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/link ABC23XYZ", "ABC23XYZ"),
        ("  /LINK   abc23xyz  ", "abc23xyz"),
        ("/Link\tABC23XYZ", "ABC23XYZ"),
        ("/link ABC23XYZ extra", "ABC23XYZ"),
    ],
)
def test_command_with_a_code_is_parsed(text: str, expected: str) -> None:
    assert parse_link_command(text=text, link_command=COMMAND) == expected


@pytest.mark.parametrize("text", ["/link", "  /LINK  "])
def test_command_without_a_code_returns_an_empty_string(text: str) -> None:
    assert parse_link_command(text=text, link_command=COMMAND) == ""


@pytest.mark.parametrize("text", ["привет", "", "   ", "link ABC23XYZ", "//link ABC23XYZ"])
def test_non_command_text_returns_none(text: str) -> None:
    assert parse_link_command(text=text, link_command=COMMAND) is None


def test_a_custom_command_is_honoured() -> None:
    assert parse_link_command(text="!bind CODE1234", link_command="!bind") == "CODE1234"
    assert parse_link_command(text="/link CODE1234", link_command="!bind") is None


def test_conversation_peers_are_recognised() -> None:
    assert is_chat_peer(2_000_000_001) is True
    assert is_chat_peer(2_000_000_000) is True
    assert is_chat_peer(424242) is False
