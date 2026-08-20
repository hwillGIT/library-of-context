from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .cli_config import create_cache
from .engine import ContextCache
from .models import SearchHit
from .quickstart import run_quickstart
from .server import run_server
from .swapper import ContextSwapper

CommandHandler = Callable[[argparse.Namespace, ContextCache], None]


def _print_hits(hits: list[SearchHit]) -> None:
    for rank, hit in enumerate(hits, 1):
        print(
            f"{rank}. score={hit.score:.3f} id={hit.record.id} "
            f"source={hit.record.source}"
        )
        print(f"   {hit.record.text[:300].replace(chr(10), ' ')}")


def _doctor(_args: argparse.Namespace, cache: ContextCache) -> None:
    stats = cache.stats()
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


def _stats(_args: argparse.Namespace, cache: ContextCache) -> None:
    print(json.dumps(cache.stats(), indent=2, ensure_ascii=False))


def _purge(_args: argparse.Namespace, cache: ContextCache) -> None:
    print(json.dumps({"purged": cache.purge_expired()}))


def _shelve(args: argparse.Namespace, cache: ContextCache) -> None:
    record = cache.put(
        args.text,
        record_id=args.id,
        metadata=args.metadata,
        source=args.source,
        importance=args.importance,
        ttl_seconds=args.ttl,
    )
    print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))


def _shelve_document(args: argparse.Namespace, cache: ContextCache) -> None:
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


def _consult(args: argparse.Namespace, cache: ContextCache) -> None:
    hits = cache.retrieve(
        args.query,
        top_k=args.top_k,
        filters=args.filters,
        minimum_score=args.minimum_score,
    )
    if args.json:
        print(json.dumps([hit.to_dict() for hit in hits], indent=2, ensure_ascii=False))
    else:
        _print_hits(hits)


def _desk(args: argparse.Namespace, cache: ContextCache) -> None:
    swapper = ContextSwapper(cache)
    try:
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
    finally:
        swapper.close()


def _watch_desk(args: argparse.Namespace, cache: ContextCache) -> None:
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


def _discard(args: argparse.Namespace, cache: ContextCache) -> None:
    print(json.dumps({"deleted": cache.delete(args.record_id)}))


def _serve(args: argparse.Namespace, cache: ContextCache) -> None:
    run_server(cache, args.host, args.port)


COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "doctor": _doctor,
    "stats": _stats,
    "purge": _purge,
    "shelve": _shelve,
    "shelve-document": _shelve_document,
    "consult": _consult,
    "desk": _desk,
    "watch-desk": _watch_desk,
    "discard": _discard,
    "serve": _serve,
}


def execute_command(args: argparse.Namespace) -> int:
    command = args.command_handler
    if command == "quickstart":
        return run_quickstart()

    handler = COMMAND_HANDLERS[command]
    cache = create_cache(args)
    try:
        handler(args, cache)
        return 0
    finally:
        cache.close()
