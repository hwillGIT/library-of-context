from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from . import mcp_views
from .client import (
    DEFAULT_DAEMON_TIMEOUT_SECONDS,
    DaemonClientError,
    LibraryDaemonClient,
)
from .daemon_auth import read_daemon_token
from .embeddings import HashingEmbedder, OllamaEmbedder
from .library import LibraryOfContext
from .mcp_schema import SERVER_INSTRUCTIONS, TOOLS
from .mcp_service import DaemonMCPTools, LibraryMCPTools
from .version import __version__

_book = mcp_views.book_view
_desk = mcp_views.desk_view
_event = mcp_views.event_view
_governed = mcp_views.governed_prompt_view
_hit = mcp_views.hit_view
_prompt = mcp_views.prompt_view
_tool_error = mcp_views.tool_error
_tool_result = mcp_views.tool_result


class LibraryMCPServer:
    def __init__(
        self,
        library: LibraryOfContext | None = None,
        *,
        daemon_client: LibraryDaemonClient | None = None,
        default_collection: str | None = None,
    ) -> None:
        if (library is None) == (daemon_client is None):
            raise ValueError("provide one local Library or one daemon client")
        if daemon_client is not None:
            remote_tools = DaemonMCPTools(
                daemon_client,
                default_collection=default_collection,
            )
            self._tools = remote_tools
            self.library = None
            self.desk = None
            self._session_registry = None
            self._governor_registry = None
            self.sessions: dict[str, Any] = {}
            self.governors: dict[str, Any] = {}
            self.runtime_id: str | None = remote_tools.runtime_id
        else:
            assert library is not None
            local_tools = LibraryMCPTools(library)
            self._tools = local_tools
            self.library = local_tools.library
            self.desk = local_tools.desk
            self._session_registry = local_tools.session_registry
            self._governor_registry = local_tools.governor_registry
            self.sessions = local_tools.sessions
            self.governors = local_tools.governors
            self.runtime_id = None
        self.shutdown_requested = False
        self.exit_requested = False
        self._request_handlers: dict[
            str, Callable[[dict[str, Any]], dict[str, Any]]
        ] = {
            "initialize": self._initialize,
            "ping": self._ping,
            "tools/list": self._list_tools,
            "tools/call": self._call_tool_request,
            "shutdown": self._shutdown,
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tools.call(name, arguments)

    @staticmethod
    def _initialize(message: dict[str, Any]) -> dict[str, Any]:
        params = message.get("params") or {}
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "library-of-context", "version": __version__},
            "instructions": SERVER_INSTRUCTIONS,
        }

    @staticmethod
    def _ping(_message: dict[str, Any]) -> dict[str, Any]:
        return {}

    @staticmethod
    def _list_tools(_message: dict[str, Any]) -> dict[str, Any]:
        return {"tools": TOOLS}

    def _call_tool_request(self, message: dict[str, Any]) -> dict[str, Any]:
        params = message.get("params") or {}
        return self.call_tool(
            str(params.get("name", "")),
            dict(params.get("arguments") or {}),
        )

    def _shutdown(self, _message: dict[str, Any]) -> dict[str, Any]:
        self.shutdown_requested = True
        return {}

    def _handle_notification(self, method: Any) -> bool:
        if method == "exit":
            self.exit_requested = True
            return True
        return method in {"notifications/initialized", "notifications/cancelled"}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if self._handle_notification(method):
            return None
        if request_id is None:
            return None
        callback = self._request_handlers.get(str(method))
        if callback is None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        try:
            result = callback(message)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }

    def close(self) -> None:
        self._tools.close()


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
    parser.add_argument(
        "--daemon-url",
        default=os.environ.get("LIBRARY_OF_CONTEXT_DAEMON_URL"),
        help=(
            "forward MCP tool calls to a loopback Library daemon instead of opening "
            "a local runtime"
        ),
    )
    parser.add_argument(
        "--daemon-timeout-seconds",
        type=float,
        default=os.environ.get(
            "LIBRARY_OF_CONTEXT_DAEMON_TIMEOUT_SECONDS",
            str(DEFAULT_DAEMON_TIMEOUT_SECONDS),
        ),
        help="loopback daemon request timeout in seconds",
    )
    parser.add_argument(
        "--daemon-token-file",
        default=os.environ.get("LIBRARY_OF_CONTEXT_DAEMON_TOKEN_FILE"),
        help="owner-readable bearer-token file for --daemon-url",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.daemon_url:
        try:
            if not args.daemon_token_file:
                raise ValueError("--daemon-token-file is required with --daemon-url")
            server = LibraryMCPServer(
                daemon_client=LibraryDaemonClient(
                    args.daemon_url,
                    bearer_token=read_daemon_token(args.daemon_token_file),
                    timeout=args.daemon_timeout_seconds,
                ),
                default_collection=args.namespace,
            )
        except (DaemonClientError, RuntimeError, ValueError) as exc:
            print(f"Library MCP bridge could not start: {exc}", file=sys.stderr)
            return 2
    else:
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
