from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from library_of_context import LibraryOfContext


class ProtectedContextPagingTests(unittest.TestCase):
    def test_every_protected_event_remains_eligible_beyond_one_store_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                governor = library.open_context_governor(
                    "protected-paging",
                    token_budget=5_000,
                    recent_token_budget=64,
                    protected_token_budget=3_000,
                    start_worker=False,
                )
                event_ids = []
                for index in range(300):
                    event_id = f"protected-{index:03d}"
                    event_ids.append(event_id)
                    governor.protect(
                        f"P{index:03d}",
                        event_id=event_id,
                    )

                envelope = governor.build_prompt(focus="protected paging contract")

                self.assertEqual(set(envelope.protected_event_ids), set(event_ids))
                self.assertLessEqual(envelope.token_count, envelope.token_budget)
                self.assertTrue(governor.release(event_ids[0]))

                released = governor.build_prompt(focus="protected paging contract")
                self.assertNotIn(event_ids[0], released.protected_event_ids)
                self.assertEqual(
                    set(released.protected_event_ids),
                    set(event_ids[1:]),
                )
                self.assertLessEqual(released.token_count, released.token_budget)
                governor.close()


if __name__ == "__main__":
    unittest.main()
