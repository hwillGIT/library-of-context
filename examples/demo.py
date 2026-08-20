import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context_cache import LibraryOfContext

with TemporaryDirectory() as directory:
    with LibraryOfContext(f"{directory}/library.sqlite", redis_url="") as library:
        library.shelve(
            "SQLite is the durable source of truth. Redis is a disposable hot tier.",
            source="architecture",
            importance=0.9,
        )
        library.shelve(
            "The librarian periodically rebuilds a token-bounded reading desk.",
            source="architecture",
            importance=0.8,
        )

        desk = library.open_reading_desk()
        working = desk.lay_out(
            "How does context persistence and swapping work?",
            session_id="demo",
            token_budget=300,
        )
        print(working.context)
        print("Swapped in:", working.swapped_in)
        desk.close()
