from __future__ import annotations


class NormalizationError(Exception):
    pass


def normalize_input(text: str, max_bytes: int = 8192) -> str:
    """Normalize user input: strip, check length."""
    stripped = text.strip()
    if not stripped:
        raise NormalizationError("empty_input")
    if len(stripped.encode("utf-8")) > max_bytes:
        raise NormalizationError("input_too_large")
    return stripped
