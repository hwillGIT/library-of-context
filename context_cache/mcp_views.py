from __future__ import annotations

import json
from typing import Any

from .models import (
    PromptEnvelope,
)
from .transport_views import (
    book_view,
    desk_view,
    event_view,
    governed_prompt_view,
    hit_view,
    search_view,
)

__all__ = [
    "book_view",
    "desk_view",
    "event_view",
    "governed_prompt_view",
    "hit_view",
    "prompt_view",
    "search_view",
    "tool_error",
    "tool_result",
]


def prompt_view(envelope: PromptEnvelope) -> dict[str, Any]:
    return {
        "session_id": envelope.session_id,
        "collection": envelope.collection,
        "messages": envelope.messages,
        "token_count": envelope.token_count,
        "token_budget": envelope.token_budget,
        "history_books": envelope.history_books,
        "recent_books": envelope.recent_books,
        "desk": desk_view(envelope.desk),
    }


def tool_result(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": value,
        "isError": False,
    }


def tool_error(exc: Exception) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"Library tool error: {exc}"}],
        "isError": True,
    }
