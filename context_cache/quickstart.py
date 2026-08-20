from __future__ import annotations

import tempfile
from pathlib import Path

from .embeddings import HashingEmbedder
from .engine import ContextCache


def run_quickstart() -> int:
    """Run a disposable prepare, commit, and index cycle."""

    with tempfile.TemporaryDirectory(prefix="library-context-quickstart-") as directory:
        database = Path(directory) / "quickstart.sqlite"
        with ContextCache(database, redis_url="", embedder=HashingEmbedder()) as cache:
            with cache.open_context_governor(
                "quickstart-agent",
                token_budget=700,
                recent_token_budget=180,
                protected_token_budget=100,
                worker_poll_seconds=0.01,
            ) as context:
                context.protect(
                    "A production rollout must begin with a canary wave.",
                    label="sample-policy",
                    event_id="quickstart-policy",
                )
                request = context.prepare(
                    "Which production rollout policy applies?",
                    system_prompt="Answer using the governed context.",
                    event_id="quickstart-user",
                )
                context.commit(
                    "Begin production with a canary wave.",
                    event_id="quickstart-assistant",
                )
                if not context.flush(timeout=5):
                    raise RuntimeError("the disposable index did not become current")

                watermarks = context.status()["watermarks"]
                if request.token_count > request.token_budget:
                    raise RuntimeError("the governed prompt exceeded its token budget")
                if watermarks["recorded_through"] != 3:
                    raise RuntimeError("the disposable event log is incomplete")
                if watermarks["indexed_through"] != 3:
                    raise RuntimeError("the disposable search index is incomplete")

                print("Library of Context quickstart: PASS")
                print(
                    "  bounded model input: "
                    f"{request.token_count}/{request.token_budget} estimated tokens"
                )
                print("  durable events recorded: 3")
                print("  events indexed and searchable: 3")
                print("  Redis, Docker, and cloud services used: no")
                print("  test data retained: no")
                print("Next: open docs/ADD_TO_YOUR_AGENT.md to connect an agent.")
    return 0
