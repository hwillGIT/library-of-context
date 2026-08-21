from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .daemon_auth import validate_daemon_token

DAEMON_PROTOCOL_NAME = "library-of-context-daemon"
DAEMON_PROTOCOL_VERSION = "1"
MCP_SCHEMA_VERSION = "2"
DEFAULT_DAEMON_TIMEOUT_SECONDS = 120.0


class DaemonClientError(RuntimeError):
    """Base error for daemon transport and compatibility failures."""


class DaemonConnectionError(DaemonClientError):
    """The loopback daemon could not be reached."""


class DaemonProtocolError(DaemonClientError):
    """The daemon and client do not implement the same protocol contract."""


class DaemonRequestError(DaemonClientError):
    """The daemon rejected a request or returned an invalid response."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"daemon request failed with HTTP {status}: {message}")


def is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    address = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


class LibraryDaemonClient:
    """Call one Library daemon over its dependency-free loopback HTTP protocol."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str,
        timeout: float = DEFAULT_DAEMON_TIMEOUT_SECONDS,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http":
            raise ValueError("daemon URL must use http://")
        if not is_loopback_host(parsed.hostname):
            raise ValueError("daemon URL must identify a loopback host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("daemon URL must not contain credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("daemon URL must identify the service root")
        if timeout <= 0:
            raise ValueError("daemon timeout must be positive")
        self.base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "", "", "")
        ).rstrip("/")
        self.bearer_token = validate_daemon_token(bearer_token)
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @staticmethod
    def _error_message(value: Any) -> str:
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, dict):
                return str(error.get("message", error))
            if error is not None:
                return str(error)
        return str(value)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.bearer_token}",
        }
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                detail = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = raw.decode("utf-8", errors="replace")
            raise DaemonRequestError(
                exc.code,
                self._error_message(detail),
            ) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise DaemonConnectionError(
                f"cannot reach Library daemon at {self.base_url}: {exc}"
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DaemonRequestError(status, "response is not valid JSON") from exc
        if not isinstance(value, dict):
            raise DaemonRequestError(status, "response must be a JSON object")
        return value

    def health(self) -> dict[str, Any]:
        health = self._request("GET", "/health")
        protocol = health.get("protocol")
        if not isinstance(protocol, dict):
            raise DaemonProtocolError("daemon health omits the protocol contract")
        received_name = protocol.get("name")
        received_version = protocol.get("version")
        if (
            received_name != DAEMON_PROTOCOL_NAME
            or received_version != DAEMON_PROTOCOL_VERSION
        ):
            raise DaemonProtocolError(
                "daemon protocol mismatch: expected "
                f"{DAEMON_PROTOCOL_NAME}/{DAEMON_PROTOCOL_VERSION}, received "
                f"{received_name}/{received_version}"
            )
        schema = health.get("schema")
        received_schema = schema.get("mcp_tools") if isinstance(schema, dict) else None
        if received_schema != MCP_SCHEMA_VERSION:
            raise DaemonProtocolError(
                "daemon MCP schema mismatch: expected "
                f"{MCP_SCHEMA_VERSION}, received {received_schema}"
            )
        runtime = health.get("runtime")
        if not isinstance(runtime, dict) or not isinstance(runtime.get("id"), str):
            raise DaemonProtocolError("daemon health omits the runtime identity")
        return health

    def call_mcp_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not name:
            raise ValueError("MCP tool name must not be empty")
        if not isinstance(arguments, dict):
            raise TypeError("MCP tool arguments must be a JSON object")
        return self._request(
            "POST",
            "/mcp/call",
            {
                "protocol_version": DAEMON_PROTOCOL_VERSION,
                "schema_version": MCP_SCHEMA_VERSION,
                "name": name,
                "arguments": arguments,
            },
        )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.call_mcp_tool(name, arguments)


DaemonClient = LibraryDaemonClient
