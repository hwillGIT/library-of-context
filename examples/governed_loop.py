"""Provider-neutral context-governor lifecycle example."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library_of_context import LibraryOfContext

with TemporaryDirectory() as directory:
    with LibraryOfContext(f"{directory}/library.sqlite", redis_url="") as library:
        with library.open_context_governor(
            "example-agent",
            token_budget=700,
            recent_token_budget=180,
            protected_token_budget=100,
        ) as context:
            context.protect(
                "A production rollout must begin with a canary wave.",
                label="deployment-policy",
            )

            request = context.prepare(
                "Which production rollout policy applies?",
                system_prompt="Answer using the governed context.",
            )
            print(
                f"Model input: {request.token_count}/{request.token_budget} tokens; "
                f"paged out: {request.paged_out_events} events"
            )
            for message in request.messages:
                print(f"[{message['role']}] {message['content']}")

            # The model call receives only request.messages; commit stores its result.
            context.commit("Begin production with a canary wave.")
            context.flush(timeout=3)
            print(context.status()["watermarks"])
