from __future__ import annotations

from typing import Any, Callable

from . import mcp_views
from .client import LibraryDaemonClient
from .governor import LibraryContextGovernor
from .library import LibraryOfContext, ReadingDesk
from .limits import MAX_RESULT_BOOKS, bounded_integer, bounded_string_tuple
from .resource_registry import GovernorRegistry, VirtualSessionRegistry
from .scopes import ThreadKey
from .session import VirtualContextSession


class LibraryMCPTools:
    """Execute Library MCP tools independently of the JSON-RPC transport."""

    def __init__(
        self,
        library: LibraryOfContext,
        *,
        close_library: bool = True,
    ) -> None:
        self.library = library
        self._close_library = close_library
        self.desk: ReadingDesk = library.open_reading_desk()
        runtime_settings = library.runtime.settings
        self.session_registry = VirtualSessionRegistry(
            library,
            max_entries=runtime_settings.max_active_threads,
            idle_ttl_seconds=runtime_settings.thread_idle_ttl_seconds,
        )
        self.governor_registry = GovernorRegistry(
            library,
            max_entries=runtime_settings.max_active_threads,
            idle_ttl_seconds=runtime_settings.thread_idle_ttl_seconds,
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
            scope=args.get("scope", "project"),
            owner_session_id=args.get("owner_session_id"),
            team_id=args.get("team_id"),
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
            scope=args.get("scope", "project"),
            owner_session_id=args.get("owner_session_id"),
            team_id=args.get("team_id"),
        )
        return {
            "shelved": len(records),
            "source": args["source"],
            "book_ids": [record.id for record in records],
        }

    def _consult(self, args: dict[str, Any]) -> dict[str, Any]:
        hits = self.library.consult(
            args["subject"],
            max_books=bounded_integer(
                args.get("max_books", 8),
                name="max_books",
                minimum=1,
                maximum=MAX_RESULT_BOOKS,
            ),
            collection=args.get("collection"),
            catalog_filters=args.get("catalog_filters"),
            minimum_relevance=float(args.get("minimum_relevance", 0.0)),
            team_ids=bounded_string_tuple(
                args.get("team_ids"),
                name="team_ids",
                maximum_items=MAX_RESULT_BOOKS,
            ),
        )
        return mcp_views.search_view(args["subject"], hits)

    @staticmethod
    def _desk_args(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": args["session_id"],
            "focus": args["subject"],
            "token_budget": int(args.get("token_budget", 4000)),
            "top_k": int(args.get("max_books", 12)),
            "namespace": args.get("collection"),
            "filters": args.get("catalog_filters"),
            "pinned_record_ids": list(
                bounded_string_tuple(
                    args.get("keep_open"),
                    name="keep_open",
                    maximum_items=MAX_RESULT_BOOKS,
                )
            ),
            "team_ids": bounded_string_tuple(
                args.get("team_ids"),
                name="team_ids",
                maximum_items=MAX_RESULT_BOOKS,
            ),
        }

    def _desk_refresh(self, args: dict[str, Any]) -> dict[str, Any]:
        return mcp_views.desk_view(self.desk.refresh(**self._desk_args(args)))

    def _desk_get(self, args: dict[str, Any]) -> dict[str, Any]:
        working = self.desk.get(
            args["session_id"],
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
            args["session_id"],
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
        collection = args.get("collection") or self.library.namespace
        if not isinstance(collection, str):
            raise TypeError("collection must be a string")
        ThreadKey(collection, "statistics")
        stats = self.library.stats(namespace=collection)
        stats["periodic_desks"] = self.desk.status(namespace=collection)
        stats["governor_registry"] = self.governor_registry.stats(collection=collection)
        stats["virtual_session_registry"] = self.session_registry.stats(
            collection=collection
        )
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
        if self._close_library:
            self.library.close()


class DaemonMCPTools:
    """Forward MCP tool calls to the runtime owned by a loopback daemon."""

    def __init__(
        self,
        client: LibraryDaemonClient,
        *,
        default_collection: str | None = None,
    ) -> None:
        self.client = client
        self.default_collection = (
            None
            if default_collection is None
            else ThreadKey(default_collection, "daemon-bridge").collection
        )
        self.health = client.health()

    @property
    def runtime_id(self) -> str:
        runtime = self.health["runtime"]
        assert isinstance(runtime, dict)
        return str(runtime["id"])

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        routed_arguments = dict(arguments)
        if self.default_collection is not None:
            collection = routed_arguments.get("collection")
            if collection is None or (
                isinstance(collection, str) and not collection.strip()
            ):
                routed_arguments["collection"] = self.default_collection
            elif not isinstance(collection, str):
                raise ValueError("collection must be a string")
            else:
                ThreadKey(collection, "daemon-bridge")
        return self.client.call_mcp_tool(name, routed_arguments)

    def close(self) -> None:
        pass
