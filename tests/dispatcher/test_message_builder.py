from __future__ import annotations

from discode.dispatcher.message_builder import (
    MAX_DISCORD_MESSAGE_LEN,
    apply_pattern_redaction,
    build_message,
)


def test_build_message_short_content_returns_single_item() -> None:
    """build_message returns a single item for short content."""
    content = "hello world"
    result = build_message(content)
    assert result == ["hello world"]


def test_build_message_splits_long_content() -> None:
    """build_message splits content exceeding 2000 chars."""
    # Create content longer than 2000 characters
    content = "a" * 2500
    result = build_message(content)
    assert len(result) == 2
    assert len(result[0]) <= MAX_DISCORD_MESSAGE_LEN
    assert len(result[1]) <= MAX_DISCORD_MESSAGE_LEN
    # All content preserved
    assert "".join(result) == content


def test_build_message_splits_at_newline_boundary() -> None:
    """build_message splits at newline boundary if possible."""
    # Build content of 2001 chars with a newline at position 1999
    part1 = "x" * 1999
    part2 = "y" * 100
    content = part1 + "\n" + part2
    result = build_message(content)
    # Should split at the newline (position 1999), so first chunk is part1
    assert result[0] == part1
    assert result[1] == "\n" + part2


def test_build_message_splits_hard_at_2000_when_no_newline() -> None:
    """build_message splits at exactly 2000 chars when no newline found."""
    content = "z" * 2500
    result = build_message(content)
    assert len(result[0]) == MAX_DISCORD_MESSAGE_LEN
    assert result[0] == "z" * 2000
    assert result[1] == "z" * 500


def test_build_message_empty_string() -> None:
    """build_message handles empty string gracefully."""
    result = build_message("")
    assert result == []


def test_build_message_exactly_2000_chars() -> None:
    """build_message returns single chunk for content exactly at the limit."""
    content = "x" * 2000
    result = build_message(content)
    assert result == [content]


def test_apply_pattern_redaction_replaces_token_equals() -> None:
    """apply_pattern_redaction replaces token=value patterns."""
    content = "token=abc123secret"
    result = apply_pattern_redaction(content)
    assert "abc123secret" not in result
    assert "token=[REDACTED]" in result


def test_apply_pattern_redaction_replaces_secret_colon() -> None:
    """apply_pattern_redaction replaces secret: value patterns."""
    content = "secret: mysecretvalue"
    result = apply_pattern_redaction(content)
    assert "mysecretvalue" not in result
    assert "secret=[REDACTED]" in result


def test_apply_pattern_redaction_replaces_password() -> None:
    """apply_pattern_redaction replaces password=value patterns."""
    content = "password=hunter2"
    result = apply_pattern_redaction(content)
    assert "hunter2" not in result
    assert "password=[REDACTED]" in result


def test_apply_pattern_redaction_case_insensitive() -> None:
    """apply_pattern_redaction is case-insensitive."""
    content = "TOKEN=abc123"
    result = apply_pattern_redaction(content)
    assert "abc123" not in result


def test_apply_pattern_redaction_no_match_leaves_content_unchanged() -> None:
    """apply_pattern_redaction leaves non-matching content unchanged."""
    content = "hello world, nothing secret here"
    result = apply_pattern_redaction(content)
    assert result == content
