from __future__ import annotations

import concurrent.futures
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from context_cache.http_app import LibraryHTTPApplication
from context_cache.server import create_server
from context_cache.swapper import ContextSwapper
from library_of_context import LibraryOfContext


class HTTPApplicationRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.library = LibraryOfContext(
            Path(self.directory.name) / "library.sqlite", redis_url=""
        )
        self.swapper = ContextSwapper(self.library)
        self.application = LibraryHTTPApplication(self.library, self.swapper)

    def tearDown(self) -> None:
        self.application.close()
        self.swapper.close()
        self.library.close()
        self.directory.cleanup()

    def dispatch(
        self, method: str, target: str, body: dict[str, object] | None = None
    ) -> tuple[int, object]:
        response = self.application.dispatch(method, target, body)
        return response.status, response.body

    def test_library_and_desk_aliases_return_expected_statuses(self) -> None:
        status, health = self.dispatch("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(self.dispatch("GET", "/stats")[0], 200)

        for index, path in enumerate(("/books", "/records")):
            status, record = self.dispatch(
                "POST",
                path,
                {"id": f"book-{index}", "text": f"HTTP route book {index}."},
            )
            self.assertEqual(status, 201)
            self.assertEqual(record["id"], f"book-{index}")

        for index, path in enumerate(("/library/ingest", "/ingest")):
            status, ingested = self.dispatch(
                "POST",
                path,
                {"text": f"Ingested route document {index}.", "source": path},
            )
            self.assertEqual(status, 201)
            self.assertEqual(ingested["count"], 1)

        for path in ("/catalog/query", "/query"):
            status, result = self.dispatch(
                "POST", path, {"query": "HTTP route book", "top_k": 2}
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["hits"])

        for index, path in enumerate(("/desk/refresh", "/context/refresh")):
            session_id = f"desk-{index}"
            status, working = self.dispatch(
                "POST",
                path,
                {
                    "session_id": session_id,
                    "focus": "HTTP route book",
                    "token_budget": 100,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(working["session_id"], session_id)
            get_path = "/desk/" if index == 0 else "/context/"
            self.assertEqual(self.dispatch("GET", get_path + session_id)[0], 200)

        for index, path in enumerate(("/desk/watch", "/context/watch")):
            session_id = f"watch-{index}"
            status, _ = self.dispatch(
                "POST",
                path,
                {
                    "session_id": session_id,
                    "focus": "HTTP route book",
                    "token_budget": 100,
                    "interval_seconds": 60,
                },
            )
            self.assertEqual(status, 200)
            stop_path = "/context/watch/" if index == 0 else "/desk/watch/"
            status, stopped = self.dispatch("DELETE", stop_path + session_id)
            self.assertEqual(status, 200)
            self.assertTrue(stopped["stopped"])

        for path, record_id in (("/books/", "book-0"), ("/records/", "book-1")):
            status, deleted = self.dispatch("DELETE", path + record_id)
            self.assertEqual(status, 200)
            self.assertTrue(deleted["deleted"])

        self.assertEqual(self.dispatch("GET", "/desk/missing")[0], 404)
        self.assertEqual(self.dispatch("GET", "/not-a-route")[0], 404)
        self.assertEqual(self.dispatch("POST", "/not-a-route", {})[0], 404)
        self.assertEqual(self.dispatch("DELETE", "/not-a-route")[0], 404)

    def test_governor_endpoint_families_support_the_same_operations(self) -> None:
        for prefix in ("governor", "context"):
            session_id = f"{prefix}-session"
            settings: dict[str, object] = {
                "session_id": session_id,
                "token_budget": 500,
                "recent_token_budget": 120,
                "protected_token_budget": 80,
            }
            status, protected = self.dispatch(
                "POST",
                f"/{prefix}/protect",
                {**settings, "content": "Keep the canary policy."},
            )
            self.assertEqual(status, 201)

            status, prepared = self.dispatch(
                "POST",
                f"/{prefix}/prepare",
                {**settings, "user_message": "What is the policy?"},
            )
            self.assertEqual(status, 200)
            self.assertTrue(prepared["replaces_compaction"])

            status, committed = self.dispatch(
                "POST",
                f"/{prefix}/commit",
                {**settings, "content": "Use a canary."},
            )
            self.assertEqual(status, 201)
            self.assertTrue(committed["recorded"])

            status, released = self.dispatch(
                "POST",
                f"/{prefix}/release",
                {**settings, "event_id": protected["event"]["event_id"]},
            )
            self.assertEqual(status, 200)
            self.assertTrue(released["released"])

            status, flushed = self.dispatch(
                "POST", f"/{prefix}/flush", {**settings, "timeout_seconds": 3}
            )
            self.assertEqual(status, 200)
            self.assertTrue(flushed["flushed"])

            status, current = self.dispatch(
                "GET", f"/{prefix}/status/{session_id}?collection=default"
            )
            self.assertEqual(status, 200)
            self.assertEqual(current["session_id"], session_id)


class HTTPTransportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.library = LibraryOfContext(
            Path(self.directory.name) / "library.sqlite", redis_url=""
        )
        self.server, self.swapper = create_server(self.library, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.swapper.close()
        self.library.close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | list[object] | bytes | None = None,
    ) -> tuple[int, object, dict[str, str]]:
        if isinstance(body, bytes):
            payload = body
        elif body is None:
            payload = None
        else:
            payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return (
                response.status,
                json.loads(response.read()),
                dict(response.headers.items()),
            )

    def test_handler_returns_standard_errors_and_serves_concurrently(
        self,
    ) -> None:
        self.assertIsInstance(self.server, ThreadingHTTPServer)
        self.assertTrue(self.server.daemon_threads)

        status, body, headers = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")

        self.assertEqual(self.request("POST", "/books", b"not-json")[0], 400)
        self.assertEqual(self.request("POST", "/books", ["not", "object"])[0], 400)
        self.assertEqual(self.request("POST", "/books", {})[0], 400)
        self.assertEqual(self.request("GET", "/not-a-route")[0], 404)
        with mock.patch.object(
            self.library, "stats", side_effect=RuntimeError("forced failure")
        ):
            status, body, _ = self.request("GET", "/stats")
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "forced failure"})

        def create_record(index: int) -> int:
            status, _, _ = self.request(
                "POST",
                "/records",
                {"id": f"concurrent-{index}", "text": f"Concurrent record {index}"},
            )
            return status

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            statuses = list(executor.map(create_record, range(8)))
        self.assertEqual(statuses, [201] * 8)


if __name__ == "__main__":
    unittest.main()
