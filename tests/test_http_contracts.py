from __future__ import annotations

import concurrent.futures
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from context_cache.http_app import LibraryHTTPApplication
from context_cache.scopes import ContextScope
from context_cache.server import ServerDrainTimeout, create_server, run_server
from context_cache.swapper import ContextSwapper
from library_of_context import LibraryOfContext, RuntimeSettings


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

    def test_stats_selects_the_requested_collection(self) -> None:
        status, _ = self.dispatch(
            "POST",
            "/records",
            {"id": "default-book", "text": "Default collection book."},
        )
        self.assertEqual(status, 201)
        status, _ = self.dispatch(
            "POST",
            "/records",
            {
                "id": "project-book",
                "text": "Project collection book.",
                "namespace": "project-b",
            },
        )
        self.assertEqual(status, 201)

        status, project_stats = self.dispatch("GET", "/stats?collection=project-b")
        self.assertEqual(status, 200)
        self.assertEqual(project_stats["namespace"], "project-b")
        self.assertEqual(project_stats["sqlite"]["records"], 1)

        status, default_stats = self.dispatch("GET", "/stats")
        self.assertEqual(status, 200)
        self.assertEqual(default_stats["namespace"], "default")
        self.assertEqual(default_stats["sqlite"]["records"], 1)

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

    def test_desk_status_uses_the_requested_collection(self) -> None:
        status, working = self.dispatch(
            "POST",
            "/desk/refresh",
            {
                "session_id": "shared-name",
                "focus": "project context",
                "namespace": "separate",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(working["namespace"], "separate")

        separate_status, separate = self.dispatch(
            "GET",
            "/desk/shared-name?collection=separate",
        )
        default_status, _ = self.dispatch("GET", "/desk/shared-name")

        self.assertEqual(separate_status, 200)
        self.assertEqual(separate["namespace"], "separate")
        self.assertEqual(default_status, 404)

        watch_status, _ = self.dispatch(
            "POST",
            "/desk/watch",
            {
                "session_id": "shared-name",
                "focus": "project context",
                "namespace": "separate",
                "interval_seconds": 60,
            },
        )
        default_stop, default_stopped = self.dispatch(
            "DELETE", "/desk/watch/shared-name"
        )
        separate_stop, stopped = self.dispatch(
            "DELETE",
            "/desk/watch/shared-name?collection=separate",
        )

        self.assertEqual(watch_status, 200)
        self.assertEqual(default_stop, 200)
        self.assertFalse(default_stopped["stopped"])
        self.assertEqual(separate_stop, 200)
        self.assertTrue(stopped["stopped"])

    def test_record_delete_is_scoped_to_the_requested_collection(self) -> None:
        for namespace, text in (("default", "default text"), ("project-b", "B text")):
            status, _ = self.dispatch(
                "POST",
                "/records",
                {"id": "shared-id", "text": text, "namespace": namespace},
            )
            self.assertEqual(status, 201)

        status, result = self.dispatch(
            "DELETE",
            "/records/shared-id?collection=project-b",
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["deleted"])
        self.assertIsNotNone(self.library.get("shared-id", namespace="default"))
        self.assertIsNone(self.library.get("shared-id", namespace="project-b"))

        self.dispatch(
            "POST",
            "/records",
            {"id": "shared-id", "text": "B text", "namespace": "project-b"},
        )
        status, result = self.dispatch(
            "DELETE",
            "/books/shared-id?namespace=project-b",
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["deleted"])
        self.assertIsNotNone(self.library.get("shared-id", namespace="default"))

        with self.assertRaisesRegex(ValueError, "same catalog"):
            self.dispatch(
                "DELETE",
                "/records/shared-id?collection=default&namespace=project-b",
            )

    def test_http_scope_routes_apply_to_search_and_pinned_desks(self) -> None:
        records = {}
        for record_id, scope, owner, team in (
            ("http-project", ContextScope.PROJECT, None, None),
            ("http-alpha", ContextScope.THREAD, "alpha", None),
            ("http-beta", ContextScope.THREAD, "beta", None),
            ("http-atlas", ContextScope.TEAM, None, "atlas"),
        ):
            status, record = self.dispatch(
                "POST",
                "/records",
                {
                    "id": record_id,
                    "text": f"HTTP scope matrix phrase {record_id}.",
                    "scope": scope.value,
                    "owner_session_id": owner,
                    "team_id": team,
                },
            )
            self.assertEqual(status, 201)
            records[record_id] = record

        status, result = self.dispatch(
            "POST",
            "/query",
            {
                "query": "HTTP scope matrix phrase",
                "top_k": 20,
                "scopes": ["thread", "project", "team"],
                "session_id": "alpha",
                "team_ids": ["atlas"],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            {hit["record"]["id"] for hit in result["hits"]},
            {"http-project", "http-alpha", "http-atlas"},
        )

        status, working = self.dispatch(
            "POST",
            "/desk/refresh",
            {
                "session_id": "beta",
                "focus": "HTTP scope matrix phrase",
                "top_k": 20,
                "team_ids": ["other"],
                "pinned_record_ids": list(records),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            {hit["record"]["id"] for hit in working["hits"]},
            {"http-project", "http-beta"},
        )

        status, denied = self.dispatch(
            "DELETE",
            "/records/http-alpha?scope=thread&session_id=beta",
        )
        self.assertEqual(status, 200)
        self.assertFalse(denied["deleted"])
        self.assertIsNotNone(
            self.library.get(
                "http-alpha",
                scopes=(ContextScope.THREAD,),
                session_id="alpha",
            )
        )
        status, allowed = self.dispatch(
            "DELETE",
            "/records/http-alpha?scope=thread&session_id=alpha",
        )
        self.assertEqual(status, 200)
        self.assertTrue(allowed["deleted"])


class HTTPTransportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.library = LibraryOfContext(
            Path(self.directory.name) / "library.sqlite", redis_url=""
        )
        self.server, self.swapper = create_server(
            self.library,
            "127.0.0.1",
            0,
            auth_token="http-contract-token-0000000000000001",
        )
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
        *,
        authorize: bool = True,
        content_type: str | None = "application/json",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, object, dict[str, str]]:
        if isinstance(body, bytes):
            payload = body
        elif body is None:
            payload = None
        else:
            payload = json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if authorize:
            request_headers["Authorization"] = f"Bearer {self.server.auth_token}"
        if content_type is not None:
            request_headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=request_headers,
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
        self.assertEqual(
            self.request("POST", "/query", {"query": "bounded", "top_k": 101})[0],
            400,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/desk/refresh",
                {
                    "session_id": "bounded",
                    "focus": "bounded",
                    "token_budget": 128_001,
                },
            )[0],
            400,
        )
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

    def test_transport_rejects_unauthorized_browser_and_non_json_requests(
        self,
    ) -> None:
        self.assertEqual(
            self.request("GET", "/health", authorize=False)[0],
            401,
        )
        self.assertEqual(
            self.request(
                "GET",
                "/health",
                headers={"Authorization": f"Bearer {'x' * 32}"},
                authorize=False,
            )[0],
            401,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/records",
                {"id": "browser", "text": "must not be stored"},
                headers={"Origin": "https://attacker.example"},
            )[0],
            403,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/records",
                {"id": "plain", "text": "must not be stored"},
                content_type="text/plain",
            )[0],
            415,
        )
        self.assertEqual(
            self.request(
                "GET",
                "/health",
                headers={"Host": "attacker.example"},
            )[0],
            403,
        )
        self.assertIsNone(self.library.get("browser"))
        self.assertIsNone(self.library.get("plain"))


class HTTPShutdownContractTests(unittest.TestCase):
    def test_active_request_timeout_preserves_application_and_storage(self) -> None:
        started = threading.Event()
        release = threading.Event()
        request_errors: list[Exception] = []
        settings = RuntimeSettings(http_shutdown_timeout_seconds=0.05)
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                runtime_settings=settings,
            )
            server, _swapper = create_server(
                library,
                "127.0.0.1",
                0,
                auth_token="http-shutdown-token-000000000000001",
            )
            server_thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.01),
                daemon=True,
            )
            original_stats = library.stats

            def blocked_stats(*args: object, **kwargs: object) -> object:
                started.set()
                release.wait()
                return original_stats(*args, **kwargs)

            def request() -> None:
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(
                            f"http://127.0.0.1:{server.server_address[1]}/stats",
                            headers={"Authorization": f"Bearer {server.auth_token}"},
                        ),
                        timeout=3,
                    ) as response:
                        response.read()
                except Exception as exc:
                    request_errors.append(exc)

            server_thread.start()
            request_thread = threading.Thread(target=request, daemon=True)
            try:
                with (
                    mock.patch.object(library, "stats", side_effect=blocked_stats),
                    mock.patch.object(
                        server.application,
                        "close",
                        wraps=server.application.close,
                    ) as application_close,
                ):
                    request_thread.start()
                    self.assertTrue(started.wait(2))
                    server.shutdown()
                    before = time.monotonic()
                    self.assertFalse(server.server_close())
                    self.assertLess(time.monotonic() - before, 0.5)
                    application_close.assert_not_called()
                    self.assertGreaterEqual(
                        library.store.stats("default")["records"],
                        0,
                    )

                    release.set()
                    request_thread.join(2)
                    self.assertFalse(request_thread.is_alive())
                    self.assertTrue(server.server_close())
                    application_close.assert_called_once_with()
            finally:
                release.set()
                request_thread.join(2)
                server.shutdown()
                server.server_close()
                library.close()
                server_thread.join(2)
            self.assertEqual(request_errors, [])

    def test_run_server_reports_incomplete_drain_without_closing_swapper(self) -> None:
        server = mock.Mock()
        server.server_close.return_value = False
        swapper = mock.Mock()
        cache = mock.Mock()
        with mock.patch(
            "context_cache.server.create_server",
            return_value=(server, swapper),
        ):
            with self.assertRaises(ServerDrainTimeout):
                run_server(cache, auth_token="x" * 32)
        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()
        swapper.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
