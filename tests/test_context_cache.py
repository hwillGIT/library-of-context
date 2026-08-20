from __future__ import annotations

import json
import os
import socketserver
import tempfile
import threading
import time
import unittest
import urllib.request

from context_cache import (
    ContextCache,
    ContextSwapper,
    HashingEmbedder,
    LibraryOfContext,
    ReadingDesk,
)
from context_cache.models import ContextRecord, SearchHit, WorkingSet
from context_cache.redis_hot import RedisHotCache
from context_cache.server import create_server
from library_of_context import LibraryOfContext as PublicLibraryOfContext


class _FakeRedisHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        values = self.server.values  # type: ignore[attr-defined]
        commands = self.server.commands  # type: ignore[attr-defined]
        while True:
            first = self.rfile.readline()
            if not first:
                return
            count = int(first[1:-2])
            parts: list[bytes] = []
            for _ in range(count):
                length = int(self.rfile.readline()[1:-2])
                parts.append(self.rfile.read(length))
                self.rfile.read(2)
            command = parts[0].upper()
            commands.append(command.decode("ascii"))
            if command == b"PING":
                self.wfile.write(b"+PONG\r\n")
            elif command in {b"AUTH", b"SELECT"}:
                self.wfile.write(b"+OK\r\n")
            elif command == b"SET":
                values[parts[1]] = parts[2]
                self.wfile.write(b"+OK\r\n")
            elif command == b"GET":
                value = values.get(parts[1])
                if value is None:
                    self.wfile.write(b"$-1\r\n")
                else:
                    self.wfile.write(f"${len(value)}\r\n".encode() + value + b"\r\n")
            elif command == b"EXPIRE":
                self.wfile.write(b":1\r\n")
            elif command == b"INCR":
                value = int(values.get(parts[1], b"0")) + 1
                values[parts[1]] = str(value).encode()
                self.wfile.write(f":{value}\r\n".encode())
            elif command == b"DBSIZE":
                self.wfile.write(f":{len(values)}\r\n".encode())
            elif command == b"DEL":
                removed = int(values.pop(parts[1], None) is not None)
                self.wfile.write(f":{removed}\r\n".encode())
            else:
                self.wfile.write(b"-ERR unsupported command\r\n")


class _FakeRedisServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FakeRedisHandler)
        self.values: dict[bytes, bytes] = {}
        self.commands: list[str] = []


class ContextCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.directory.name, "cache.sqlite")
        self.cache = ContextCache(self.db, redis_url="", ram_bytes=32 * 1024)

    def tearDown(self) -> None:
        self.cache.close()
        self.directory.cleanup()

    def test_hashing_embedder_is_deterministic_and_normalized(self) -> None:
        embedder = HashingEmbedder(64)
        first, second = embedder.embed(["alpha beta", "alpha beta"])
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=6)

    def test_disk_recovers_after_ram_is_cleared(self) -> None:
        record = self.cache.put("durable decision", record_id="decision-1")
        self.cache.ram.clear()
        recovered = self.cache.get(record.id)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.text, "durable decision")
        self.assertGreaterEqual(self.cache.ram.stats()["items"], 1)

    def test_hybrid_retrieval_and_metadata_filter(self) -> None:
        self.cache.put(
            "Python asyncio coordinates concurrent tasks with an event loop.",
            record_id="python",
            metadata={"project": "runtime"},
            importance=0.8,
        )
        self.cache.put(
            "Tomatoes grow best with sunlight and consistent watering.",
            record_id="garden",
            metadata={"project": "garden"},
        )
        hits = self.cache.retrieve("Python concurrent event loop", top_k=2)
        self.assertEqual(hits[0].record.id, "python")
        filtered = self.cache.retrieve(
            "sunlight", top_k=5, filters={"project": "garden"}
        )
        self.assertEqual([hit.record.id for hit in filtered], ["garden"])

    def test_query_cache_invalidates_on_write(self) -> None:
        self.cache.put("first alpha note", record_id="one")
        before = self.cache.retrieve("alpha", top_k=10)
        self.cache.put("second alpha note", record_id="two")
        after = self.cache.retrieve("alpha", top_k=10)
        self.assertEqual(len(before), 1)
        self.assertEqual({hit.record.id for hit in after}, {"one", "two"})

    def test_ttl_and_purge(self) -> None:
        self.cache.put("short lived", record_id="ttl", ttl_seconds=0.01)
        time.sleep(0.03)
        self.assertIsNone(self.cache.get("ttl"))
        self.assertEqual(self.cache.stats()["sqlite"]["records"], 0)

    def test_replace_source_and_chunking(self) -> None:
        long_text = " ".join(f"word{index}" for index in range(400))
        first = self.cache.ingest(
            long_text,
            source="notes",
            chunk_tokens=100,
            overlap_tokens=10,
        )
        second = self.cache.ingest(
            "replacement text",
            source="notes",
            replace_source=True,
        )
        self.assertGreater(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(self.cache.stats()["sqlite"]["records"], 1)

    def test_working_set_respects_budget_and_pin(self) -> None:
        pinned = self.cache.put("A pinned architectural constraint.", record_id="pin")
        self.cache.put(
            "Redis accelerates hot reads and cached query results.", record_id="redis"
        )
        swapper = ContextSwapper(self.cache)
        working = swapper.refresh(
            "test",
            "Redis cache architecture",
            token_budget=80,
            pinned_record_ids=[pinned.id],
        )
        self.assertLessEqual(working.token_count, working.token_budget)
        self.assertEqual(working.hits[0].record.id, "pin")
        self.assertIs(swapper.get("test"), working)
        swapper.close()

    def test_periodic_refresh_starts_updates_and_stops(self) -> None:
        self.cache.put(
            "Periodic refresh follows the active focus.", record_id="periodic"
        )
        swapper = ContextSwapper(self.cache)
        initial = swapper.start_periodic(
            "periodic-session",
            "active focus",
            interval_seconds=0.02,
            token_budget=100,
        )
        time.sleep(0.06)
        latest = swapper.get("periodic-session")
        self.assertIsNotNone(latest)
        self.assertGreater(latest.refreshed_at, initial.refreshed_at)
        self.assertTrue(swapper.status()[0]["alive"])
        self.assertTrue(swapper.stop_periodic("periodic-session"))
        swapper.close()

    def test_refresh_reports_virtual_memory_swap_delta(self) -> None:
        self.cache.put("Alpha protocol and only alpha details.", record_id="alpha")
        self.cache.put("Beta protocol and only beta details.", record_id="beta")
        desk = ContextSwapper(self.cache)
        first = desk.refresh("delta", "alpha", top_k=1, token_budget=100)
        second = desk.refresh("delta", "beta", top_k=1, token_budget=100)
        self.assertEqual(first.swapped_in, ["alpha"])
        self.assertEqual(first.swapped_out, [])
        self.assertEqual(second.swapped_in, ["beta"])
        self.assertEqual(second.swapped_out, ["alpha"])
        self.assertEqual(second.retained, [])
        desk.close()

    def test_library_metaphor_facade(self) -> None:
        self.cache.close()
        library = LibraryOfContext(self.db, redis_url="")
        self.cache = library
        book = library.shelve(
            "A reading desk is bounded virtual context.", book_id="book-1"
        )
        self.assertEqual(library.consult("bounded reading desk")[0].record.id, book.id)
        desk = library.open_reading_desk()
        self.assertIsInstance(desk, ReadingDesk)
        working = desk.lay_out("virtual context", token_budget=100)
        self.assertIn("book-1", working.swapped_in)
        desk.close()

    def test_public_package_exports_library_class(self) -> None:
        self.assertIs(PublicLibraryOfContext, LibraryOfContext)

    def test_local_http_api(self) -> None:
        server, swapper = create_server(self.cache, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path: str, body: dict[str, object]) -> dict[str, object]:
            request = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                return json.loads(response.read())

        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as response:
                self.assertTrue(json.loads(response.read())["ok"])
            created = post(
                "/books",
                {"id": "api", "text": "HTTP requests can refresh working context."},
            )
            self.assertEqual(created["id"], "api")
            queried = post(
                "/catalog/query", {"query": "HTTP working context", "top_k": 2}
            )
            self.assertEqual(queried["hits"][0]["record"]["id"], "api")
            refreshed = post(
                "/desk/refresh",
                {
                    "session_id": "api-session",
                    "focus": "HTTP context",
                    "token_budget": 100,
                },
            )
            self.assertEqual(refreshed["session_id"], "api-session")
        finally:
            server.shutdown()
            server.server_close()
            swapper.close()
            thread.join(timeout=2)


class RedisProtocolTests(unittest.TestCase):
    def test_hot_cache_round_trips_all_payload_types(self) -> None:
        server = _FakeRedisServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        hot = RedisHotCache(
            f"redis://127.0.0.1:{server.server_address[1]}/0", required=True
        )
        now = time.time()
        record = ContextRecord(
            id="record",
            namespace="test",
            text="cached text",
            embedding=[0.25, 0.75],
            token_count=3,
            created_at=now,
            updated_at=now,
            accessed_at=now,
        )
        hit = SearchHit(record, 0.9, 0.8, 1.0, 0.5, 1.0)
        working = WorkingSet(
            "session", "test", "cached", [hit], "cached text", 3, 100, now
        )
        try:
            hot.put_record(record)
            self.assertEqual(hot.get_record("test", "record").text, "cached text")
            self.assertEqual(hot.bump_generation("test"), 1)
            self.assertEqual(hot.generation("test"), 1)
            hot.put_query("test", "query-key", [hit])
            self.assertEqual(hot.get_query("test", "query-key")[0].record.id, "record")
            hot.put_working_set(working)
            self.assertEqual(hot.get_working_set("test", "session").focus, "cached")
            self.assertTrue(hot.stats()["enabled"])
            self.assertIn("SET", server.commands)
            self.assertIn("GET", server.commands)
        finally:
            hot.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


@unittest.skipUnless(
    os.environ.get("CONTEXT_CACHE_TEST_REDIS") == "1", "Redis integration not requested"
)
class RedisIntegrationTests(unittest.TestCase):
    def test_local_redis_tier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = ContextCache(
                os.path.join(directory, "cache.sqlite"),
                redis_url=os.environ.get(
                    "CONTEXT_CACHE_REDIS_URL", "redis://127.0.0.1:6379/0"
                ),
                redis_required=True,
            )
            try:
                record = cache.put("redis integration", record_id="redis-test")
                cache.ram.clear()
                recovered = cache.get(record.id)
                self.assertEqual(recovered.text, record.text)
                self.assertTrue(cache.stats()["redis"]["enabled"])
            finally:
                cache.delete("redis-test")
                cache.close()


if __name__ == "__main__":
    unittest.main()
