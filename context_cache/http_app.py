from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from .client import (
    DAEMON_PROTOCOL_NAME,
    DAEMON_PROTOCOL_VERSION,
    MCP_SCHEMA_VERSION,
)
from .engine import ContextCache
from .library import LibraryOfContext
from .limits import (
    MAX_CONTEXT_TOKENS,
    MAX_RESULT_BOOKS,
    bounded_integer,
    bounded_string_tuple,
)
from .mcp_service import LibraryMCPTools
from .resource_registry import GovernorRegistry
from .scopes import ThreadKey
from .store import SCHEMA_VERSION
from .swapper import ContextSwapper
from .transport_views import desk_view, event_view, governed_prompt_view, search_view


@dataclass(frozen=True, slots=True)
class JSONResponse:
    """Transport-neutral JSON response produced by the HTTP application."""

    status: int
    body: Any


PostHandler = Callable[[dict[str, Any]], JSONResponse]


class LibraryHTTPApplication:
    """Route Library HTTP requests without depending on ``BaseHTTPRequestHandler``."""

    def __init__(self, cache: ContextCache, swapper: ContextSwapper) -> None:
        self.cache = cache
        self.swapper = swapper
        self.governors = GovernorRegistry(
            cache,
            max_entries=cache.runtime.settings.max_active_threads,
            idle_ttl_seconds=cache.runtime.settings.thread_idle_ttl_seconds,
        )
        self.runtime_id = uuid4().hex
        self.mcp_tools = (
            LibraryMCPTools(cache, close_library=False)
            if isinstance(cache, LibraryOfContext)
            else None
        )
        self._post_routes: dict[str, PostHandler] = {
            "/governor/prepare": self._prepare,
            "/context/prepare": self._prepare,
            "/governor/commit": self._commit,
            "/context/commit": self._commit,
            "/governor/protect": self._protect,
            "/context/protect": self._protect,
            "/governor/release": self._release,
            "/context/release": self._release,
            "/governor/flush": self._flush,
            "/context/flush": self._flush,
            "/books": self._put,
            "/records": self._put,
            "/library/ingest": self._ingest,
            "/ingest": self._ingest,
            "/catalog/query": self._query,
            "/query": self._query,
            "/desk/refresh": self._refresh_desk,
            "/context/refresh": self._refresh_desk,
            "/desk/watch": self._watch_desk,
            "/context/watch": self._watch_desk,
            "/mcp/call": self._mcp_call,
            "/mcp/tools/call": self._mcp_call,
        }

    def close(self) -> None:
        self.governors.close()
        if self.mcp_tools is not None:
            self.mcp_tools.close()

    def dispatch(
        self,
        method: str,
        target: str,
        body: dict[str, Any] | None = None,
    ) -> JSONResponse:
        parsed = urlparse(target)
        path = parsed.path.rstrip("/") or "/"
        if method == "GET":
            return self._get(path, parsed.query)
        if method == "POST":
            return self._post(path, {} if body is None else body)
        if method == "DELETE":
            return self._delete(path, parsed.query)
        return self._not_found()

    @staticmethod
    def _not_found() -> JSONResponse:
        return JSONResponse(404, {"error": "not found"})

    def _collection_from_query(
        self,
        query_string: str,
        *,
        accept_namespace_alias: bool = False,
    ) -> str:
        query = parse_qs(query_string, keep_blank_values=True)
        collection_values = query.get("collection")
        namespace_values = query.get("namespace") if accept_namespace_alias else None
        if (
            collection_values
            and namespace_values
            and collection_values[0] != namespace_values[0]
        ):
            raise ValueError("collection and namespace must identify the same catalog")
        values = collection_values or namespace_values
        collection = self.cache.namespace if not values else values[0]
        ThreadKey(collection, "http")
        return collection

    @staticmethod
    def _visibility_from_query(
        query_string: str,
    ) -> tuple[tuple[str, ...] | None, str | None, tuple[str, ...]]:
        query = parse_qs(query_string, keep_blank_values=True)
        raw_scopes = query.get("scope")
        scopes = None if raw_scopes is None else tuple(raw_scopes)
        session_values = query.get("session_id")
        session_id = None if not session_values else session_values[0]
        team_ids = tuple(query.get("team_id", ()))
        return scopes, session_id, team_ids

    def _get(self, path: str, query_string: str) -> JSONResponse:
        if path == "/health":
            return self._health()
        if path == "/stats":
            collection = self._collection_from_query(
                query_string,
                accept_namespace_alias=True,
            )
            return JSONResponse(200, self.cache.stats(namespace=collection))
        if path.startswith(("/governor/status/", "/context/status/")):
            return self._governor_status(path, query_string)
        if path.startswith(("/desk/", "/context/")):
            return self._desk_status(path, query_string)
        return self._not_found()

    def _health(self) -> JSONResponse:
        redis = None if self.cache.redis is None else self.cache.redis.stats()
        return JSONResponse(
            200,
            {
                "ok": True,
                "service": "The Library of Context",
                "sqlite": True,
                "redis": None if redis is None else redis["enabled"],
                "protocol": {
                    "name": DAEMON_PROTOCOL_NAME,
                    "version": DAEMON_PROTOCOL_VERSION,
                },
                "schema": {
                    "sqlite": SCHEMA_VERSION,
                    "mcp_tools": MCP_SCHEMA_VERSION,
                },
                "runtime": {"id": self.runtime_id, **self.cache.runtime.status()},
            },
        )

    def _mcp_call(self, body: dict[str, Any]) -> JSONResponse:
        received_protocol = body.get("protocol_version")
        if received_protocol != DAEMON_PROTOCOL_VERSION:
            return JSONResponse(
                409,
                {
                    "error": {
                        "code": "protocol_mismatch",
                        "message": (
                            "daemon protocol mismatch: expected "
                            f"{DAEMON_PROTOCOL_VERSION}, received {received_protocol}"
                        ),
                    }
                },
            )
        received_schema = body.get("schema_version")
        if received_schema != MCP_SCHEMA_VERSION:
            return JSONResponse(
                409,
                {
                    "error": {
                        "code": "schema_mismatch",
                        "message": (
                            "daemon MCP schema mismatch: expected "
                            f"{MCP_SCHEMA_VERSION}, received {received_schema}"
                        ),
                    }
                },
            )
        if self.mcp_tools is None:
            return JSONResponse(
                503,
                {
                    "error": {
                        "code": "mcp_service_unavailable",
                        "message": "daemon runtime does not expose Library MCP tools",
                    }
                },
            )
        arguments = body.get("arguments", {})
        if not isinstance(arguments, dict):
            raise TypeError("MCP tool arguments must be a JSON object")
        return JSONResponse(
            200,
            self.mcp_tools.call(str(body.get("name", "")), arguments),
        )

    def _governor_status(self, path: str, query_string: str) -> JSONResponse:
        prefix = (
            "/governor/status/"
            if path.startswith("/governor/status/")
            else "/context/status/"
        )
        session_id = unquote(path.removeprefix(prefix))
        collection = self._collection_from_query(query_string)
        governor = self.governors.get(
            {"session_id": session_id, "collection": collection}
        )
        return JSONResponse(200, governor.status())

    def _desk_status(self, path: str, query_string: str) -> JSONResponse:
        prefix = "/desk/" if path.startswith("/desk/") else "/context/"
        session_id = unquote(path.removeprefix(prefix))
        collection = self._collection_from_query(query_string)
        working = self.swapper.get(session_id, namespace=collection)
        return JSONResponse(
            404 if working is None else 200,
            None if working is None else desk_view(working),
        )

    def _post(self, path: str, body: dict[str, Any]) -> JSONResponse:
        handler = self._post_routes.get(path)
        if handler is None:
            return self._not_found()
        return handler(body)

    def _prepare(self, body: dict[str, Any]) -> JSONResponse:
        governor = self.governors.get(body)
        envelope = governor.prepare(
            body["user_message"],
            focus=body.get("focus"),
            system_prompt=str(body.get("system_prompt", "")),
            metadata=body.get("metadata"),
            importance=float(body.get("importance", 0.5)),
            protected=bool(body.get("protected", False)),
            event_id=body.get("event_id"),
            strict_freshness=bool(body.get("strict_freshness", False)),
        )
        return JSONResponse(200, governed_prompt_view(envelope))

    def _commit(self, body: dict[str, Any]) -> JSONResponse:
        governor = self.governors.get(body)
        event = governor.commit(
            body["content"],
            role=body.get("role", "assistant"),
            metadata=body.get("metadata"),
            importance=(
                None if body.get("importance") is None else float(body["importance"])
            ),
            protected=(
                None if body.get("protected") is None else bool(body["protected"])
            ),
            event_id=body.get("event_id"),
        )
        return JSONResponse(
            201,
            {
                "recorded": True,
                "event": event_view(event),
                "watermarks": governor.status()["watermarks"],
            },
        )

    def _protect(self, body: dict[str, Any]) -> JSONResponse:
        governor = self.governors.get(body)
        event = governor.protect(
            body["content"],
            role=body.get("role", "developer"),
            label=body.get("label"),
            importance=float(body.get("importance", 1.0)),
            event_id=body.get("event_id"),
        )
        return JSONResponse(201, {"protected": True, "event": event_view(event)})

    def _release(self, body: dict[str, Any]) -> JSONResponse:
        governor = self.governors.get(body)
        return JSONResponse(
            200,
            {
                "released": governor.release(body["event_id"]),
                "event_id": body["event_id"],
            },
        )

    def _flush(self, body: dict[str, Any]) -> JSONResponse:
        governor = self.governors.get(body)
        flushed = governor.flush(timeout=float(body.get("timeout_seconds", 10.0)))
        return JSONResponse(
            200 if flushed else 202,
            {"flushed": flushed, "status": governor.status()},
        )

    def _put(self, body: dict[str, Any]) -> JSONResponse:
        record = self.cache.put(
            body["text"],
            record_id=body.get("id"),
            namespace=body.get("namespace"),
            metadata=body.get("metadata"),
            source=body.get("source", "api"),
            importance=float(body.get("importance", 0.5)),
            ttl_seconds=body.get("ttl_seconds"),
            scope=body.get("scope", "project"),
            owner_session_id=body.get("owner_session_id"),
            team_id=body.get("team_id"),
        )
        return JSONResponse(201, record.to_dict())

    def _ingest(self, body: dict[str, Any]) -> JSONResponse:
        records = self.cache.ingest(
            body["text"],
            source=body.get("source", "api"),
            namespace=body.get("namespace"),
            metadata=body.get("metadata"),
            importance=float(body.get("importance", 0.5)),
            chunk_tokens=int(body.get("chunk_tokens", 450)),
            overlap_tokens=int(body.get("overlap_tokens", 60)),
            replace_source=bool(body.get("replace_source", False)),
            scope=body.get("scope", "project"),
            owner_session_id=body.get("owner_session_id"),
            team_id=body.get("team_id"),
        )
        return JSONResponse(
            201,
            {"count": len(records), "records": [r.to_dict() for r in records]},
        )

    def _query(self, body: dict[str, Any]) -> JSONResponse:
        hits = self.cache.retrieve(
            body["query"],
            top_k=bounded_integer(
                body.get("top_k", 8),
                name="top_k",
                minimum=1,
                maximum=MAX_RESULT_BOOKS,
            ),
            namespace=body.get("namespace"),
            filters=body.get("filters"),
            minimum_score=float(body.get("minimum_score", 0.0)),
            scopes=bounded_string_tuple(
                body.get("scopes"),
                name="scopes",
                maximum_items=3,
            )
            or None,
            session_id=body.get("session_id"),
            team_ids=bounded_string_tuple(
                body.get("team_ids"),
                name="team_ids",
                maximum_items=MAX_RESULT_BOOKS,
            ),
        )
        return JSONResponse(200, search_view(body["query"], hits))

    def _refresh_desk(self, body: dict[str, Any]) -> JSONResponse:
        return self._lay_out_desk(body, periodic=False)

    def _watch_desk(self, body: dict[str, Any]) -> JSONResponse:
        return self._lay_out_desk(body, periodic=True)

    def _lay_out_desk(self, body: dict[str, Any], *, periodic: bool) -> JSONResponse:
        kwargs = {
            "session_id": body["session_id"],
            "focus": body["focus"],
            "token_budget": bounded_integer(
                body.get("token_budget", 4000),
                name="token_budget",
                minimum=64,
                maximum=MAX_CONTEXT_TOKENS,
            ),
            "top_k": bounded_integer(
                body.get("top_k", 12),
                name="top_k",
                minimum=1,
                maximum=MAX_RESULT_BOOKS,
            ),
            "namespace": body.get("namespace"),
            "filters": body.get("filters"),
            "pinned_record_ids": list(
                bounded_string_tuple(
                    body.get("pinned_record_ids"),
                    name="pinned_record_ids",
                    maximum_items=MAX_RESULT_BOOKS,
                )
            ),
            "team_ids": bounded_string_tuple(
                body.get("team_ids"),
                name="team_ids",
                maximum_items=MAX_RESULT_BOOKS,
            ),
        }
        if periodic:
            working = self.swapper.start_periodic(
                **kwargs,
                interval_seconds=float(body.get("interval_seconds", 30.0)),
            )
        else:
            working = self.swapper.refresh(**kwargs)
        return JSONResponse(200, desk_view(working))

    def _delete(self, path: str, query_string: str) -> JSONResponse:
        if path.startswith(("/books/", "/records/")):
            prefix = "/books/" if path.startswith("/books/") else "/records/"
            record_id = unquote(path.removeprefix(prefix))
            collection = self._collection_from_query(
                query_string,
                accept_namespace_alias=True,
            )
            scopes, session_id, team_ids = self._visibility_from_query(query_string)
            return JSONResponse(
                200,
                {
                    "deleted": self.cache.delete(
                        record_id,
                        namespace=collection,
                        scopes=scopes,
                        session_id=session_id,
                        team_ids=team_ids,
                    )
                },
            )
        if path.startswith(("/desk/watch/", "/context/watch/")):
            prefix = (
                "/desk/watch/" if path.startswith("/desk/watch/") else "/context/watch/"
            )
            session_id = unquote(path.removeprefix(prefix))
            collection = self._collection_from_query(query_string)
            return JSONResponse(
                200,
                {
                    "stopped": self.swapper.stop_periodic(
                        session_id,
                        namespace=collection,
                    )
                },
            )
        return self._not_found()
