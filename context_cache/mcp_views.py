from __future__ import annotations

import json
from typing import Any

from .models import (
    ContextEvent,
    ContextRecord,
    GovernedPrompt,
    PromptEnvelope,
    SearchHit,
    WorkingSet,
)


def book_view(record: ContextRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "collection": record.namespace,
        "text": record.text,
        "catalog": record.metadata,
        "source": record.source,
        "importance": record.importance,
        "token_count": record.token_count,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "accessed_at": record.accessed_at,
        "expires_at": record.expires_at,
        "content_hash": record.content_hash,
    }


def hit_view(hit: SearchHit) -> dict[str, Any]:
    return {
        "book": book_view(hit.record),
        "relevance": hit.score,
        "vector_score": hit.vector_score,
        "lexical_score": hit.lexical_score,
        "importance_score": hit.importance_score,
        "recency_score": hit.recency_score,
    }


def desk_view(working: WorkingSet) -> dict[str, Any]:
    return {
        "session_id": working.session_id,
        "collection": working.namespace,
        "subject": working.focus,
        "context": working.context,
        "token_count": working.token_count,
        "token_budget": working.token_budget,
        "refreshed_at": working.refreshed_at,
        "swapped_in": working.swapped_in,
        "swapped_out": working.swapped_out,
        "retained": working.retained,
        "books": [hit_view(hit) for hit in working.hits],
    }


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


def event_view(event: ContextEvent) -> dict[str, Any]:
    return event.to_dict()


def governed_prompt_view(envelope: GovernedPrompt) -> dict[str, Any]:
    value = envelope.to_dict()
    value["desk"] = desk_view(envelope.desk)
    return value


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
