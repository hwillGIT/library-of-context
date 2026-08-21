from __future__ import annotations

from typing import Any

SERVER_INSTRUCTIONS = (
    "The Library of Context is virtual memory for bounded model context. For cooperative "
    "MCP use, call library_desk_refresh at task start or focus change and replace, never "
    "append, the prior desk. Shelve durable decisions; never shelve secrets. A gateway "
    "that owns each model call must call library_context_prepare, send only its returned "
    "messages, then call library_context_commit. Calling governor tools during a normal "
    "MCP turn does not replace the host transcript or compaction behavior. Treat "
    "swapped_out books as stored off-desk, "
    "not deleted. Give each project a separate collection and each thread a stable, "
    "unique session_id. For long tasks, library_desk_watch refreshes stored desk state; "
    "call library_desk_get to read it."
)


def _object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


COLLECTION = {
    "type": "string",
    "maxLength": 512,
    "description": "Optional library collection/namespace; defaults to the configured collection.",
}
CATALOG = {
    "type": "object",
    "description": "JSON catalog metadata or exact-match catalog filters.",
    "additionalProperties": True,
}
SCOPE = {
    "type": "string",
    "enum": ["thread", "project", "team"],
    "description": "Visibility boundary for a shelved book.",
}
TEAM_IDS = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "maxItems": 100,
    "description": "Team identifiers authorized by the calling gateway.",
}


def _tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": False,
        },
    }


TOOLS = [
    _tool(
        "library_shelve",
        "Persist one concise book of context to the local Library. Use for durable decisions, constraints, findings, and user-approved memories—not transient chatter or secrets.",
        _object_schema(
            {
                "text": {"type": "string", "minLength": 1},
                "book_id": {"type": "string"},
                "collection": COLLECTION,
                "catalog": CATALOG,
                "source": {"type": "string", "default": "mcp"},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "shelf_life_seconds": {"type": "number", "minimum": 0},
                "scope": SCOPE,
                "owner_session_id": {"type": "string", "minLength": 1},
                "team_id": {"type": "string", "minLength": 1},
            },
            ["text"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_shelve_document",
        "Chunk and persist a larger local text document as multiple books. Set replace_edition to replace prior chunks from the same source.",
        _object_schema(
            {
                "text": {"type": "string", "minLength": 1},
                "source": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
                "catalog": CATALOG,
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "chapter_tokens": {"type": "integer", "minimum": 32},
                "overlap_tokens": {"type": "integer", "minimum": 0},
                "replace_edition": {"type": "boolean", "default": False},
                "scope": SCOPE,
                "owner_session_id": {"type": "string", "minLength": 1},
                "team_id": {"type": "string", "minLength": 1},
            },
            ["text", "source"],
        ),
        read_only=False,
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "library_consult",
        "Search the off-desk Library without changing the reading desk. Returns bounded untrusted excerpts, lightweight record references, and ranking scores.",
        _object_schema(
            {
                "subject": {"type": "string", "minLength": 1},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "collection": COLLECTION,
                "catalog_filters": CATALOG,
                "minimum_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "team_ids": TEAM_IDS,
            },
            ["subject"],
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_desk_refresh",
        "Replace a session's bounded reading desk with books relevant to the current subject. Returns one trust-marked context block plus bounded book references and swap IDs.",
        _object_schema(
            {
                "subject": {"type": "string", "minLength": 1},
                "session_id": {"type": "string", "minLength": 1},
                "token_budget": {"type": "integer", "minimum": 64},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "collection": COLLECTION,
                "catalog_filters": CATALOG,
                "keep_open": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 100,
                    "description": "Book IDs that must be considered first for the desk.",
                },
                "team_ids": TEAM_IDS,
            },
            ["subject", "session_id"],
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_desk_get",
        "Read the latest bounded reading desk snapshot for a session.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
            },
            ["session_id"],
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_desk_watch",
        "Refresh a reading desk immediately and periodically thereafter for a long-running task. A later focus change should call library_desk_refresh or start this tool again.",
        _object_schema(
            {
                "subject": {"type": "string", "minLength": 1},
                "session_id": {"type": "string", "minLength": 1},
                "interval_seconds": {"type": "number", "minimum": 1},
                "token_budget": {"type": "integer", "minimum": 64},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "collection": COLLECTION,
                "catalog_filters": CATALOG,
                "keep_open": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 100,
                },
                "team_ids": TEAM_IDS,
            },
            ["subject", "session_id"],
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_desk_stop",
        "Stop periodic refresh for a reading-desk session. The current snapshot remains available.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
            },
            ["session_id"],
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_message_record",
        "Shelve one conversation message for a virtual-context session. External model gateways should record assistant replies after each response.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "role": {
                    "type": "string",
                    "enum": ["system", "developer", "user", "assistant", "tool"],
                },
                "content": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ["session_id", "role", "content"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_prompt_build",
        "Build a complete stateless model-input envelope from bounded recent turns plus a replacement reading desk. Full history remains shelved on disk instead of growing the live prompt.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "user_message": {"type": "string"},
                "system_prompt": {"type": "string"},
                "collection": COLLECTION,
                "token_budget": {"type": "integer", "minimum": 256},
                "recent_token_budget": {"type": "integer", "minimum": 64},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "record_user": {"type": "boolean", "default": True},
            },
            ["session_id"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_prepare",
        "Durably append a user turn, then return the complete bounded semantic-paging envelope for the next model call. Send only the returned messages to the model.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "user_message": {"type": "string", "minLength": 1},
                "focus": {"type": "string"},
                "system_prompt": {"type": "string"},
                "collection": COLLECTION,
                "token_budget": {"type": "integer", "minimum": 256},
                "recent_token_budget": {"type": "integer", "minimum": 64},
                "protected_token_budget": {"type": "integer", "minimum": 0},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "metadata": CATALOG,
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "protected": {"type": "boolean", "default": False},
                "event_id": {
                    "type": "string",
                    "maxLength": 512,
                    "description": "Optional caller idempotency key for this turn.",
                },
                "strict_freshness": {"type": "boolean", "default": False},
            },
            ["session_id", "user_message"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_commit",
        "Durably append the assistant or tool result after a governed model call. The outbox indexes it asynchronously while the recent ring makes it immediately visible.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "role": {
                    "type": "string",
                    "enum": ["assistant", "tool", "user", "developer", "system"],
                    "default": "assistant",
                },
                "content": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
                "token_budget": {"type": "integer", "minimum": 256},
                "recent_token_budget": {"type": "integer", "minimum": 64},
                "protected_token_budget": {"type": "integer", "minimum": 0},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "metadata": CATALOG,
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "protected": {"type": "boolean"},
                "event_id": {
                    "type": "string",
                    "maxLength": 512,
                    "description": "Optional caller idempotency key for this result.",
                },
            },
            ["session_id", "content"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_protect",
        "Persist critical instructions, decisions, active plans, or unresolved state as protected context that remains eligible for every governed prompt until released.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "content": {"type": "string", "minLength": 1},
                "role": {
                    "type": "string",
                    "enum": ["developer", "system", "user", "assistant", "tool"],
                    "default": "developer",
                },
                "label": {"type": "string"},
                "collection": COLLECTION,
                "token_budget": {"type": "integer", "minimum": 256},
                "recent_token_budget": {"type": "integer", "minimum": 64},
                "protected_token_budget": {"type": "integer", "minimum": 0},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "event_id": {"type": "string", "maxLength": 512},
            },
            ["session_id", "content"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_release",
        "Release one protected event so it can be paged normally. The durable event is retained.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "event_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                },
                "collection": COLLECTION,
            },
            ["session_id", "event_id"],
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_context_status",
        "Read context-governor watermarks, queue occupancy, prompt pressure, and worker health for one agent thread.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
            },
            ["session_id"],
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_context_flush",
        "Wait until this thread's durable outbox has been embedded and indexed through its recorded watermark.",
        _object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60},
            },
            ["session_id"],
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_stats",
        "Inspect Library tier health, usage, cache hit/miss counters, and embedding configuration.",
        _object_schema({"collection": COLLECTION}),
        read_only=True,
        idempotent=True,
    ),
]
