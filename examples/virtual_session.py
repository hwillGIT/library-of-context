import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library_of_context import LibraryOfContext

with TemporaryDirectory() as directory:
    with LibraryOfContext(f"{directory}/library.sqlite", redis_url="") as library:
        session = library.open_virtual_session(
            "example", token_budget=700, recent_token_budget=220
        )
        for index in range(30):
            session.record("user", f"Question {index} about ordinary bookkeeping.")
            session.record(
                "assistant", f"Answer {index} with ordinary bookkeeping details."
            )
        session.record(
            "assistant",
            "The deployment uses a canary wave before the main production rollout.",
            importance=0.9,
        )

        envelope = session.build_prompt(
            user_message="What was the canary deployment decision?",
            system_prompt="Answer from the Library when it contains relevant context.",
        )
        print(
            f"Stored books: {envelope.history_books}; "
            f"live prompt: {envelope.token_count}/{envelope.token_budget} tokens"
        )
        print(envelope.messages[0]["content"])
        session.close()
