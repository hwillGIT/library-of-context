from __future__ import annotations

import hmac
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import socket
from typing import Any

from .client import is_loopback_host
from .daemon_auth import validate_daemon_token
from .engine import ContextCache
from .http_app import LibraryHTTPApplication
from .process_lock import DatabaseRuntimeLock
from .swapper import ContextSwapper
from .version import __version__


class ServerDrainTimeout(RuntimeError):
    """Signal that the bounded drain ended with active requests."""


class _LibraryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        application: LibraryHTTPApplication,
        *,
        max_connections: int,
        read_timeout_seconds: float,
        shutdown_timeout_seconds: float,
        auth_token: str,
        database_lock: DatabaseRuntimeLock,
        close_database_lock: bool,
    ) -> None:
        self.application = application
        self.database_lock = database_lock
        self.close_database_lock = close_database_lock
        self.read_timeout_seconds = read_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.auth_token = validate_daemon_token(auth_token)
        self._admission = threading.BoundedSemaphore(max_connections)
        self._admission_condition = threading.Condition(threading.RLock())
        self._active_requests = 0
        self._rejected_requests = 0
        self._accepting_requests = True
        self.drain_timed_out = False
        self._close_lock = threading.Lock()
        self._listener_closed = False
        self._application_closed = False
        super().__init__(server_address, _LibraryRequestHandler)

    def process_request(
        self,
        request: socket,
        client_address: tuple[str, int],
    ) -> None:
        if not self._admission.acquire(blocking=False):
            with self._admission_condition:
                self._rejected_requests += 1
            self._reject_overload(request)
            return
        reject = False
        with self._admission_condition:
            if not self._accepting_requests:
                self._admission.release()
                self._rejected_requests += 1
                reject = True
            else:
                self._active_requests += 1
        if reject:
            self._reject_overload(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release_admission()
            raise

    def process_request_thread(
        self,
        request: socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_admission()

    def _release_admission(self) -> None:
        with self._admission_condition:
            self._active_requests -= 1
            self._admission_condition.notify_all()
        self._admission.release()

    def _reject_overload(self, request: socket) -> None:
        request.settimeout(0.05)
        received = b""
        try:
            while b"\r\n\r\n" not in received and len(received) < 16 * 1024:
                chunk = request.recv(4096)
                if not chunk:
                    break
                received += chunk
        except (OSError, TimeoutError):
            pass
        payload = b'{"error":"daemon request capacity is exhausted"}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Cache-Control: no-store\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
            + payload
        )
        try:
            request.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

    def admission_status(self) -> dict[str, int]:
        with self._admission_condition:
            return {
                "active_requests": self._active_requests,
                "rejected_requests": self._rejected_requests,
            }

    def drain_requests(self, *, deadline: float | None = None) -> bool:
        """Stop admission and wait within the configured shutdown bound."""

        if deadline is None:
            deadline = time.monotonic() + self.shutdown_timeout_seconds
        with self._admission_condition:
            self._accepting_requests = False
            remaining = max(0.0, deadline - time.monotonic())
            drained = self._admission_condition.wait_for(
                lambda: self._active_requests == 0,
                timeout=remaining,
            )
            self.drain_timed_out = not drained
            return drained

    def server_close(self) -> bool:
        deadline = time.monotonic() + self.shutdown_timeout_seconds
        with self._close_lock:
            with self._admission_condition:
                self._accepting_requests = False
            if not self._listener_closed:
                super().server_close()
                self._listener_closed = True
            if not self.drain_requests(deadline=deadline):
                return False
            if not self._application_closed:
                try:
                    self.application.close()
                finally:
                    if self.close_database_lock:
                        self.database_lock.close()
                self._application_closed = True
            return True


class _LibraryRequestHandler(BaseHTTPRequestHandler):
    server_version = f"LibraryOfContext/{__version__}"

    def setup(self) -> None:
        super().setup()
        if isinstance(self.server, _LibraryHTTPServer):
            self.connection.settimeout(self.server.read_timeout_seconds)

    @property
    def application(self) -> LibraryHTTPApplication:
        if not isinstance(self.server, _LibraryHTTPServer):
            raise RuntimeError(
                "Library request handler requires its application server"
            )
        return self.server.application

    def _send(
        self,
        status: int,
        value: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, content in (headers or {}).items():
            self.send_header(name, content)
        self.end_headers()
        self.wfile.write(payload)

    def _reject(
        self,
        status: int,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> bool:
        self.close_connection = True
        self._send(status, {"error": message}, headers=headers)
        return False

    def _request_is_authorized(self) -> bool:
        if not isinstance(self.server, _LibraryHTTPServer):
            return self._reject(500, "Library request handler is not initialized")

        raw_host = self.headers.get("Host")
        try:
            parsed_host = urllib.parse.urlsplit(f"//{raw_host or ''}")
        except ValueError:
            parsed_host = urllib.parse.SplitResult("", "", "", "", "")
        if (
            parsed_host.username is not None
            or parsed_host.password is not None
            or not is_loopback_host(parsed_host.hostname)
        ):
            return self._reject(403, "request Host must identify loopback")

        if self.headers.get("Origin") is not None:
            return self._reject(403, "browser-origin requests are not accepted")
        if self.headers.get("Sec-Fetch-Site", "").casefold() == "cross-site":
            return self._reject(403, "cross-site requests are not accepted")

        scheme, separator, credential = self.headers.get("Authorization", "").partition(
            " "
        )
        if (
            separator != " "
            or scheme.casefold() != "bearer"
            or not credential
            or not hmac.compare_digest(credential, self.server.auth_token)
        ):
            return self._reject(
                401,
                "valid daemon bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if (
            self.command == "POST"
            and self.headers.get_content_type() != "application/json"
        ):
            return self._reject(415, "POST body must use application/json")
        return True

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
        if not self._request_is_authorized():
            return
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
    cache: ContextCache,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    auth_token: str,
) -> tuple[_LibraryHTTPServer, ContextSwapper]:
    if not is_loopback_host(host):
        raise ValueError("Library daemon must bind to a loopback host")
    swapper = cache.runtime.swapper
    application = LibraryHTTPApplication(cache, swapper)
    settings = cache.runtime.settings
    database_lock = cache.database_runtime_lock
    if database_lock is None:
        raise RuntimeError("Library server requires a pre-open database owner lock")
    try:
        server = _LibraryHTTPServer(
            (host, port),
            application,
            max_connections=settings.http_max_connections,
            read_timeout_seconds=settings.http_read_timeout_seconds,
            shutdown_timeout_seconds=settings.http_shutdown_timeout_seconds,
            auth_token=auth_token,
            database_lock=database_lock,
            close_database_lock=False,
        )
    except Exception:
        application.close()
        raise
    return server, swapper


def run_server(
    cache: ContextCache,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    auth_token: str,
) -> None:
    server, swapper = create_server(cache, host, port, auth_token=auth_token)
    try:
        print(f"The Library of Context is listening on http://{host}:{port}")
        server.serve_forever()
    finally:
        drained = server.server_close()
        if drained:
            swapper.close()
        else:
            raise ServerDrainTimeout(
                "daemon shutdown timed out with active requests; runtime "
                "resources remain open"
            )
