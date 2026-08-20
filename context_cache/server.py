from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .engine import ContextCache
from .governor import LibraryContextGovernor
from .swapper import ContextSwapper


def create_server(
    cache: ContextCache, host: str = "127.0.0.1", port: int = 8765
) -> tuple[ThreadingHTTPServer, ContextSwapper]:
    swapper = ContextSwapper(cache)
    governors: dict[tuple[str, str], LibraryContextGovernor] = {}
    governors_lock = threading.RLock()

    def governor_for(body: dict[str, Any]) -> LibraryContextGovernor:
        session_id = str(body.get("session_id", "default"))
        collection = str(body.get("collection") or cache.namespace)
        key = (collection, session_id)
        with governors_lock:
            governor = governors.get(key)
            token_budget = int(
                body.get(
                    "token_budget",
                    12000 if governor is None else governor.token_budget,
                )
            )
            recent_budget = int(
                body.get(
                    "recent_token_budget",
                    4000 if governor is None else governor.recent_token_budget,
                )
            )
            protected_budget = int(
                body.get(
                    "protected_token_budget",
                    2000 if governor is None else governor.protected_token_budget,
                )
            )
            max_books = int(
                body.get("max_books", 12 if governor is None else governor.max_books)
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
                governor = cache.open_context_governor(
                    session_id,
                    collection=collection,
                    token_budget=token_budget,
                    recent_token_budget=recent_budget,
                    protected_token_budget=protected_budget,
                    max_books=max_books,
                )
                governors[key] = governor
            return governor

    class Handler(BaseHTTPRequestHandler):
        server_version = "LibraryOfContext/0.2"

        def _send(self, status: int, value: Any) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 10 * 1024 * 1024:
                raise ValueError("request body exceeds 10 MiB")
            if length == 0:
                return {}
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _handle(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if self.command == "GET" and path == "/health":
                redis = None if cache.redis is None else cache.redis.stats()
                self._send(
                    200,
                    {
                        "ok": True,
                        "service": "The Library of Context",
                        "sqlite": True,
                        "redis": None if redis is None else redis["enabled"],
                    },
                )
                return
            if self.command == "GET" and path == "/stats":
                self._send(200, cache.stats())
                return
            if self.command == "GET" and path.startswith(
                ("/governor/status/", "/context/status/")
            ):
                prefix = (
                    "/governor/status/"
                    if path.startswith("/governor/status/")
                    else "/context/status/"
                )
                session_id = unquote(path.removeprefix(prefix))
                self._send(200, governor_for({"session_id": session_id}).status())
                return
            if self.command == "GET" and path.startswith(("/desk/", "/context/")):
                prefix = "/desk/" if path.startswith("/desk/") else "/context/"
                session_id = unquote(path.removeprefix(prefix))
                working = swapper.get(session_id)
                self._send(
                    404 if working is None else 200,
                    None if working is None else working.to_dict(),
                )
                return

            body = self._body() if self.command == "POST" else {}
            if self.command == "POST" and path in {
                "/governor/prepare",
                "/context/prepare",
            }:
                governor = governor_for(body)
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
                self._send(200, envelope.to_dict())
                return
            if self.command == "POST" and path in {
                "/governor/commit",
                "/context/commit",
            }:
                governor = governor_for(body)
                event = governor.commit(
                    body["content"],
                    role=body.get("role", "assistant"),
                    metadata=body.get("metadata"),
                    importance=(
                        None
                        if body.get("importance") is None
                        else float(body["importance"])
                    ),
                    protected=(
                        None
                        if body.get("protected") is None
                        else bool(body["protected"])
                    ),
                    event_id=body.get("event_id"),
                )
                self._send(
                    201,
                    {
                        "recorded": True,
                        "event": event.to_dict(),
                        "watermarks": governor.status()["watermarks"],
                    },
                )
                return
            if self.command == "POST" and path in {
                "/governor/protect",
                "/context/protect",
            }:
                governor = governor_for(body)
                event = governor.protect(
                    body["content"],
                    role=body.get("role", "developer"),
                    label=body.get("label"),
                    importance=float(body.get("importance", 1.0)),
                    event_id=body.get("event_id"),
                )
                self._send(201, {"protected": True, "event": event.to_dict()})
                return
            if self.command == "POST" and path in {
                "/governor/release",
                "/context/release",
            }:
                governor = governor_for(body)
                self._send(
                    200,
                    {
                        "released": governor.release(body["event_id"]),
                        "event_id": body["event_id"],
                    },
                )
                return
            if self.command == "POST" and path in {
                "/governor/flush",
                "/context/flush",
            }:
                governor = governor_for(body)
                flushed = governor.flush(
                    timeout=float(body.get("timeout_seconds", 10.0))
                )
                self._send(
                    200 if flushed else 202,
                    {"flushed": flushed, "status": governor.status()},
                )
                return
            if self.command == "POST" and path in {"/books", "/records"}:
                record = cache.put(
                    body["text"],
                    record_id=body.get("id"),
                    namespace=body.get("namespace"),
                    metadata=body.get("metadata"),
                    source=body.get("source", "api"),
                    importance=float(body.get("importance", 0.5)),
                    ttl_seconds=body.get("ttl_seconds"),
                )
                self._send(201, record.to_dict())
                return
            if self.command == "POST" and path in {"/library/ingest", "/ingest"}:
                records = cache.ingest(
                    body["text"],
                    source=body.get("source", "api"),
                    namespace=body.get("namespace"),
                    metadata=body.get("metadata"),
                    importance=float(body.get("importance", 0.5)),
                    chunk_tokens=int(body.get("chunk_tokens", 450)),
                    overlap_tokens=int(body.get("overlap_tokens", 60)),
                    replace_source=bool(body.get("replace_source", False)),
                )
                self._send(
                    201,
                    {"count": len(records), "records": [r.to_dict() for r in records]},
                )
                return
            if self.command == "POST" and path in {"/catalog/query", "/query"}:
                hits = cache.retrieve(
                    body["query"],
                    top_k=int(body.get("top_k", 8)),
                    namespace=body.get("namespace"),
                    filters=body.get("filters"),
                    minimum_score=float(body.get("minimum_score", 0.0)),
                )
                self._send(200, {"hits": [hit.to_dict() for hit in hits]})
                return
            if self.command == "POST" and path in {
                "/desk/refresh",
                "/desk/watch",
                "/context/refresh",
                "/context/watch",
            }:
                kwargs = {
                    "session_id": body["session_id"],
                    "focus": body["focus"],
                    "token_budget": int(body.get("token_budget", 4000)),
                    "top_k": int(body.get("top_k", 12)),
                    "namespace": body.get("namespace"),
                    "filters": body.get("filters"),
                    "pinned_record_ids": body.get("pinned_record_ids"),
                }
                if path.endswith("/watch"):
                    working = swapper.start_periodic(
                        **kwargs,
                        interval_seconds=float(body.get("interval_seconds", 30.0)),
                    )
                else:
                    working = swapper.refresh(**kwargs)
                self._send(200, working.to_dict())
                return
            if self.command == "DELETE" and path.startswith(("/books/", "/records/")):
                prefix = "/books/" if path.startswith("/books/") else "/records/"
                record_id = unquote(path.removeprefix(prefix))
                self._send(200, {"deleted": cache.delete(record_id)})
                return
            if self.command == "DELETE" and path.startswith(
                ("/desk/watch/", "/context/watch/")
            ):
                prefix = (
                    "/desk/watch/"
                    if path.startswith("/desk/watch/")
                    else "/context/watch/"
                )
                session_id = unquote(path.removeprefix(prefix))
                self._send(200, {"stopped": swapper.stop_periodic(session_id)})
                return
            self._send(404, {"error": "not found"})

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._handle()
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)})
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            self.do_GET()

        def do_DELETE(self) -> None:  # noqa: N802
            self.do_GET()

    class LibraryHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self) -> None:
            with governors_lock:
                active = list(governors.values())
                governors.clear()
            for governor in active:
                governor.close()
            super().server_close()

    return LibraryHTTPServer((host, port), Handler), swapper


def run_server(cache: ContextCache, host: str = "127.0.0.1", port: int = 8765) -> None:
    server, swapper = create_server(cache, host, port)
    try:
        print(f"The Library of Context is listening on http://{host}:{port}")
        server.serve_forever()
    finally:
        server.server_close()
        swapper.close()
