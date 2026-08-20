from __future__ import annotations

from typing import Any, Callable

from . import mcp_views
from .governor import LibraryContextGovernor
from .library import LibraryOfContext, ReadingDesk
from .resource_registry import GovernorRegistry, VirtualSessionRegistry
from .session import VirtualContextSession


class LibraryMCPTools:
    """Execute Library MCP tools independently of the JSON-RPC transport."""

    def __init__(self, library: LibraryOfContext) -> None:
        self.library = library
        self.desk: ReadingDesk = library.open_reading_desk()
        self.session_registry = VirtualSessionRegistry(
            library,
            default_session_id="codex",
        )
        self.governor_registry = GovernorRegistry(
            library,
            default_session_id="codex",
        )
        self.sessions = self.session_registry.sessions
        self.governors = self.governor_registry.governors
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
        return {"shelved": True, "book": mcp_views.book_view(record)}

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
        return {
            "subject": args["subject"],
            "hits": [mcp_views.hit_view(hit) for hit in hits],
        }

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
        return mcp_views.desk_view(self.desk.refresh(**self._desk_args(args)))

    def _desk_get(self, args: dict[str, Any]) -> dict[str, Any]:
        working = self.desk.get(
            args.get("session_id", "codex"),
            namespace=args.get("collection"),
        )
        return {
            "found": working is not None,
            "desk": None if working is None else mcp_views.desk_view(working),
        }

    def _desk_watch(self, args: dict[str, Any]) -> dict[str, Any]:
        working = self.desk.start_periodic(
            **self._desk_args(args),
            interval_seconds=float(args.get("interval_seconds", 30.0)),
        )
        return {
            "watching": True,
            "interval_seconds": float(args.get("interval_seconds", 30.0)),
            "desk": mcp_views.desk_view(working),
        }

    def _desk_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        stopped = self.desk.stop_periodic(
            args.get("session_id", "codex"),
            namespace=args.get("collection"),
        )
        return {"stopped": stopped}

    def _session(self, args: dict[str, Any]) -> VirtualContextSession:
        return self.session_registry.get(args)

    def _message_record(self, args: dict[str, Any]) -> dict[str, Any]:
        record = self._session(args).record(
            args["role"],
            args["content"],
            importance=(
                None if args.get("importance") is None else float(args["importance"])
            ),
        )
        return {"recorded": True, "book": mcp_views.book_view(record)}

    def _prompt_build(self, args: dict[str, Any]) -> dict[str, Any]:
        envelope = self._session(args).build_prompt(
            user_message=args.get("user_message"),
            system_prompt=str(args.get("system_prompt", "")),
            record_user=bool(args.get("record_user", True)),
            max_books=int(args.get("max_books", 12)),
        )
        return mcp_views.prompt_view(envelope)

    def _governor(self, args: dict[str, Any]) -> LibraryContextGovernor:
        return self.governor_registry.get(args)

    def _context_prepare(self, args: dict[str, Any]) -> dict[str, Any]:
        envelope = self._governor(args).prepare(
            args["user_message"],
            focus=args.get("focus"),
            system_prompt=str(args.get("system_prompt", "")),
            metadata=args.get("metadata"),
            importance=float(args.get("importance", 0.5)),
            protected=bool(args.get("protected", False)),
            event_id=args.get("event_id"),
            strict_freshness=bool(args.get("strict_freshness", False)),
        )
        return mcp_views.governed_prompt_view(envelope)

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
            "event": mcp_views.event_view(event),
            "watermarks": governor.status()["watermarks"],
        }

    def _context_protect(self, args: dict[str, Any]) -> dict[str, Any]:
        event = self._governor(args).protect(
            args["content"],
            role=args.get("role", "developer"),
            label=args.get("label"),
            importance=float(args.get("importance", 1.0)),
            event_id=args.get("event_id"),
        )
        return {"protected": True, "event": mcp_views.event_view(event)}

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

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        callback = self._calls.get(name)
        if callback is None:
            return mcp_views.tool_error(ValueError(f"unknown tool: {name}"))
        try:
            return mcp_views.tool_result(callback(arguments))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            return mcp_views.tool_error(exc)

    def close(self) -> None:
        self.governor_registry.close()
        self.session_registry.close()
        self.desk.close()
        self.library.close()
