from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .engine import ContextCache
from .resource_registry import GovernorRegistry
from .swapper import ContextSwapper


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
        self.governors = GovernorRegistry(cache, default_session_id="default")
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
        }

    def close(self) -> None:
        self.governors.close()

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
            return self._delete(path)
        return self._not_found()

    @staticmethod
    def _not_found() -> JSONResponse:
        return JSONResponse(404, {"error": "not found"})

    def _get(self, path: str, query_string: str) -> JSONResponse:
        if path == "/health":
            return self._health()
        if path == "/stats":
            return JSONResponse(200, self.cache.stats())
        if path.startswith(("/governor/status/", "/context/status/")):
            return self._governor_status(path, query_string)
        if path.startswith(("/desk/", "/context/")):
            return self._desk_status(path)
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
            },
        )

    def _governor_status(self, path: str, query_string: str) -> JSONResponse:
        prefix = (
            "/governor/status/"
            if path.startswith("/governor/status/")
            else "/context/status/"
        )
        session_id = unquote(path.removeprefix(prefix))
        query = parse_qs(query_string)
        collection = query.get("collection", [self.cache.namespace])[0]
        governor = self.governors.get(
            {"session_id": session_id, "collection": collection}
        )
        return JSONResponse(200, governor.status())

    def _desk_status(self, path: str) -> JSONResponse:
        prefix = "/desk/" if path.startswith("/desk/") else "/context/"
        session_id = unquote(path.removeprefix(prefix))
        working = self.swapper.get(session_id)
        return JSONResponse(
            404 if working is None else 200,
            None if working is None else working.to_dict(),
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
        return JSONResponse(200, envelope.to_dict())

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
                "event": event.to_dict(),
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
        return JSONResponse(201, {"protected": True, "event": event.to_dict()})

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
        )
        return JSONResponse(
            201,
            {"count": len(records), "records": [r.to_dict() for r in records]},
        )

    def _query(self, body: dict[str, Any]) -> JSONResponse:
        hits = self.cache.retrieve(
            body["query"],
            top_k=int(body.get("top_k", 8)),
            namespace=body.get("namespace"),
            filters=body.get("filters"),
            minimum_score=float(body.get("minimum_score", 0.0)),
        )
        return JSONResponse(200, {"hits": [hit.to_dict() for hit in hits]})

    def _refresh_desk(self, body: dict[str, Any]) -> JSONResponse:
        return self._lay_out_desk(body, periodic=False)

    def _watch_desk(self, body: dict[str, Any]) -> JSONResponse:
        return self._lay_out_desk(body, periodic=True)

    def _lay_out_desk(self, body: dict[str, Any], *, periodic: bool) -> JSONResponse:
        kwargs = {
            "session_id": body["session_id"],
            "focus": body["focus"],
            "token_budget": int(body.get("token_budget", 4000)),
            "top_k": int(body.get("top_k", 12)),
            "namespace": body.get("namespace"),
            "filters": body.get("filters"),
            "pinned_record_ids": body.get("pinned_record_ids"),
        }
        if periodic:
            working = self.swapper.start_periodic(
                **kwargs,
                interval_seconds=float(body.get("interval_seconds", 30.0)),
            )
        else:
            working = self.swapper.refresh(**kwargs)
        return JSONResponse(200, working.to_dict())

    def _delete(self, path: str) -> JSONResponse:
        if path.startswith(("/books/", "/records/")):
            prefix = "/books/" if path.startswith("/books/") else "/records/"
            record_id = unquote(path.removeprefix(prefix))
            return JSONResponse(200, {"deleted": self.cache.delete(record_id)})
        if path.startswith(("/desk/watch/", "/context/watch/")):
            prefix = (
                "/desk/watch/" if path.startswith("/desk/watch/") else "/context/watch/"
            )
            session_id = unquote(path.removeprefix(prefix))
            return JSONResponse(
                200, {"stopped": self.swapper.stop_periodic(session_id)}
            )
        return self._not_found()
