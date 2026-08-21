from __future__ import annotations

import argparse

from .embeddings import HashingEmbedder, OllamaEmbedder
from .library import LibraryOfContext


def create_cache(args: argparse.Namespace) -> LibraryOfContext:
    embedder = (
        OllamaEmbedder(model=args.ollama_model)
        if args.embedder == "ollama"
        else HashingEmbedder()
    )
    return LibraryOfContext(
        args.db,
        namespace=args.namespace,
        ram_bytes=args.ram_mb * 1024 * 1024,
        redis_url="" if args.no_redis else args.redis_url,
        redis_required=args.redis_required,
        embedder=embedder,
        exclusive_database_owner=(getattr(args, "command_handler", None) == "serve"),
    )
