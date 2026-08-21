from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from context_cache.client import (
    DAEMON_PROTOCOL_NAME,
    DAEMON_PROTOCOL_VERSION,
    DEFAULT_DAEMON_TIMEOUT_SECONDS,
    MCP_SCHEMA_VERSION,
    DaemonProtocolError,
    DaemonRequestError,
    LibraryDaemonClient,
)
from context_cache.daemon_auth import load_or_create_daemon_token
from context_cache.mcp_server import LibraryMCPServer
from context_cache.mcp_service import DaemonMCPTools
from context_cache.server import create_server
from context_cache.store import SCHEMA_VERSION
from library_of_context import LibraryOfContext, RuntimeSettings


class _DaemonHarness:
    def __init__(
        self,
        database: Path,
        *,
        runtime_settings: RuntimeSettings | None = None,
    ) -> None:
        self.library = LibraryOfContext(
            database,
            redis_url="",
            runtime_settings=runtime_settings,
            exclusive_database_owner=True,
        )
        self.token_file = Path(f"{database}.daemon-token")
        self.token = load_or_create_daemon_token(self.token_file)
        self.server, _ = create_server(
            self.library,
            "127.0.0.1",
            0,
            auth_token=self.token,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = int(self.server.server_address[1])
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.library.close()


class _BufferedStandardStream:
    def __init__(self, value: bytes = b"") -> None:
        self.buffer = io.BytesIO(value)


class DaemonBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "library.sqlite"
        self.daemon = _DaemonHarness(self.database)

    def tearDown(self) -> None:
        self.daemon.close()
        self.directory.cleanup()

    def test_health_exposes_protocol_schema_and_owned_runtime(self) -> None:
        client = LibraryDaemonClient(
            self.daemon.url,
            bearer_token=self.daemon.token,
        )
        health = client.health()

        self.assertEqual(client.timeout, DEFAULT_DAEMON_TIMEOUT_SECONDS)
        self.assertTrue(health["ok"])
        self.assertEqual(health["redis"], None)
        self.assertEqual(
            health["protocol"],
            {
                "name": DAEMON_PROTOCOL_NAME,
                "version": DAEMON_PROTOCOL_VERSION,
            },
        )
        self.assertEqual(health["schema"]["mcp_tools"], MCP_SCHEMA_VERSION)
        self.assertEqual(health["schema"]["sqlite"], SCHEMA_VERSION)
        self.assertIsInstance(health["runtime"]["id"], str)
        self.assertIn("indexer", health["runtime"])
        self.assertIn("settings", health["runtime"])

    def test_loopback_boundary_and_protocol_mismatch_are_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            LibraryDaemonClient(
                "http://example.com:8765",
                bearer_token="x" * 32,
            )
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_server(
                self.daemon.library,
                "0.0.0.0",
                0,
                auth_token=self.daemon.token,
            )

        client = LibraryDaemonClient(
            self.daemon.url,
            bearer_token=self.daemon.token,
        )
        with (
            mock.patch(
                "context_cache.http_app.DAEMON_PROTOCOL_VERSION",
                "incompatible",
            ),
            self.assertRaisesRegex(
                DaemonProtocolError,
                "protocol mismatch: expected .* received",
            ),
        ):
            client.health()

        with self.assertRaises(DaemonRequestError) as unauthorized:
            LibraryDaemonClient(
                self.daemon.url,
                bearer_token="wrong-daemon-token-000000000000000",
            ).health()
        self.assertEqual(unauthorized.exception.status, 401)

    def test_second_daemon_owner_for_one_database_is_rejected(self) -> None:
        with (
            mock.patch("context_cache.engine.SQLiteStore") as sqlite_store,
            self.assertRaisesRegex(RuntimeError, "another Library runtime owns"),
        ):
            LibraryOfContext(
                self.database,
                redis_url="",
                exclusive_database_owner=True,
            )
        sqlite_store.assert_not_called()

    def test_two_thin_bridges_share_the_daemon_runtime(self) -> None:
        first = LibraryMCPServer(
            daemon_client=LibraryDaemonClient(
                self.daemon.url,
                bearer_token=self.daemon.token,
            )
        )
        second = LibraryMCPServer(
            daemon_client=LibraryDaemonClient(
                self.daemon.url,
                bearer_token=self.daemon.token,
            )
        )
        try:
            self.assertEqual(first.runtime_id, second.runtime_id)
            self.assertIsNone(first.library)
            self.assertIsNone(first.desk)

            shelved = first.call_tool(
                "library_shelve",
                {
                    "book_id": "shared-runtime",
                    "text": "The daemon owns the shared Library runtime.",
                },
            )
            self.assertFalse(shelved["isError"])

            consulted = second.call_tool(
                "library_consult",
                {"subject": "shared Library runtime", "max_books": 3},
            )
            self.assertFalse(consulted["isError"])
            self.assertEqual(
                consulted["structuredContent"]["hits"][0]["book"]["id"],
                "shared-runtime",
            )

            first.close()
            stats = second.call_tool("library_stats", {})
            self.assertFalse(stats["isError"])
            self.assertIsNone(stats["structuredContent"]["redis"])
        finally:
            first.close()
            second.close()

    def test_thin_bridge_supplies_default_collection_without_overriding_one(
        self,
    ) -> None:
        client = mock.create_autospec(LibraryDaemonClient, instance=True)
        client.health.return_value = {"runtime": {"id": "test-runtime"}}
        client.call_mcp_tool.side_effect = lambda _name, arguments: arguments
        tools = DaemonMCPTools(client, default_collection="project-a")

        omitted: dict[str, object] = {"subject": "deployment"}
        self.assertEqual(
            tools.call("library_consult", omitted),
            {"subject": "deployment", "collection": "project-a"},
        )
        self.assertEqual(omitted, {"subject": "deployment"})
        self.assertEqual(
            tools.call(
                "library_consult",
                {"subject": "deployment", "collection": "project-b"},
            ),
            {"subject": "deployment", "collection": "project-b"},
        )
        for empty_collection in (None, "", "   "):
            with self.subTest(collection=empty_collection):
                self.assertEqual(
                    tools.call(
                        "library_consult",
                        {
                            "subject": "deployment",
                            "collection": empty_collection,
                        },
                    ),
                    {"subject": "deployment", "collection": "project-a"},
                )
        with self.assertRaisesRegex(ValueError, "collection must be a string"):
            tools.call(
                "library_consult",
                {"subject": "deployment", "collection": 3},
            )

    def test_remote_stdio_configuration_reaches_client_and_bridge(self) -> None:
        from context_cache import mcp_server

        client = mock.create_autospec(LibraryDaemonClient, instance=True)
        client.health.return_value = {"runtime": {"id": "test-runtime"}}
        bridge = mock.Mock()
        standard_input = _BufferedStandardStream()
        standard_output = _BufferedStandardStream()
        with (
            mock.patch.object(
                mcp_server,
                "LibraryDaemonClient",
                return_value=client,
            ) as client_factory,
            mock.patch.object(
                mcp_server,
                "LibraryMCPServer",
                return_value=bridge,
            ) as bridge_factory,
            mock.patch.object(mcp_server.sys, "stdin", standard_input),
            mock.patch.object(mcp_server.sys, "stdout", standard_output),
        ):
            result = mcp_server.main(
                [
                    "--daemon-url",
                    self.daemon.url,
                    "--daemon-token-file",
                    str(self.daemon.token_file),
                    "--namespace",
                    "project-a",
                    "--daemon-timeout-seconds",
                    "37",
                ]
            )

        self.assertEqual(result, 0)
        client_factory.assert_called_once_with(
            self.daemon.url,
            bearer_token=self.daemon.token,
            timeout=37.0,
        )
        bridge_factory.assert_called_once_with(
            daemon_client=client,
            default_collection="project-a",
        )
        bridge.close.assert_called_once_with()

    def test_remote_stdio_mode_opens_no_local_library_or_embedder(self) -> None:
        from context_cache import mcp_server

        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "library_stats", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        payload = b"".join(
            json.dumps(message).encode("utf-8") + b"\n" for message in messages
        )
        standard_input = _BufferedStandardStream(payload)
        standard_output = _BufferedStandardStream()
        with (
            mock.patch.object(mcp_server, "LibraryOfContext") as local_library,
            mock.patch.object(mcp_server, "HashingEmbedder") as hashing_embedder,
            mock.patch.object(mcp_server, "OllamaEmbedder") as ollama_embedder,
            mock.patch.object(mcp_server.sys, "stdin", standard_input),
            mock.patch.object(mcp_server.sys, "stdout", standard_output),
        ):
            result = mcp_server.main(
                [
                    "--daemon-url",
                    self.daemon.url,
                    "--daemon-token-file",
                    str(self.daemon.token_file),
                ]
            )

        self.assertEqual(result, 0)
        responses = [
            json.loads(line)
            for line in standard_output.buffer.getvalue().decode("utf-8").splitlines()
        ]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertIsNone(responses[1]["result"]["structuredContent"]["redis"])
        local_library.assert_not_called()
        hashing_embedder.assert_not_called()
        ollama_embedder.assert_not_called()

    def test_remote_stdio_mode_requires_a_token_file(self) -> None:
        from context_cache import mcp_server

        error_output = io.StringIO()
        with (
            mock.patch.object(mcp_server, "LibraryOfContext") as local_library,
            mock.patch.object(mcp_server, "LibraryDaemonClient") as client,
            mock.patch.object(mcp_server.sys, "stderr", error_output),
        ):
            result = mcp_server.main(["--daemon-url", self.daemon.url])

        self.assertEqual(result, 2)
        self.assertIn("--daemon-token-file is required", error_output.getvalue())
        local_library.assert_not_called()
        client.assert_not_called()

    def test_remote_stdio_mode_reports_an_unreadable_token_file(self) -> None:
        from context_cache import mcp_server

        error_output = io.StringIO()
        missing_token = Path(self.directory.name) / "missing.token"
        with (
            mock.patch.object(mcp_server, "LibraryOfContext") as local_library,
            mock.patch.object(mcp_server, "LibraryDaemonClient") as client,
            mock.patch.object(mcp_server.sys, "stderr", error_output),
        ):
            result = mcp_server.main(
                [
                    "--daemon-url",
                    self.daemon.url,
                    "--daemon-token-file",
                    str(missing_token),
                ]
            )

        self.assertEqual(result, 2)
        self.assertIn("Library MCP bridge could not start", error_output.getvalue())
        self.assertIn(str(missing_token), error_output.getvalue())
        local_library.assert_not_called()
        client.assert_not_called()

    def test_request_admission_rejects_overload_without_spawning_a_thread(self) -> None:
        self.daemon.close()
        self.daemon = _DaemonHarness(
            self.database,
            runtime_settings=RuntimeSettings(http_max_connections=1),
        )
        first_entered = threading.Event()
        release_first = threading.Event()
        first_failures: list[BaseException] = []
        original_dispatch = self.daemon.server.application.dispatch

        def blocking_dispatch(*args: object, **kwargs: object) -> object:
            first_entered.set()
            if not release_first.wait(2):
                raise TimeoutError("admitted request was not released")
            return original_dispatch(*args, **kwargs)

        def first_request() -> None:
            try:
                LibraryDaemonClient(
                    self.daemon.url,
                    bearer_token=self.daemon.token,
                ).health()
            except BaseException as exc:
                first_failures.append(exc)

        with mock.patch.object(
            self.daemon.server.application,
            "dispatch",
            side_effect=blocking_dispatch,
        ):
            first = threading.Thread(target=first_request)
            first.start()
            self.assertTrue(first_entered.wait(2))

            with self.assertRaises(DaemonRequestError) as rejected:
                LibraryDaemonClient(
                    self.daemon.url,
                    bearer_token=self.daemon.token,
                ).health()
            self.assertEqual(rejected.exception.status, 503)
            self.assertEqual(
                self.daemon.server.admission_status(),
                {"active_requests": 1, "rejected_requests": 1},
            )

            release_first.set()
            first.join(2)

        self.assertFalse(first.is_alive())
        self.assertEqual(first_failures, [])
        deadline = time.monotonic() + 2
        while (
            self.daemon.server.admission_status()["active_requests"]
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(self.daemon.server.admission_status()["active_requests"], 0)


class DaemonRecoveryTests(unittest.TestCase):
    def test_restart_recovers_protected_context_without_redis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            first_daemon = _DaemonHarness(database)
            first_bridge = LibraryMCPServer(
                daemon_client=LibraryDaemonClient(
                    first_daemon.url,
                    bearer_token=first_daemon.token,
                )
            )
            settings = {
                "session_id": "restart-thread",
                "token_budget": 500,
                "recent_token_budget": 120,
                "protected_token_budget": 100,
            }
            protected = first_bridge.call_tool(
                "library_context_protect",
                {
                    **settings,
                    "content": "Use a canary before production deployment.",
                    "label": "deployment-policy",
                },
            )
            event_id = protected["structuredContent"]["event"]["event_id"]
            flushed = first_bridge.call_tool(
                "library_context_flush",
                {**settings, "timeout_seconds": 3},
            )
            self.assertTrue(flushed["structuredContent"]["flushed"])
            first_bridge.close()
            first_daemon.close()

            second_daemon = _DaemonHarness(database)
            second_bridge = LibraryMCPServer(
                daemon_client=LibraryDaemonClient(
                    second_daemon.url,
                    bearer_token=second_daemon.token,
                )
            )
            try:
                prepared = second_bridge.call_tool(
                    "library_context_prepare",
                    {
                        **settings,
                        "user_message": "What is the deployment policy?",
                    },
                )
                envelope = prepared["structuredContent"]
                self.assertFalse(prepared["isError"])
                self.assertIn(event_id, envelope["protected_event_ids"])
                self.assertLessEqual(envelope["token_count"], envelope["token_budget"])
                self.assertIsNone(second_daemon.library.redis)
            finally:
                second_bridge.close()
                second_daemon.close()


if __name__ == "__main__":
    unittest.main()
