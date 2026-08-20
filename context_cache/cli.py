from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .embeddings import HashingEmbedder, OllamaEmbedder
from .engine import ContextCache
from .server import run_server
from .swapper import ContextSwapper


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="library-of-context",
        description="Virtual context memory: retrieve books from a durable library onto a bounded reading desk",
    )
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

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "doctor", help="check the disk, RAM, Redis, and embedding tiers"
    )
    commands.add_parser("stats", help="show cache statistics")
    commands.add_parser("purge", help="remove expired disk records")

    put = commands.add_parser(
        "shelve", aliases=["put"], help="shelve one context book/chunk"
    )
    put.add_argument("text")
    put.add_argument("--id")
    put.add_argument("--source", default="cli")
    put.add_argument("--metadata", type=_json_object, default={})
    put.add_argument("--importance", type=float, default=0.5)
    put.add_argument("--ttl", type=float)

    ingest = commands.add_parser(
        "shelve-document",
        aliases=["ingest"],
        help="chunk and shelve a text file or stdin",
    )
    ingest.add_argument("path", help="path or - for stdin")
    ingest.add_argument("--source")
    ingest.add_argument("--metadata", type=_json_object, default={})
    ingest.add_argument("--importance", type=float, default=0.5)
    ingest.add_argument("--chunk-tokens", type=int, default=450)
    ingest.add_argument("--overlap-tokens", type=int, default=60)
    ingest.add_argument("--replace", action="store_true")

    query = commands.add_parser(
        "consult", aliases=["query"], help="consult the hybrid catalog"
    )
    query.add_argument("query")
    query.add_argument("--top-k", type=int, default=8)
    query.add_argument("--filters", type=_json_object)
    query.add_argument("--minimum-score", type=float, default=0.0)
    query.add_argument("--json", action="store_true")

    desk = commands.add_parser(
        "desk",
        aliases=["context"],
        help="replace the reading desk with a token-bounded relevant working set",
    )
    desk.add_argument("focus")
    desk.add_argument("--session", default="cli")
    desk.add_argument("--budget", type=int, default=4000)
    desk.add_argument("--top-k", type=int, default=12)
    desk.add_argument("--filters", type=_json_object)
    desk.add_argument("--pin", action="append", default=[])
    desk.add_argument("--json", action="store_true")

    watch = commands.add_parser(
        "watch-desk",
        aliases=["watch"],
        help="periodically replace the reading desk as relevance changes",
    )
    watch.add_argument("focus")
    watch.add_argument("--session", default="cli")
    watch.add_argument("--budget", type=int, default=4000)
    watch.add_argument("--top-k", type=int, default=12)
    watch.add_argument("--interval", type=float, default=30.0)
    watch.add_argument("--filters", type=_json_object)
    watch.add_argument("--pin", action="append", default=[])

    delete = commands.add_parser(
        "discard", aliases=["delete"], help="permanently remove one book/chunk"
    )
    delete.add_argument("record_id")

    serve = commands.add_parser("serve", help="run the local HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def _cache(args: argparse.Namespace) -> ContextCache:
    if args.embedder == "ollama":
        embedder = OllamaEmbedder(model=args.ollama_model)
    else:
        embedder = HashingEmbedder()
    return ContextCache(
        args.db,
        namespace=args.namespace,
        ram_bytes=args.ram_mb * 1024 * 1024,
        redis_url="" if args.no_redis else args.redis_url,
        redis_required=args.redis_required,
        embedder=embedder,
    )


def _print_hits(hits: list[Any]) -> None:
    for rank, hit in enumerate(hits, 1):
        print(
            f"{rank}. score={hit.score:.3f} id={hit.record.id} "
            f"source={hit.record.source}"
        )
        print(f"   {hit.record.text[:300].replace(chr(10), ' ')}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cache = _cache(args)
    try:
        if args.command in {"doctor", "stats"}:
            stats = cache.stats()
            if args.command == "doctor":
                stats["status"] = {
                    "sqlite": "ok",
                    "ram": "ok",
                    "redis": (
                        "disabled"
                        if stats["redis"] is None
                        else "ok"
                        if stats["redis"]["enabled"]
                        else "unavailable (fallback active)"
                    ),
                }
            print(json.dumps(stats, indent=2, ensure_ascii=False))
        elif args.command == "purge":
            print(json.dumps({"purged": cache.purge_expired()}))
        elif args.command in {"shelve", "put"}:
            record = cache.put(
                args.text,
                record_id=args.id,
                metadata=args.metadata,
                source=args.source,
                importance=args.importance,
                ttl_seconds=args.ttl,
            )
            print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
        elif args.command in {"shelve-document", "ingest"}:
            if args.path == "-":
                text = sys.stdin.read()
                source = args.source or "stdin"
            else:
                path = Path(args.path)
                text = path.read_text(encoding="utf-8")
                source = args.source or str(path.resolve())
            records = cache.ingest(
                text,
                source=source,
                metadata=args.metadata,
                importance=args.importance,
                chunk_tokens=args.chunk_tokens,
                overlap_tokens=args.overlap_tokens,
                replace_source=args.replace,
            )
            print(json.dumps({"ingested": len(records), "source": source}))
        elif args.command in {"consult", "query"}:
            hits = cache.retrieve(
                args.query,
                top_k=args.top_k,
                filters=args.filters,
                minimum_score=args.minimum_score,
            )
            if args.json:
                print(
                    json.dumps(
                        [hit.to_dict() for hit in hits], indent=2, ensure_ascii=False
                    )
                )
            else:
                _print_hits(hits)
        elif args.command in {"desk", "context"}:
            swapper = ContextSwapper(cache)
            working = swapper.refresh(
                args.session,
                args.focus,
                token_budget=args.budget,
                top_k=args.top_k,
                filters=args.filters,
                pinned_record_ids=args.pin,
            )
            print(
                json.dumps(working.to_dict(), indent=2, ensure_ascii=False)
                if args.json
                else working.context
            )
            swapper.close()
        elif args.command in {"watch-desk", "watch"}:
            swapper = ContextSwapper(cache)
            print("Refreshing the reading desk; press Ctrl+C to stop.")
            try:
                while True:
                    working = swapper.refresh(
                        args.session,
                        args.focus,
                        token_budget=args.budget,
                        top_k=args.top_k,
                        filters=args.filters,
                        pinned_record_ids=args.pin,
                    )
                    print(
                        f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"({working.token_count}/{working.token_budget} tokens) ---\n"
                    )
                    print(working.context)
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                pass
            finally:
                swapper.close()
        elif args.command in {"discard", "delete"}:
            print(json.dumps({"deleted": cache.delete(args.record_id)}))
        elif args.command == "serve":
            run_server(cache, args.host, args.port)
        return 0
    finally:
        cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
