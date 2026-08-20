from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from context_cache.library import LibraryOfContext
from context_cache.mcp_schema import SERVER_INSTRUCTIONS as SCHEMA_INSTRUCTIONS
from context_cache.mcp_schema import TOOLS as SCHEMA_TOOLS
from context_cache.mcp_server import SERVER_INSTRUCTIONS, TOOLS
from context_cache.models import ContextEvent
from context_cache.resource_registry import GovernorRegistry
from context_cache.rings import RecentEventRing, UniqueWorkQueue
from context_cache.text_budget import select_event_tail, truncate_text


def event(event_id: str, sequence: int, content: str, tokens: int) -> ContextEvent:
    return ContextEvent(
        event_id=event_id,
        namespace="project",
        session_id="thread",
        sequence=sequence,
        role="user",
        content=content,
        token_count=tokens,
    )


class QueueAndBudgetTests(unittest.TestCase):
    def test_work_queue_suppresses_pending_duplicates_and_reports_overflow(
        self,
    ) -> None:
        work: UniqueWorkQueue[str] = UniqueWorkQueue(capacity=1)

        self.assertTrue(work.offer("a"))
        self.assertTrue(work.offer("a"))
        self.assertFalse(work.offer("b"))
        self.assertEqual(work.stats(), {"queued": 1, "capacity": 1, "occupancy": 1.0})

        item = work.get(timeout=0.01)
        self.assertEqual(item, "a")
        work.complete(item)
        self.assertTrue(work.offer("b"))

    def test_recent_ring_is_ordered_bounded_and_idempotent(self) -> None:
        recent = RecentEventRing(max_events=2, max_tokens=64)
        recent.append(event("a", 1, "first", 4))
        recent.append(event("a", 1, "first", 4))
        recent.append(event("b", 2, "second", 4))
        recent.append(event("c", 3, "third", 4))

        self.assertEqual([item.event_id for item in recent.snapshot()], ["b", "c"])
        self.assertEqual(recent.stats()["events"], 2)

    def test_event_selection_keeps_order_and_marks_truncation(self) -> None:
        events = [
            event("a", 1, "old", 1),
            event("b", 2, "middle", 2),
            event("c", 3, "x" * 400, 100),
        ]

        selected, used = select_event_tail(events, 20, excluded={"b"})

        self.assertEqual([item.event_id for item, _ in selected], ["c"])
        self.assertLessEqual(used, 20)
        self.assertIn("full event remains", selected[0][1])
        self.assertEqual(truncate_text("abc", 1, marker=" [more]"), "abc")


class RegistryAndExportContractTests(unittest.TestCase):
    def test_server_module_reexports_mcp_schema_constants(self) -> None:
        self.assertIs(SERVER_INSTRUCTIONS, SCHEMA_INSTRUCTIONS)
        self.assertIs(TOOLS, SCHEMA_TOOLS)

    def test_concurrent_governor_lookup_reuses_one_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                os.path.join(directory, "library.sqlite"),
                redis_url="",
            )
            registry = GovernorRegistry(library, default_session_id="thread")
            settings = {
                "collection": "project",
                "session_id": "thread",
                "token_budget": 512,
                "recent_token_budget": 128,
                "protected_token_budget": 64,
            }
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    governors = list(
                        pool.map(lambda _: registry.get(settings), range(8))
                    )
                self.assertEqual(len({id(item) for item in governors}), 1)
            finally:
                registry.close()
                library.close()

    def test_failed_reconfiguration_leaves_registered_governor_usable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                os.path.join(directory, "library.sqlite"),
                redis_url="",
            )
            registry = GovernorRegistry(library, default_session_id="thread")
            settings = {
                "collection": "project",
                "session_id": "thread",
                "token_budget": 512,
                "recent_token_budget": 128,
                "protected_token_budget": 64,
            }
            try:
                governor = registry.get(settings)
                with self.assertRaises(ValueError):
                    registry.get({**settings, "token_budget": 128})
                self.assertIs(registry.get(settings), governor)
                governor.prepare("The governor accepts a turn.")
            finally:
                registry.close()
                library.close()


if __name__ == "__main__":
    unittest.main()
