from __future__ import annotations

from typing import Any

MAX_CONTEXT_TOKENS = 128_000
MAX_RESULT_BOOKS = 100


def bounded_integer(
    value: Any,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    """Parse an integer and enforce a public resource boundary."""

    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    resolved = int(value)
    if resolved < minimum or resolved > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return resolved


def bounded_string_tuple(
    value: Any,
    *,
    name: str,
    maximum_items: int,
) -> tuple[str, ...]:
    """Validate a JSON-style string array within a declared item bound."""

    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be an array of strings")
    if len(value) > maximum_items:
        raise ValueError(f"{name} cannot contain more than {maximum_items} items")
    resolved: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{name} must contain only strings")
        if not item.strip():
            raise ValueError(f"{name} cannot contain an empty string")
        resolved.append(item)
    return tuple(resolved)
