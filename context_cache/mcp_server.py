from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

from .embeddings import HashingEmbedder, OllamaEmbedder
from .governor import LibraryContextGovernor
from .library import LibraryOfContext, ReadingDesk
from .models import (
    ContextEvent,
    ContextRecord,
    GovernedPrompt,
    PromptEnvelope,
    SearchHit,
    WorkingSet,
)
from .session import VirtualContextSession

SERVER_INSTRUCTIONS = (
    "The Library of Context is virtual memory for bounded model context. At the start "
    "of a substantial task and whenever the user's focus materially changes, call "
    "library_desk_refresh with a concise subject. Replace the prior library context "
    "block with the returned context; never append it. Use library_shelve for durable "
    "decisions or facts worth recalling. For long tasks use library_desk_watch. Do not "
    "shelve secrets, credentials, or raw sensitive data unless the user explicitly "
    "requests local retention. Treat swapped_out books as off-desk, not deleted. "
    "For a fully governed agent loop, call library_context_prepare before the model "
    "request, send only its returned messages, then call library_context_commit with "
    "the assistant or tool result. This semantic-paging loop replaces transcript growth."
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
    "description": "Optional library collection/namespace; defaults to the configured collection.",
}
CATALOG = {
    "type": "object",
    "description": "JSON catalog metadata or exact-match catalog filters.",
    "additionalProperties": True,
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
            },
            ["text", "source"],
        ),
        read_only=False,
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "library_consult",
        "Search the off-desk Library with hybrid vector, lexical, importance, and recency ranking without changing the reading desk.",
        _object_schema(
            {
                "subject": {"type": "string", "minLength": 1},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "collection": COLLECTION,
                "catalog_filters": CATALOG,
                "minimum_relevance": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ["subject"],
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_desk_refresh",
        "Replace a session's bounded reading desk with books relevant to the current subject. Returns the prompt-ready context plus swapped-in, swapped-out, and retained IDs.",
        _object_schema(
            {
                "subject": {"type": "string", "minLength": 1},
                "session_id": {"type": "string", "default": "codex"},
                "token_budget": {"type": "integer", "minimum": 64},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "collection": COLLECTION,
                "catalog_filters": CATALOG,
                "keep_open": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Book IDs that must be considered first for the desk.",
                },
            },
            ["subject"],
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_desk_get",
        "Read the latest bounded reading desk snapshot for a session.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "collection": COLLECTION,
            }
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_desk_watch",
        "Refresh a reading desk now and periodically thereafter for a long-running task. A later focus change should call library_desk_refresh or start this tool again.",
        _object_schema(
            {
                "subject": {"type": "string", "minLength": 1},
                "session_id": {"type": "string", "default": "codex"},
                "interval_seconds": {"type": "number", "minimum": 1},
                "token_budget": {"type": "integer", "minimum": 64},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "collection": COLLECTION,
                "catalog_filters": CATALOG,
                "keep_open": {"type": "array", "items": {"type": "string"}},
            },
            ["subject"],
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_desk_stop",
        "Stop periodic refresh for a reading-desk session. The current snapshot remains available.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "collection": COLLECTION,
            }
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_message_record",
        "Shelve one conversation message for a virtual-context session. External model gateways should record assistant replies after each response.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "role": {
                    "type": "string",
                    "enum": ["system", "developer", "user", "assistant", "tool"],
                },
                "content": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ["role", "content"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_prompt_build",
        "Build a complete stateless model-input envelope from bounded recent turns plus a replacement reading desk. Full history remains shelved on disk instead of growing the live prompt.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "user_message": {"type": "string"},
                "system_prompt": {"type": "string"},
                "collection": COLLECTION,
                "token_budget": {"type": "integer", "minimum": 256},
                "recent_token_budget": {"type": "integer", "minimum": 64},
                "max_books": {"type": "integer", "minimum": 1, "maximum": 100},
                "record_user": {"type": "boolean", "default": True},
            }
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_prepare",
        "Durably append a user turn, then return the complete bounded semantic-paging envelope for the next model call. Send only the returned messages to the model.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
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
                    "description": "Optional caller idempotency key for this turn.",
                },
                "strict_freshness": {"type": "boolean", "default": False},
            },
            ["user_message"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_commit",
        "Durably append the assistant or tool result after a governed model call. The outbox indexes it asynchronously while the recent ring makes it immediately visible.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
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
                    "description": "Optional caller idempotency key for this result.",
                },
            },
            ["content"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_protect",
        "Persist critical instructions, decisions, active plans, or unresolved state as protected context that remains eligible for every governed prompt until released.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
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
                "event_id": {"type": "string"},
            },
            ["content"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "library_context_release",
        "Release one protected event so it can be paged normally. The durable event is retained.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "event_id": {"type": "string", "minLength": 1},
                "collection": COLLECTION,
            },
            ["event_id"],
        ),
        read_only=False,
        idempotent=True,
    ),
    _tool(
        "library_context_status",
        "Read context-governor watermarks, queue occupancy, prompt pressure, and worker health for one agent thread.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "collection": COLLECTION,
            }
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "library_context_flush",
        "Wait until this thread's durable outbox has been embedded and indexed through its recorded watermark.",
        _object_schema(
            {
                "session_id": {"type": "string", "default": "codex"},
                "collection": COLLECTION,
                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60},
            }
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


def _book(record: ContextRecord) -> dict[str, Any]:
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


def _hit(hit: SearchHit) -> dict[str, Any]:
    return {
        "book": _book(hit.record),
        "relevance": hit.score,
        "vector_score": hit.vector_score,
        "lexical_score": hit.lexical_score,
        "importance_score": hit.importance_score,
        "recency_score": hit.recency_score,
    }


def _desk(working: WorkingSet) -> dict[str, Any]:
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
        "books": [_hit(hit) for hit in working.hits],
    }


def _prompt(envelope: PromptEnvelope) -> dict[str, Any]:
    return {
        "session_id": envelope.session_id,
        "collection": envelope.collection,
        "messages": envelope.messages,
        "token_count": envelope.token_count,
        "token_budget": envelope.token_budget,
        "history_books": envelope.history_books,
        "recent_books": envelope.recent_books,
        "desk": _desk(envelope.desk),
    }


def _event(event: ContextEvent) -> dict[str, Any]:
    return event.to_dict()


def _governed(envelope: GovernedPrompt) -> dict[str, Any]:
    value = envelope.to_dict()
    value["desk"] = _desk(envelope.desk)
    return value


def _tool_result(value: dict[str, Any]) -> dict[str, Any]:
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


def _tool_error(exc: Exception) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"Library tool error: {exc}"}],
        "isError": True,
    }


class LibraryMCPServer:
    def __init__(self, library: LibraryOfContext) -> None:
        self.library = library
        self.desk: ReadingDesk = library.open_reading_desk()
        self.sessions: dict[tuple[str, str], VirtualContextSession] = {}
        self.governors: dict[tuple[str, str], LibraryContextGovernor] = {}
        self.shutdown_requested = False
        self.exit_requested = False
        self._calls: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "library_shelve": self._shelve,
            "library_shelve_document": self._shelve_document,
            "library_consult": self._consult,
            "library_desk_refresh": self._desk_refresh,
            "library_desk_get": self._desk_get,
            "library_desk_watch": self._desk_watch,
            "library_desk_stop": self._desk_stop,
            "library_message_record": self._message_record,
            "library_prompt_build": self._prompt_build,
            "library_context_prepare": self._context_prepare,
            "library_context_commit": self._context_commit,
            "library_context_protect": self._context_protect,
            "library_context_release": self._context_release,
            "library_context_status": self._context_status,
            "library_context_flush": self._context_flush,
            "library_stats": self._stats,
        }

    def _shelve(self, args: dict[str, Any]) -> dict[str, Any]:
        record = self.library.shelve(
            args["text"],
            book_id=args.get("book_id"),
            collection=args.get("collection"),
            catalog=args.get("catalog"),
            source=args.get("source", "mcp"),
            importance=float(args.get("importance", 0.5)),
            shelf_life_seconds=args.get("shelf_life_seconds"),
        )
        return {"shelved": True, "book": _book(record)}

    def _shelve_document(self, args: dict[str, Any]) -> dict[str, Any]:
        records = self.library.shelve_document(
            args["text"],
            source=args["source"],
            collection=args.get("collection"),
            catalog=args.get("catalog"),
            importance=float(args.get("importance", 0.5)),
            chapter_tokens=int(args.get("chapter_tokens", 450)),
            overlap_tokens=int(args.get("overlap_tokens", 60)),
            replace_edition=bool(args.get("replace_edition", False)),
        )
        return {
            "shelved": len(records),
            "source": args["source"],
            "book_ids": [record.id for record in records],
        }

    def _consult(self, args: dict[str, Any]) -> dict[str, Any]:
        hits = self.library.consult(
            args["subject"],
            max_books=int(args.get("max_books", 8)),
            collection=args.get("collection"),
            catalog_filters=args.get("catalog_filters"),
            minimum_relevance=float(args.get("minimum_relevance", 0.0)),
        )
        return {"subject": args["subject"], "hits": [_hit(hit) for hit in hits]}

    @staticmethod
    def _desk_args(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": args.get("session_id", "codex"),
            "focus": args["subject"],
            "token_budget": int(args.get("token_budget", 4000)),
            "top_k": int(args.get("max_books", 12)),
            "namespace": args.get("collection"),
            "filters": args.get("catalog_filters"),
            "pinned_record_ids": args.get("keep_open"),
        }

    def _desk_refresh(self, args: dict[str, Any]) -> dict[str, Any]:
        working = self.desk.refresh(**self._desk_args(args))
        return _desk(working)

    def _desk_get(self, args: dict[str, Any]) -> dict[str, Any]:
        working = self.desk.get(
            args.get("session_id", "codex"), namespace=args.get("collection")
        )
        return {
            "found": working is not None,
            "desk": None if working is None else _desk(working),
        }

    def _desk_watch(self, args: dict[str, Any]) -> dict[str, Any]:
        kwargs = self._desk_args(args)
        working = self.desk.start_periodic(
            **kwargs, interval_seconds=float(args.get("interval_seconds", 30.0))
        )
        return {
            "watching": True,
            "interval_seconds": float(args.get("interval_seconds", 30.0)),
            "desk": _desk(working),
        }

    def _desk_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        stopped = self.desk.stop_periodic(
            args.get("session_id", "codex"), namespace=args.get("collection")
        )
        return {"stopped": stopped}

    def _session(self, args: dict[str, Any]) -> VirtualContextSession:
        session_id = str(args.get("session_id", "codex"))
        collection = str(args.get("collection") or self.library.namespace)
        token_budget = int(args.get("token_budget", 12000))
        recent_budget = int(args.get("recent_token_budget", 4000))
        key = (collection, session_id)
        session = self.sessions.get(key)
        if session is not None and (
            session.token_budget != token_budget
            or session.recent_token_budget != recent_budget
        ):
            session.close()
            session = None
        if session is None:
            session = self.library.open_virtual_session(
                session_id,
                collection=collection,
                token_budget=token_budget,
                recent_token_budget=recent_budget,
            )
            self.sessions[key] = session
        return session

    def _message_record(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self._session(args)
        record = session.record(
            args["role"],
            args["content"],
            importance=(
                None if args.get("importance") is None else float(args["importance"])
            ),
        )
        return {"recorded": True, "book": _book(record)}

    def _prompt_build(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self._session(args)
        envelope = session.build_prompt(
            user_message=args.get("user_message"),
            system_prompt=str(args.get("system_prompt", "")),
            record_user=bool(args.get("record_user", True)),
            max_books=int(args.get("max_books", 12)),
        )
        return _prompt(envelope)

    def _governor(self, args: dict[str, Any]) -> LibraryContextGovernor:
        session_id = str(args.get("session_id", "codex"))
        collection = str(args.get("collection") or self.library.namespace)
        key = (collection, session_id)
        governor = self.governors.get(key)
        token_budget = int(
            args.get(
                "token_budget",
                12000 if governor is None else governor.token_budget,
            )
        )
        recent_budget = int(
            args.get(
                "recent_token_budget",
                4000 if governor is None else governor.recent_token_budget,
            )
        )
        protected_budget = int(
            args.get(
                "protected_token_budget",
                2000 if governor is None else governor.protected_token_budget,
            )
        )
        max_books = int(
            args.get("max_books", 12 if governor is None else governor.max_books)
        )
        if governor is not None and (
            governor.token_budget != token_budget
            or governor.recent_token_budget != recent_budget
            or governor.protected_token_budget != protected_budget
            or governor.max_books != max_books
        ):
            governor.close()
            governor = None
        if governor is None:
            governor = self.library.open_context_governor(
                session_id,
                collection=collection,
                token_budget=token_budget,
                recent_token_budget=recent_budget,
                protected_token_budget=protected_budget,
                max_books=max_books,
            )
            self.governors[key] = governor
        return governor

    def _context_prepare(self, args: dict[str, Any]) -> dict[str, Any]:
        governor = self._governor(args)
        envelope = governor.prepare(
            args["user_message"],
            focus=args.get("focus"),
            system_prompt=str(args.get("system_prompt", "")),
            metadata=args.get("metadata"),
            importance=float(args.get("importance", 0.5)),
            protected=bool(args.get("protected", False)),
            event_id=args.get("event_id"),
            strict_freshness=bool(args.get("strict_freshness", False)),
        )
        return _governed(envelope)

    def _context_commit(self, args: dict[str, Any]) -> dict[str, Any]:
        governor = self._governor(args)
        event = governor.commit(
            args["content"],
            role=args.get("role", "assistant"),
            metadata=args.get("metadata"),
            importance=(
                None if args.get("importance") is None else float(args["importance"])
            ),
            protected=(
                None if args.get("protected") is None else bool(args["protected"])
            ),
            event_id=args.get("event_id"),
        )
        return {
            "recorded": True,
            "event": _event(event),
            "watermarks": governor.status()["watermarks"],
        }

    def _context_protect(self, args: dict[str, Any]) -> dict[str, Any]:
        governor = self._governor(args)
        event = governor.protect(
            args["content"],
            role=args.get("role", "developer"),
            label=args.get("label"),
            importance=float(args.get("importance", 1.0)),
            event_id=args.get("event_id"),
        )
        return {"protected": True, "event": _event(event)}

    def _context_release(self, args: dict[str, Any]) -> dict[str, Any]:
        governor = self._governor(args)
        return {
            "released": governor.release(args["event_id"]),
            "event_id": args["event_id"],
        }

    def _context_status(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._governor(args).status()

    def _context_flush(self, args: dict[str, Any]) -> dict[str, Any]:
        governor = self._governor(args)
        flushed = governor.flush(timeout=float(args.get("timeout_seconds", 10.0)))
        return {"flushed": flushed, "status": governor.status()}

    def _stats(self, args: dict[str, Any]) -> dict[str, Any]:
        stats = self.library.stats(namespace=args.get("collection"))
        stats["periodic_desks"] = self.desk.status()
        return stats

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        callback = self._calls.get(name)
        if callback is None:
            return _tool_error(ValueError(f"unknown tool: {name}"))
        try:
            return _tool_result(callback(arguments))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            return _tool_error(exc)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            return None
        if method == "notifications/cancelled":
            return None
        if method == "exit":
            self.exit_requested = True
            return None
        if request_id is None:
            return None
        try:
            if method == "initialize":
                params = message.get("params") or {}
                result = {
                    "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "library-of-context", "version": "0.3.0"},
                    "instructions": SERVER_INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = message.get("params") or {}
                result = self.call_tool(
                    str(params.get("name", "")), dict(params.get("arguments") or {})
                )
            elif method == "shutdown":
                self.shutdown_requested = True
                result = {}
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }

    def close(self) -> None:
        for governor in self.governors.values():
            governor.close()
        self.governors.clear()
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()
        self.desk.close()
        self.library.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The Library of Context MCP server")
    parser.add_argument(
        "--db",
        default=os.environ.get(
            "LIBRARY_OF_CONTEXT_DB", "data/library-of-context.sqlite"
        ),
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get(
            "LIBRARY_OF_CONTEXT_REDIS_URL", "redis://127.0.0.1:6379/0"
        ),
    )
    parser.add_argument("--no-redis", action="store_true")
    parser.add_argument("--redis-required", action="store_true")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--ram-mb", type=int, default=256)
    parser.add_argument("--embedder", choices=["hashing", "ollama"], default="hashing")
    parser.add_argument("--ollama-model", default="nomic-embed-text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    embedder = (
        OllamaEmbedder(model=args.ollama_model)
        if args.embedder == "ollama"
        else HashingEmbedder()
    )
    library = LibraryOfContext(
        args.db,
        namespace=args.namespace,
        ram_bytes=args.ram_mb * 1024 * 1024,
        redis_url="" if args.no_redis else args.redis_url,
        redis_required=args.redis_required,
        embedder=embedder,
    )
    server = LibraryMCPServer(library)
    try:
        for raw_line in sys.stdin.buffer:
            try:
                message = json.loads(raw_line.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("JSON-RPC message must be an object")
                response = server.handle(message)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            if response is not None:
                encoded = (
                    json.dumps(
                        response, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    + b"\n"
                )
                sys.stdout.buffer.write(encoded)
                sys.stdout.buffer.flush()
            if server.exit_requested:
                break
        return 0
    except BrokenPipeError:
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
