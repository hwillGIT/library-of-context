from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .engine import ContextCache
from .http_app import LibraryHTTPApplication
from .swapper import ContextSwapper


class _LibraryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        application: LibraryHTTPApplication,
    ) -> None:
        self.application = application
        super().__init__(server_address, _LibraryRequestHandler)

    def server_close(self) -> None:
        self.application.close()
        super().server_close()


class _LibraryRequestHandler(BaseHTTPRequestHandler):
    server_version = "LibraryOfContext/0.2"

    @property
    def application(self) -> LibraryHTTPApplication:
        if not isinstance(self.server, _LibraryHTTPServer):
            raise RuntimeError(
                "Library request handler requires its application server"
            )
        return self.server.application

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
        body = self._body() if self.command == "POST" else None
        response = self.application.dispatch(self.command, self.path, body)
        self._send(response.status, response.body)

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


def create_server(
    cache: ContextCache, host: str = "127.0.0.1", port: int = 8765
) -> tuple[ThreadingHTTPServer, ContextSwapper]:
    swapper = ContextSwapper(cache)
    application = LibraryHTTPApplication(cache, swapper)
    return _LibraryHTTPServer((host, port), application), swapper


def run_server(cache: ContextCache, host: str = "127.0.0.1", port: int = 8765) -> None:
    server, swapper = create_server(cache, host, port)
    try:
        print(f"The Library of Context is listening on http://{host}:{port}")
        server.serve_forever()
    finally:
        server.server_close()
        swapper.close()
