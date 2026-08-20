from __future__ import annotations

import argparse
import json
import os
from typing import Any, Protocol


class _SubparserFactory(Protocol):
    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


def json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def _command(
    subparsers: _SubparserFactory,
    name: str,
    *,
    handler: str,
    aliases: list[str] | None = None,
    help_text: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, aliases=aliases or [], help=help_text)
    parser.set_defaults(command_handler=handler)
    return parser


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=os.environ.get(
            "LIBRARY_OF_CONTEXT_DB",
            os.environ.get("CONTEXT_CACHE_DB", "data/library-of-context.sqlite"),
        ),
        help="SQLite backing-store path",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get(
            "LIBRARY_OF_CONTEXT_REDIS_URL",
            os.environ.get("CONTEXT_CACHE_REDIS_URL", "redis://127.0.0.1:6379/0"),
        ),
        help="local Redis URL",
    )
    parser.add_argument(
        "--no-redis", action="store_true", help="disable the Redis tier"
    )
    parser.add_argument(
        "--redis-required", action="store_true", help="fail if Redis is unavailable"
    )
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--ram-mb", type=int, default=256)
    parser.add_argument("--embedder", choices=["hashing", "ollama"], default="hashing")
    parser.add_argument("--ollama-model", default="nomic-embed-text")


def _add_status_commands(commands: _SubparserFactory) -> None:
    _command(
        commands,
        "quickstart",
        handler="quickstart",
        help_text="run a disposable context-governor self-test (no Redis or saved data)",
    )
    _command(
        commands,
        "doctor",
        handler="doctor",
        help_text="check the disk, RAM, Redis, and embedding tiers",
    )
    _command(commands, "stats", handler="stats", help_text="show cache statistics")
    _command(
        commands,
        "purge",
        handler="purge",
        help_text="remove expired disk records",
    )


def _add_shelving_commands(commands: _SubparserFactory) -> None:
    shelve = _command(
        commands,
        "shelve",
        aliases=["put"],
        handler="shelve",
        help_text="shelve one context book/chunk",
    )
    shelve.add_argument("text")
    shelve.add_argument("--id")
    shelve.add_argument("--source", default="cli")
    shelve.add_argument("--metadata", type=json_object, default={})
    shelve.add_argument("--importance", type=float, default=0.5)
    shelve.add_argument("--ttl", type=float)

    ingest = _command(
        commands,
        "shelve-document",
        aliases=["ingest"],
        handler="shelve-document",
        help_text="chunk and shelve a text file or stdin",
    )
    ingest.add_argument("path", help="path or - for stdin")
    ingest.add_argument("--source")
    ingest.add_argument("--metadata", type=json_object, default={})
    ingest.add_argument("--importance", type=float, default=0.5)
    ingest.add_argument("--chunk-tokens", type=int, default=450)
    ingest.add_argument("--overlap-tokens", type=int, default=60)
    ingest.add_argument("--replace", action="store_true")


def _add_retrieval_commands(commands: _SubparserFactory) -> None:
    consult = _command(
        commands,
        "consult",
        aliases=["query"],
        handler="consult",
        help_text="consult the hybrid catalog",
    )
    consult.add_argument("query")
    consult.add_argument("--top-k", type=int, default=8)
    consult.add_argument("--filters", type=json_object)
    consult.add_argument("--minimum-score", type=float, default=0.0)
    consult.add_argument("--json", action="store_true")


def _add_desk_commands(commands: _SubparserFactory) -> None:
    desk = _command(
        commands,
        "desk",
        aliases=["context"],
        handler="desk",
        help_text="replace the reading desk with a token-bounded relevant working set",
    )
    desk.add_argument("focus")
    desk.add_argument("--session", default="cli")
    desk.add_argument("--budget", type=int, default=4000)
    desk.add_argument("--top-k", type=int, default=12)
    desk.add_argument("--filters", type=json_object)
    desk.add_argument("--pin", action="append", default=[])
    desk.add_argument("--json", action="store_true")

    watch = _command(
        commands,
        "watch-desk",
        aliases=["watch"],
        handler="watch-desk",
        help_text="periodically replace the reading desk as relevance changes",
    )
    watch.add_argument("focus")
    watch.add_argument("--session", default="cli")
    watch.add_argument("--budget", type=int, default=4000)
    watch.add_argument("--top-k", type=int, default=12)
    watch.add_argument("--interval", type=float, default=30.0)
    watch.add_argument("--filters", type=json_object)
    watch.add_argument("--pin", action="append", default=[])


def _add_maintenance_commands(commands: _SubparserFactory) -> None:
    discard = _command(
        commands,
        "discard",
        aliases=["delete"],
        handler="discard",
        help_text="permanently remove one book/chunk",
    )
    discard.add_argument("record_id")

    serve = _command(
        commands,
        "serve",
        handler="serve",
        help_text="run the local HTTP service",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="library-of-context",
        description=(
            "Virtual context memory: retrieve books from a durable library onto a "
            "bounded reading desk"
        ),
    )
    _add_runtime_options(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    _add_status_commands(commands)
    _add_shelving_commands(commands)
    _add_retrieval_commands(commands)
    _add_desk_commands(commands)
    _add_maintenance_commands(commands)
    return parser
