from __future__ import annotations

from collections.abc import Sequence

from .embeddings import estimate_tokens
from .models import ContextEvent

EVENT_TRUNCATION_MARKER = " … [full event remains in the Library]"
MESSAGE_TRUNCATION_MARKER = " … [full message remains in the Library]"


def message_token_count(messages: Sequence[dict[str, str]]) -> int:
    """Estimate message content plus a small per-message framing allowance."""

    return sum(estimate_tokens(message["content"]) + 4 for message in messages)


def truncate_text(text: str, token_budget: int, *, marker: str) -> str:
    """Return text that fits the estimated budget and identifies stored overflow."""

    if token_budget <= 0:
        return ""
    if estimate_tokens(text) <= token_budget:
        return text
    marker_tokens = estimate_tokens(marker)
    if token_budget <= marker_tokens:
        return text[: token_budget * 4]
    return text[: (token_budget - marker_tokens) * 4].rstrip() + marker


def select_event_tail(
    events: Sequence[ContextEvent],
    token_budget: int,
    *,
    excluded: set[str] | None = None,
) -> tuple[list[tuple[ContextEvent, str]], int]:
    """Select the newest events that fit, preserving chronological order."""

    excluded_ids = excluded or set()
    selected: list[tuple[ContextEvent, str]] = []
    used = 0
    for event in reversed(events):
        if event.event_id in excluded_ids:
            continue
        available = token_budget - used - 4
        if available <= 0:
            break
        if event.token_count > available:
            if selected:
                break
            content = truncate_text(
                event.content,
                available,
                marker=EVENT_TRUNCATION_MARKER,
            )
        else:
            content = event.content
        cost = estimate_tokens(content) + 4
        if used + cost > token_budget:
            break
        selected.append((event, content))
        used += cost
    selected.reverse()
    return selected, used
