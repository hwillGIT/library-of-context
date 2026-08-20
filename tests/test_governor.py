from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from context_cache.server import create_server
from library_of_context import LibraryOfContext


class ContextGovernorTests(unittest.TestCase):
    def test_prepare_is_durable_before_index_and_prompt_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.sqlite"
            with LibraryOfContext(path, redis_url="") as library:
                governor = library.open_context_governor(
                    "durable",
                    token_budget=320,
                    recent_token_budget=120,
                    protected_token_budget=64,
                    start_worker=False,
                )
                full_message = "A" * 4000
                envelope = governor.prepare(
                    full_message,
                    system_prompt="Keep the current request visible.",
                    event_id="turn-1",
                )
                stored = library.store.get_thread_event("default", "turn-1")
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored.content, full_message)
                self.assertLessEqual(envelope.token_count, envelope.token_budget)
                self.assertEqual(envelope.watermarks.recorded_through, 1)
                self.assertEqual(envelope.watermarks.indexed_through, 0)
                self.assertEqual(envelope.watermarks.pending_events, 1)
                self.assertIn("turn-1", envelope.recent_event_ids)
                self.assertIn("full event remains", envelope.messages[-1]["content"])
                governor.close()

            with LibraryOfContext(path, redis_url="") as recovered:
                governor = recovered.open_context_governor(
                    "durable",
                    token_budget=320,
                    recent_token_budget=120,
                    protected_token_budget=64,
                )
                self.assertTrue(governor.flush(timeout=3))
                watermarks = governor.status()["watermarks"]
                self.assertEqual(watermarks["recorded_through"], 1)
                self.assertEqual(watermarks["indexed_through"], 1)
                self.assertEqual(watermarks["pending_events"], 0)
                event = recovered.store.get_thread_event("default", "turn-1")
                assert event is not None
                self.assertIsNotNone(recovered.get(event.record_id))
                governor.close()

    def test_protected_context_survives_recent_ring_eviction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                governor = library.open_context_governor(
                    "protected",
                    token_budget=500,
                    recent_token_budget=120,
                    protected_token_budget=120,
                    recent_ring_events=4,
                    start_worker=False,
                )
                decision = governor.protect(
                    "Production deployment must always use a canary wave.",
                    label="deployment-rule",
                    event_id="protected-decision",
                )
                for index in range(20):
                    governor.record("assistant", f"Routine completed step {index}.")
                envelope = governor.build_prompt(focus="What is the deployment rule?")
                self.assertIn(decision.event_id, envelope.protected_event_ids)
                self.assertTrue(
                    any(
                        "canary wave" in message["content"]
                        for message in envelope.messages
                    )
                )
                self.assertGreater(envelope.paged_out_events, 0)
                self.assertTrue(governor.release(decision.event_id))
                rebuilt = governor.build_prompt(focus="Routine status")
                self.assertNotIn(decision.event_id, rebuilt.protected_event_ids)
                governor.close()

    def test_bounded_work_ring_spills_losslessly_to_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.sqlite"
            with LibraryOfContext(path, redis_url="") as library:
                governor = library.open_context_governor(
                    "overflow",
                    token_budget=500,
                    recent_token_budget=120,
                    protected_token_budget=80,
                    work_ring_capacity=1,
                    start_worker=False,
                )
                for index in range(8):
                    governor.record(
                        "user",
                        f"Durable ring overflow event {index}.",
                        event_id=f"e-{index}",
                    )
                status = governor.status()
                self.assertEqual(status["work_ring"]["queued"], 1)
                self.assertEqual(status["watermarks"]["pending_events"], 8)
                governor.close()

            with LibraryOfContext(path, redis_url="") as recovered:
                governor = recovered.open_context_governor(
                    "overflow",
                    token_budget=500,
                    recent_token_budget=120,
                    protected_token_budget=80,
                    work_ring_capacity=2,
                )
                self.assertTrue(governor.flush(timeout=3))
                status = governor.status()
                self.assertEqual(status["watermarks"]["indexed_through"], 8)
                self.assertEqual(status["watermarks"]["pending_events"], 0)
                governor.close()

    def test_event_id_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                governor = library.open_context_governor(
                    "idempotent", start_worker=False
                )
                first = governor.record("user", "Same turn.", event_id="same")
                second = governor.record("user", "Same turn.", event_id="same")
                self.assertEqual(first.sequence, second.sequence)
                self.assertEqual(
                    library.store.count_thread_events("default", "idempotent"), 1
                )
                with self.assertRaises(ValueError):
                    governor.record("user", "Different turn.", event_id="same")
                governor.close()


class GovernorHTTPTests(unittest.TestCase):
    def test_prepare_commit_flush_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(Path(directory) / "library.sqlite", redis_url="")
            server, swapper = create_server(library, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def post(path: str, body: dict[str, object]) -> tuple[int, dict]:
                request = urllib.request.Request(
                    base + path,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    return response.status, json.loads(response.read())

            try:
                _, prepared = post(
                    "/context/prepare",
                    {
                        "session_id": "http-governed",
                        "user_message": "Remember this turn.",
                        "token_budget": 500,
                        "recent_token_budget": 120,
                        "protected_token_budget": 80,
                    },
                )
                self.assertTrue(prepared["replaces_compaction"])
                self.assertLessEqual(prepared["token_count"], prepared["token_budget"])
                status_code, committed = post(
                    "/context/commit",
                    {
                        "session_id": "http-governed",
                        "content": "The response is durable.",
                    },
                )
                self.assertEqual(status_code, 201)
                self.assertTrue(committed["recorded"])
                _, flushed = post(
                    "/context/flush",
                    {"session_id": "http-governed", "timeout_seconds": 3},
                )
                self.assertTrue(flushed["flushed"])
                with urllib.request.urlopen(
                    base + "/context/status/http-governed", timeout=3
                ) as response:
                    status = json.loads(response.read())
                self.assertEqual(status["watermarks"]["recorded_through"], 2)
                self.assertEqual(status["watermarks"]["indexed_through"], 2)
            finally:
                server.shutdown()
                server.server_close()
                swapper.close()
                library.close()
                thread.join(timeout=2)

    def test_status_respects_a_non_default_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(Path(directory) / "library.sqlite", redis_url="")
            server, swapper = create_server(library, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            request = urllib.request.Request(
                base + "/context/prepare",
                data=json.dumps(
                    {
                        "session_id": "isolated",
                        "collection": "project-a",
                        "user_message": "Keep this inside project A.",
                        "token_budget": 500,
                        "recent_token_budget": 120,
                        "protected_token_budget": 80,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                with urllib.request.urlopen(
                    base + "/context/status/isolated?collection=project-a", timeout=3
                ) as response:
                    status = json.loads(response.read())
                self.assertEqual(status["collection"], "project-a")
                self.assertEqual(status["watermarks"]["recorded_through"], 1)
            finally:
                server.shutdown()
                server.server_close()
                swapper.close()
                library.close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
