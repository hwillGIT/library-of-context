from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from context_cache.http_app import LibraryHTTPApplication
from context_cache.mcp_schema import TOOLS
from context_cache.mcp_service import LibraryMCPTools
from context_cache.scopes import ContextScope, ThreadKey
from library_of_context import LibraryOfContext

STATEFUL_TOOLS = {
    "library_desk_refresh",
    "library_desk_get",
    "library_desk_watch",
    "library_desk_stop",
    "library_message_record",
    "library_prompt_build",
    "library_context_prepare",
    "library_context_commit",
    "library_context_protect",
    "library_context_release",
    "library_context_status",
    "library_context_flush",
}


class IdentityContractTests(unittest.TestCase):
    def test_stateful_mcp_tools_require_session_id(self) -> None:
        tools = {tool["name"]: tool for tool in TOOLS}
        for name in STATEFUL_TOOLS:
            schema = tools[name]["inputSchema"]
            self.assertIn("session_id", schema["required"], name)
            self.assertNotIn("default", schema["properties"]["session_id"], name)

    def test_stateful_services_reject_missing_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            tools = LibraryMCPTools(library)
            try:
                result = tools.call(
                    "library_desk_refresh",
                    {"subject": "missing identity"},
                )
                self.assertTrue(result["isError"])
                self.assertIn("session_id", result["content"][0]["text"])

                application = LibraryHTTPApplication(
                    library,
                    library.runtime.swapper,
                )
                try:
                    with self.assertRaisesRegex(ValueError, "session_id is required"):
                        application.dispatch(
                            "POST",
                            "/context/prepare",
                            {"user_message": "missing identity"},
                        )
                finally:
                    application.close()
            finally:
                tools.close()

    def test_thread_key_rejects_ambiguous_identifiers(self) -> None:
        for session_id in ("", "   ", "thread\nother", "x" * 513):
            with self.subTest(session_id=repr(session_id)):
                with self.assertRaises(ValueError):
                    ThreadKey("project", session_id)

    def test_catalog_names_cannot_collide_in_cache_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.shelve(
                    "First catalog record.",
                    book_id="b\x1fc",
                    collection="a",
                )
                with self.assertRaises(ValueError):
                    library.shelve(
                        "Second catalog record.",
                        book_id="c",
                        collection="a\x1fb",
                    )
                with self.assertRaises(ValueError):
                    library.get("c", namespace="a\x1fb")

    def test_python_retrieval_enforces_the_result_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                with self.assertRaisesRegex(ValueError, "top_k cannot exceed"):
                    library.retrieve("bounded result", top_k=101)


class ScopeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.library = LibraryOfContext(
            Path(self.directory.name) / "library.sqlite",
            redis_url="",
        )

    def tearDown(self) -> None:
        self.library.close()
        self.directory.cleanup()

    def test_thread_records_are_isolated_and_project_records_are_shared(self) -> None:
        project = self.library.shelve(
            "Project deployment policy uses canary waves.",
            book_id="project-policy",
        )
        alpha = self.library.open_context_governor("thread-alpha")
        beta = self.library.open_context_governor("thread-beta")
        alpha_event = alpha.record(
            "user",
            "Alpha private phrase is cobalt-lantern.",
            event_id="alpha-private",
        )
        beta.record(
            "user",
            "Beta private phrase is amber-orchid.",
            event_id="beta-private",
        )
        self.assertTrue(alpha.flush(timeout=3))
        self.assertTrue(beta.flush(timeout=3))

        alpha_hits = self.library.retrieve(
            "cobalt-lantern",
            scopes=(ContextScope.THREAD, ContextScope.PROJECT),
            session_id="thread-alpha",
        )
        beta_hits = self.library.retrieve(
            "cobalt-lantern",
            scopes=(ContextScope.THREAD, ContextScope.PROJECT),
            session_id="thread-beta",
        )
        self.assertIn(alpha_event.record_id, [hit.record.id for hit in alpha_hits])
        self.assertNotIn(alpha_event.record_id, [hit.record.id for hit in beta_hits])
        self.assertIn(project.id, [hit.record.id for hit in alpha_hits])
        self.assertIn(project.id, [hit.record.id for hit in beta_hits])

        project_hits = self.library.consult("cobalt-lantern")
        self.assertNotIn(alpha_event.record_id, [hit.record.id for hit in project_hits])
        self.assertIsNone(
            self.library.get(
                alpha_event.record_id,
                scopes=(ContextScope.THREAD, ContextScope.PROJECT),
                session_id="thread-beta",
            )
        )

        self.library.ram.clear()
        with patch.object(
            self.library.store,
            "_from_row",
            wraps=self.library.store._from_row,
        ) as hydrate:
            self.assertIsNone(
                self.library.get(
                    alpha_event.record_id,
                    scopes=(ContextScope.THREAD, ContextScope.PROJECT),
                    session_id="thread-beta",
                )
            )
        hydrate.assert_not_called()

        pinned = self.library.runtime.swapper.refresh(
            "thread-beta",
            "unrelated subject",
            namespace="default",
            pinned_record_ids=[alpha_event.record_id],
        )
        self.assertNotIn(alpha_event.record_id, [hit.record.id for hit in pinned.hits])

    def test_team_scope_requires_an_authorized_team_route(self) -> None:
        record = self.library.shelve(
            "Team Atlas operates the blue deployment lane.",
            book_id="atlas-policy",
            scope=ContextScope.TEAM,
            team_id="atlas",
        )
        self.assertNotIn(
            record.id,
            [hit.record.id for hit in self.library.consult("blue deployment lane")],
        )
        self.assertIn(
            record.id,
            [
                hit.record.id
                for hit in self.library.consult(
                    "blue deployment lane",
                    team_ids=("atlas",),
                )
            ],
        )
        self.assertNotIn(
            record.id,
            [
                hit.record.id
                for hit in self.library.consult(
                    "blue deployment lane",
                    team_ids=("other",),
                )
            ],
        )

    def test_promotion_copies_context_and_retains_private_source(self) -> None:
        governor = self.library.open_context_governor("source-thread")
        event = governor.record(
            "assistant",
            "Approved reusable finding: batch embeddings by token count.",
            event_id="finding",
        )
        self.assertTrue(governor.flush(timeout=3))

        with self.assertRaises(PermissionError):
            self.library.promote_book(
                event.record_id,
                target_scope=ContextScope.PROJECT,
                source_session_id="other-thread",
            )
        promoted = self.library.promote_book(
            event.record_id,
            target_scope=ContextScope.PROJECT,
            source_session_id="source-thread",
        )
        source = self.library.store.get("default", event.record_id)
        assert source is not None
        self.assertEqual(source.scope, ContextScope.THREAD)
        self.assertEqual(source.owner_session_id, "source-thread")
        self.assertEqual(promoted.scope, ContextScope.PROJECT)
        self.assertEqual(
            promoted.metadata["promoted_from_record_id"],
            event.record_id,
        )
        self.assertEqual(
            promoted.metadata["promoted_from_owner_session_id"],
            "source-thread",
        )
        self.assertIn(
            promoted.id,
            [hit.record.id for hit in self.library.consult("batch embeddings")],
        )
        self.assertTrue(self.library.delete(promoted.id))
        self.assertIsNone(self.library.get(promoted.id))
        retained_source = self.library.get(
            event.record_id,
            scopes=(ContextScope.THREAD,),
            session_id="source-thread",
        )
        self.assertIsNotNone(retained_source)

    def test_record_delete_requires_a_matching_visibility_route(self) -> None:
        private = self.library.shelve(
            "Private deletion contract.",
            book_id="delete-private",
            scope=ContextScope.THREAD,
            owner_session_id="owner",
        )
        team = self.library.shelve(
            "Team deletion contract.",
            book_id="delete-team",
            scope=ContextScope.TEAM,
            team_id="atlas",
        )
        self.assertFalse(self.library.delete(private.id))
        self.assertFalse(
            self.library.delete(
                private.id,
                scopes=(ContextScope.THREAD,),
                session_id="other",
            )
        )
        self.assertTrue(
            self.library.delete(
                private.id,
                scopes=(ContextScope.THREAD,),
                session_id="owner",
            )
        )
        self.assertFalse(
            self.library.delete(
                team.id,
                scopes=(ContextScope.TEAM,),
                team_ids=("other",),
            )
        )
        self.assertTrue(
            self.library.delete(
                team.id,
                scopes=(ContextScope.TEAM,),
                team_ids=("atlas",),
            )
        )

    def test_upsert_cannot_change_a_record_visibility_boundary(self) -> None:
        private = self.library.shelve(
            "Private thread fact.",
            book_id="fixed-boundary",
            scope=ContextScope.THREAD,
            owner_session_id="owner-thread",
        )

        with self.assertRaisesRegex(ValueError, "visibility cannot be changed"):
            self.library.shelve(
                "Exposed project fact.",
                book_id=private.id,
                scope=ContextScope.PROJECT,
            )

        stored = self.library.store.get("default", private.id)
        assert stored is not None
        self.assertEqual(stored.scope, ContextScope.THREAD)
        self.assertEqual(stored.owner_session_id, "owner-thread")
        self.assertEqual(stored.text, "Private thread fact.")

    def test_virtual_session_filters_source_labels_and_serializes_writes(self) -> None:
        session = self.library.open_virtual_session("isolated-thread")
        self.library.shelve(
            "Project record with a copied conversation source.",
            book_id="source-label-collision",
            source="conversation:isolated-thread",
        )

        with ThreadPoolExecutor(max_workers=8) as executor:
            records = list(
                executor.map(
                    lambda index: session.record("user", f"Thread message {index}"),
                    range(20),
                )
            )

        history = session.history()
        self.assertEqual(len(history), 20)
        self.assertNotIn("source-label-collision", [record.id for record in history])
        self.assertEqual(
            sorted(int(record.metadata["turn"]) for record in records),
            list(range(1, 21)),
        )

    def test_same_event_id_is_valid_in_distinct_threads(self) -> None:
        first = self.library.open_context_governor("first", start_worker=False)
        second = self.library.open_context_governor("second", start_worker=False)
        self.assertEqual(first.record("user", "First.", event_id="turn-1").sequence, 1)
        self.assertEqual(
            second.record("user", "Second.", event_id="turn-1").sequence,
            1,
        )

    def test_mcp_statistics_do_not_expose_another_collection(self) -> None:
        tools = LibraryMCPTools(self.library, close_library=False)
        try:
            watched = tools.call(
                "library_desk_watch",
                {
                    "session_id": "private-thread-b",
                    "subject": "private-focus-b",
                    "collection": "project-b",
                    "interval_seconds": 60,
                },
            )
            self.assertFalse(watched["isError"])
            protected = tools.call(
                "library_context_protect",
                {
                    "session_id": "private-thread-b",
                    "content": "private policy B",
                    "collection": "project-b",
                },
            )
            self.assertFalse(protected["isError"])

            response = tools.call("library_stats", {"collection": "project-a"})
            self.assertFalse(response["isError"])
            stats = response["structuredContent"]
            self.assertEqual(stats["periodic_desks"], [])
            self.assertEqual(stats["runtime"]["periodic_desks"], [])
            self.assertEqual(stats["runtime"]["thread_states"]["active"], 0)
            self.assertEqual(stats["governor_registry"]["active"], 0)
            serialized = json.dumps(stats)
            self.assertNotIn("private-thread-b", serialized)
            self.assertNotIn("private-focus-b", serialized)
        finally:
            tools.call(
                "library_desk_stop",
                {
                    "session_id": "private-thread-b",
                    "collection": "project-b",
                },
            )
            tools.close()

    def test_mcp_scope_routes_apply_to_search_and_pinned_desks(self) -> None:
        project = self.library.shelve(
            "Scope matrix phrase in project context.",
            book_id="matrix-project",
        )
        private = self.library.shelve(
            "Scope matrix phrase in alpha context.",
            book_id="matrix-alpha",
            scope=ContextScope.THREAD,
            owner_session_id="alpha",
        )
        other = self.library.shelve(
            "Scope matrix phrase in beta context.",
            book_id="matrix-beta",
            scope=ContextScope.THREAD,
            owner_session_id="beta",
        )
        team = self.library.shelve(
            "Scope matrix phrase in Atlas context.",
            book_id="matrix-atlas",
            scope=ContextScope.TEAM,
            team_id="atlas",
        )
        tools = LibraryMCPTools(self.library, close_library=False)
        try:
            unauthorized = tools.call(
                "library_consult",
                {"subject": "scope matrix phrase", "max_books": 20},
            )["structuredContent"]["hits"]
            authorized = tools.call(
                "library_consult",
                {
                    "subject": "scope matrix phrase",
                    "max_books": 20,
                    "team_ids": ["atlas"],
                },
            )["structuredContent"]["hits"]
            self.assertEqual(
                {hit["book"]["id"] for hit in unauthorized},
                {project.id},
            )
            self.assertEqual(
                {hit["book"]["id"] for hit in authorized},
                {project.id, team.id},
            )

            alpha = tools.call(
                "library_desk_refresh",
                {
                    "session_id": "alpha",
                    "subject": "scope matrix phrase",
                    "max_books": 20,
                    "team_ids": ["atlas"],
                    "keep_open": [private.id, other.id, team.id, project.id],
                },
            )["structuredContent"]
            alpha_ids = {book["book"]["id"] for book in alpha["books"]}
            self.assertEqual(alpha_ids, {private.id, project.id, team.id})
            self.assertNotIn(other.id, alpha_ids)

            beta = tools.call(
                "library_desk_refresh",
                {
                    "session_id": "beta",
                    "subject": "scope matrix phrase",
                    "max_books": 20,
                    "keep_open": [private.id, other.id, team.id, project.id],
                },
            )["structuredContent"]
            beta_ids = {book["book"]["id"] for book in beta["books"]}
            self.assertEqual(beta_ids, {other.id, project.id})
        finally:
            tools.close()


if __name__ == "__main__":
    unittest.main()
