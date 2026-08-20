from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from library_of_context import LibraryOfContext

ROOT = Path(__file__).resolve().parents[1]


class VirtualContextSessionTests(unittest.TestCase):
    def test_history_grows_while_live_prompt_stays_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                session = library.open_virtual_session(
                    "bounded",
                    token_budget=700,
                    recent_token_budget=180,
                )
                session.record(
                    "assistant",
                    "The deployment decision is a canary wave before production rollout.",
                    importance=0.95,
                )
                for index in range(60):
                    session.record(
                        "user", f"Routine bookkeeping question number {index}."
                    )
                    session.record(
                        "assistant", f"Routine bookkeeping answer number {index}."
                    )

                envelope = session.build_prompt(
                    user_message="What was the canary deployment decision?",
                    system_prompt="Use retrieved project context when relevant.",
                )
                self.assertEqual(envelope.history_books, 122)
                self.assertLessEqual(envelope.token_count, envelope.token_budget)
                self.assertLess(len(envelope.messages), envelope.history_books)
                self.assertIn("canary wave", envelope.messages[0]["content"])
                self.assertNotIn(envelope.recent_books[0], envelope.desk.swapped_in)
                session.record_assistant("The canary decision is confirmed.")
                self.assertEqual(len(session.history()), 123)
                session.close()

    def test_one_oversized_recent_message_cannot_break_the_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                session = library.open_virtual_session(
                    "oversized", token_budget=256, recent_token_budget=96
                )
                original = "X" * 4000
                envelope = session.build_prompt(user_message=original)
                self.assertLessEqual(envelope.token_count, envelope.token_budget)
                self.assertIn("full message remains", envelope.messages[-1]["content"])
                self.assertEqual(session.history()[-1].text, original)
                session.close()


class MCPProtocolTests(unittest.TestCase):
    def test_stdio_server_initializes_and_serves_the_full_paging_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "library_of_context.mcp_server",
                    "--no-redis",
                    "--db",
                    str(Path(directory) / "mcp.sqlite"),
                ],
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            self.assertIsNotNone(process.stdin)
            self.assertIsNotNone(process.stdout)
            responses: queue.Queue[str] = queue.Queue()

            def read_stdout() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    responses.put(line)

            reader = threading.Thread(target=read_stdout, daemon=True)
            reader.start()

            def send(message: dict[str, object], *, expect: bool = True) -> dict:
                assert process.stdin is not None
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
                if not expect:
                    return {}
                try:
                    line = responses.get(timeout=5)
                except queue.Empty as exc:
                    error = ""
                    if process.poll() is not None and process.stderr is not None:
                        error = process.stderr.read()
                    raise AssertionError(f"MCP response timed out: {error}") from exc
                return json.loads(line)

            def request(
                request_id: int, method: str, params: dict | None = None
            ) -> dict:
                message: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                }
                if params is not None:
                    message["params"] = params
                return send(message)

            try:
                initialized = request(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                )
                self.assertEqual(
                    initialized["result"]["serverInfo"]["name"],
                    "library-of-context",
                )
                self.assertIn("never append", initialized["result"]["instructions"])
                send(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    expect=False,
                )

                listed = request(2, "tools/list")
                tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
                expected = {
                    "library_shelve",
                    "library_consult",
                    "library_desk_refresh",
                    "library_desk_watch",
                    "library_message_record",
                    "library_prompt_build",
                    "library_context_prepare",
                    "library_context_commit",
                    "library_context_protect",
                    "library_context_release",
                    "library_context_status",
                    "library_context_flush",
                    "library_stats",
                }
                self.assertTrue(expected.issubset(tools))
                self.assertTrue(tools["library_consult"]["annotations"]["readOnlyHint"])
                self.assertFalse(tools["library_shelve"]["annotations"]["readOnlyHint"])

                shelved = request(
                    3,
                    "tools/call",
                    {
                        "name": "library_shelve",
                        "arguments": {
                            "book_id": "decision",
                            "text": "Use a canary deployment wave before production.",
                            "importance": 0.95,
                        },
                    },
                )
                self.assertFalse(shelved["result"]["isError"])
                self.assertEqual(
                    shelved["result"]["structuredContent"]["book"]["id"],
                    "decision",
                )

                consulted = request(
                    4,
                    "tools/call",
                    {
                        "name": "library_consult",
                        "arguments": {"subject": "canary deployment", "max_books": 3},
                    },
                )
                self.assertEqual(
                    consulted["result"]["structuredContent"]["hits"][0]["book"]["id"],
                    "decision",
                )

                refreshed = request(
                    5,
                    "tools/call",
                    {
                        "name": "library_desk_refresh",
                        "arguments": {
                            "subject": "deployment decision",
                            "session_id": "mcp-test",
                            "token_budget": 300,
                        },
                    },
                )
                desk = refreshed["result"]["structuredContent"]
                self.assertIn("decision", desk["swapped_in"])
                self.assertLessEqual(desk["token_count"], desk["token_budget"])

                prompt = request(
                    6,
                    "tools/call",
                    {
                        "name": "library_prompt_build",
                        "arguments": {
                            "session_id": "gateway-test",
                            "user_message": "What is the deployment decision?",
                            "system_prompt": "Answer from project context.",
                            "token_budget": 500,
                            "recent_token_budget": 120,
                        },
                    },
                )
                envelope = prompt["result"]["structuredContent"]
                self.assertLessEqual(envelope["token_count"], envelope["token_budget"])
                self.assertIn("<library-context", envelope["messages"][0]["content"])

                protected = request(
                    7,
                    "tools/call",
                    {
                        "name": "library_context_protect",
                        "arguments": {
                            "session_id": "governed-test",
                            "content": "Never deploy without a canary.",
                            "label": "deployment-rule",
                            "token_budget": 500,
                            "recent_token_budget": 120,
                            "protected_token_budget": 80,
                        },
                    },
                )
                protected_event = protected["result"]["structuredContent"]["event"]
                self.assertTrue(protected["result"]["structuredContent"]["protected"])

                prepared = request(
                    8,
                    "tools/call",
                    {
                        "name": "library_context_prepare",
                        "arguments": {
                            "session_id": "governed-test",
                            "user_message": "What is the deployment rule?",
                            "system_prompt": "Use governed context.",
                            "token_budget": 500,
                            "recent_token_budget": 120,
                            "protected_token_budget": 80,
                        },
                    },
                )
                governed = prepared["result"]["structuredContent"]
                self.assertTrue(governed["replaces_compaction"])
                self.assertIn(
                    protected_event["event_id"], governed["protected_event_ids"]
                )
                self.assertLessEqual(governed["token_count"], governed["token_budget"])

                committed = request(
                    9,
                    "tools/call",
                    {
                        "name": "library_context_commit",
                        "arguments": {
                            "session_id": "governed-test",
                            "content": "Use a canary deployment wave.",
                        },
                    },
                )
                self.assertTrue(committed["result"]["structuredContent"]["recorded"])
                flushed = request(
                    10,
                    "tools/call",
                    {
                        "name": "library_context_flush",
                        "arguments": {
                            "session_id": "governed-test",
                            "timeout_seconds": 3,
                        },
                    },
                )
                self.assertTrue(flushed["result"]["structuredContent"]["flushed"])

                pong = request(11, "ping")
                self.assertEqual(pong["result"], {})
                request(12, "shutdown")
                send({"jsonrpc": "2.0", "method": "exit"}, expect=False)
                assert process.stdin is not None
                process.stdin.close()
                process.wait(timeout=5)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                reader.join(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
