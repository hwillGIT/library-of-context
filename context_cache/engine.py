from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .embeddings import Embedder, HashingEmbedder, estimate_tokens
from .models import ContextRecord, SearchHit
from .ram import ByteLRU, record_size
from .redis_hot import RedisHotCache
from .retrieval import RANKER_VERSION, RetrievalPolicy, rank_records
from .store import SQLiteStore

if TYPE_CHECKING:
    from .governor import LibraryContextGovernor


def chunk_text(text: str, max_tokens: int = 450, overlap_tokens: int = 60) -> list[str]:
    """Split text into approximate-token windows with overlap."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be >= 0 and < max_tokens")
    words = text.split()
    if not words:
        return []
    # Use 1.3 tokens per whitespace-delimited word as a coarse packing heuristic.
    max_words = max(1, int(max_tokens / 1.3))
    overlap_words = int(overlap_tokens / 1.3)
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + max_words)
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = max(start + 1, end - overlap_words)
    return chunks


class ContextCache:
    """Three-tier context store with hybrid lexical and vector retrieval.

    L1 is an in-process byte-bounded LRU, L2 is optional local Redis, and L3 is
    durable SQLite. SQLite is always authoritative.
    """

    def __init__(
        self,
        sqlite_path: str | Path = "data/library-of-context.sqlite",
        *,
        namespace: str = "default",
        ram_bytes: int = 256 * 1024 * 1024,
        query_cache_bytes: int = 32 * 1024 * 1024,
        redis_url: str | None = None,
        redis_required: bool = False,
        embedder: Embedder | None = None,
    ) -> None:
        self.namespace = namespace
        self.store = SQLiteStore(sqlite_path)
        self.embedder = embedder or HashingEmbedder()
        self.ram = ByteLRU[ContextRecord](ram_bytes)
        self.query_ram = ByteLRU[list[SearchHit]](query_cache_bytes)
        self._lock = threading.RLock()
        self._local_generation: dict[str, int] = {}
        resolved_redis_url = redis_url
        if resolved_redis_url is None:
            resolved_redis_url = os.environ.get(
                "LIBRARY_OF_CONTEXT_REDIS_URL",
                os.environ.get("CONTEXT_CACHE_REDIS_URL", "redis://127.0.0.1:6379/0"),
            )
        self.redis = (
            None
            if resolved_redis_url == ""
            else RedisHotCache(resolved_redis_url, required=redis_required)
        )

    @staticmethod
    def _ram_key(namespace: str, record_id: str) -> str:
        return f"{namespace}\x1f{record_id}"

    def _namespace(self, namespace: str | None) -> str:
        return self.namespace if namespace is None else namespace

    def _generation(self, namespace: str) -> int:
        local = self._local_generation.get(namespace, 0)
        remote = 0 if self.redis is None else self.redis.generation(namespace)
        return max(local, remote)

    def _invalidate(self, namespace: str) -> None:
        with self._lock:
            self._local_generation[namespace] = self._generation(namespace) + 1
            if self.redis is not None:
                remote = self.redis.bump_generation(namespace)
                self._local_generation[namespace] = max(
                    self._local_generation[namespace], remote
                )

    def put(
        self,
        text: str,
        *,
        record_id: str | None = None,
        namespace: str | None = None,
        metadata: dict[str, Any] | None = None,
        source: str = "manual",
        importance: float = 0.5,
        ttl_seconds: float | None = None,
    ) -> ContextRecord:
        if not text.strip():
            raise ValueError("text cannot be empty")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        ns = self._namespace(namespace)
        record_id = record_id or uuid.uuid4().hex
        existing = self.store.get(ns, record_id)
        now = time.time()
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        embedding = self.embedder.embed([text])[0]
        record = ContextRecord(
            id=record_id,
            namespace=ns,
            text=text,
            embedding=embedding,
            metadata=dict(metadata or {}),
            source=source,
            importance=importance,
            token_count=estimate_tokens(text),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            accessed_at=now,
            expires_at=None if ttl_seconds is None else now + max(0.0, ttl_seconds),
            content_hash=content_hash,
        )
        self.store.upsert(record)
        self.ram.put(
            self._ram_key(ns, record_id),
            record,
            size=record_size(record),
            ttl_seconds=ttl_seconds,
        )
        if self.redis is not None:
            self.redis.put_record(record)
        self._invalidate(ns)
        return record

    def ingest(
        self,
        text: str,
        *,
        source: str,
        namespace: str | None = None,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        chunk_tokens: int = 450,
        overlap_tokens: int = 60,
        replace_source: bool = False,
    ) -> list[ContextRecord]:
        ns = self._namespace(namespace)
        if replace_source:
            self.delete_source(source, namespace=ns)
        records: list[ContextRecord] = []
        for index, chunk in enumerate(chunk_text(text, chunk_tokens, overlap_tokens)):
            digest = hashlib.sha256(
                f"{ns}\x00{source}\x00{index}\x00{chunk}".encode("utf-8")
            ).hexdigest()[:24]
            chunk_metadata = dict(metadata or {})
            chunk_metadata.update({"chunk_index": index, "source": source})
            records.append(
                self.put(
                    chunk,
                    record_id=digest,
                    namespace=ns,
                    metadata=chunk_metadata,
                    source=source,
                    importance=importance,
                )
            )
        return records

    def get(
        self, record_id: str, *, namespace: str | None = None
    ) -> ContextRecord | None:
        ns = self._namespace(namespace)
        key = self._ram_key(ns, record_id)
        record = self.ram.get(key)
        now = time.time()
        if record is not None:
            if record.expires_at is None or record.expires_at > now:
                return record
            self.ram.delete(key)
        if self.redis is not None:
            record = self.redis.get_record(ns, record_id)
            if record is not None and (
                record.expires_at is None or record.expires_at > now
            ):
                self.ram.put(key, record, size=record_size(record))
                return record
        record = self.store.get(ns, record_id)
        if record is not None:
            ttl = (
                None if record.expires_at is None else max(0.0, record.expires_at - now)
            )
            self.ram.put(key, record, size=record_size(record), ttl_seconds=ttl)
            if self.redis is not None:
                self.redis.put_record(record)
        return record

    def delete(self, record_id: str, *, namespace: str | None = None) -> bool:
        ns = self._namespace(namespace)
        deleted = self.store.delete(ns, record_id)
        self.ram.delete(self._ram_key(ns, record_id))
        if self.redis is not None:
            self.redis.delete_record(ns, record_id)
        if deleted:
            self._invalidate(ns)
        return deleted

    def delete_source(self, source: str, *, namespace: str | None = None) -> int:
        ns = self._namespace(namespace)
        ids = self.store.delete_source(ns, source)
        for record_id in ids:
            self.ram.delete(self._ram_key(ns, record_id))
            if self.redis is not None:
                self.redis.delete_record(ns, record_id)
        if ids:
            self._invalidate(ns)
        return len(ids)

    def source_records(
        self,
        source: str,
        *,
        namespace: str | None = None,
        limit: int | None = None,
    ) -> list[ContextRecord]:
        return self.store.list_source_records(
            self._namespace(namespace), source, limit=limit
        )

    def source_count(self, source: str, *, namespace: str | None = None) -> int:
        return self.store.count_source(self._namespace(namespace), source)

    def _embedder_cache_identity(self) -> dict[str, Any]:
        embedder_type = type(self.embedder)
        identity: dict[str, Any] = {
            "class": f"{embedder_type.__module__}.{embedder_type.__qualname__}",
            "dimensions": self.embedder.dimensions,
        }
        for attribute in ("model", "base_url", "include_bigrams"):
            if not hasattr(self.embedder, attribute):
                continue
            value = getattr(self.embedder, attribute)
            if value is None or isinstance(value, (str, int, float, bool)):
                identity[attribute] = value
        return identity

    def _query_cache_key(
        self,
        namespace: str,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None,
        minimum_score: float,
        policy: RetrievalPolicy,
    ) -> str:
        value = {
            "namespace": namespace,
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "minimum_score": minimum_score,
            "generation": self._generation(namespace),
            "ranker": RANKER_VERSION,
            "policy": policy.cache_identity(),
            "embedder": self._embedder_cache_identity(),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    def _get_cached_hits(
        self,
        namespace: str,
        cache_key: str,
        digest: str,
    ) -> list[SearchHit] | None:
        cached = self.query_ram.get(digest)
        if cached is not None:
            return cached
        if self.redis is None:
            return None
        cached = self.redis.get_query(namespace, cache_key)
        if cached is None:
            return None
        encoded_size = len(json.dumps([hit.to_dict() for hit in cached]))
        self.query_ram.put(
            digest,
            cached,
            size=encoded_size,
            ttl_seconds=self.redis.query_ttl,
        )
        return cached

    def _rank_query(
        self,
        query: str,
        namespace: str,
        *,
        top_k: int,
        filters: dict[str, Any] | None,
        minimum_score: float,
        policy: RetrievalPolicy,
    ) -> list[SearchHit]:
        query_vector = self.embedder.embed([query])[0]
        records = self.store.list_records(namespace)
        lexical_scores = dict(
            self.store.lexical_search(namespace, query, limit=max(64, top_k * 8))
        )
        return rank_records(
            records,
            query_vector,
            lexical_scores,
            filters=filters,
            minimum_score=minimum_score,
            top_k=top_k,
            policy=policy,
            now=time.time(),
        )

    def _publish_records(self, namespace: str, hits: list[SearchHit]) -> None:
        if not hits:
            return
        touched_at = time.time()
        self.store.touch_many(
            namespace,
            (hit.record.id for hit in hits),
            touched_at,
        )
        for hit in hits:
            hit.record.accessed_at = touched_at
            self.ram.put(
                self._ram_key(namespace, hit.record.id),
                hit.record,
                size=record_size(hit.record),
            )
            if self.redis is not None:
                self.redis.put_record(hit.record)

    def _cache_hits(
        self,
        namespace: str,
        cache_key: str,
        digest: str,
        hits: list[SearchHit],
    ) -> None:
        encoded_size = len(json.dumps([hit.to_dict() for hit in hits])) + 128
        query_ttl = 60 if self.redis is None else self.redis.query_ttl
        self.query_ram.put(digest, hits, size=encoded_size, ttl_seconds=query_ttl)
        if self.redis is not None:
            self.redis.put_query(namespace, cache_key, hits)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 8,
        namespace: str | None = None,
        filters: dict[str, Any] | None = None,
        minimum_score: float = 0.0,
        vector_weight: float = 0.60,
        lexical_weight: float = 0.25,
        importance_weight: float = 0.10,
        recency_weight: float = 0.05,
        recency_half_life_days: float = 14.0,
    ) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if top_k <= 0:
            return []
        ns = self._namespace(namespace)
        policy = RetrievalPolicy(
            vector_weight=vector_weight,
            lexical_weight=lexical_weight,
            importance_weight=importance_weight,
            recency_weight=recency_weight,
            recency_half_life_days=recency_half_life_days,
        )
        cache_key = self._query_cache_key(
            ns,
            query,
            top_k,
            filters,
            minimum_score,
            policy,
        )
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        cached = self._get_cached_hits(ns, cache_key, digest)
        if cached is not None:
            return cached
        hits = self._rank_query(
            query,
            ns,
            top_k=top_k,
            filters=filters,
            minimum_score=minimum_score,
            policy=policy,
        )
        self._publish_records(ns, hits)
        self._cache_hits(ns, cache_key, digest, hits)
        return hits

    def purge_expired(self) -> int:
        purged = self.store.purge_expired()
        if purged:
            self.ram.clear()
            self.query_ram.clear()
            self._invalidate(self.namespace)
        return purged

    def stats(self, *, namespace: str | None = None) -> dict[str, Any]:
        ns = self._namespace(namespace)
        return {
            "namespace": ns,
            "sqlite": self.store.stats(ns),
            "ram_records": self.ram.stats(),
            "ram_queries": self.query_ram.stats(),
            "redis": None if self.redis is None else self.redis.stats(),
            "embedder": {
                "name": type(self.embedder).__name__,
                "dimensions": self.embedder.dimensions,
            },
            "generation": self._generation(ns),
        }

    def open_context_governor(
        self,
        session_id: str,
        *,
        collection: str | None = None,
        token_budget: int = 12000,
        recent_token_budget: int = 4000,
        protected_token_budget: int = 2000,
        max_books: int = 12,
        recent_ring_events: int = 256,
        work_ring_capacity: int = 1024,
        worker_poll_seconds: float = 0.1,
        start_worker: bool = True,
    ) -> "LibraryContextGovernor":
        """Create a context governor for one agent thread."""

        from .governor import LibraryContextGovernor

        return LibraryContextGovernor(
            self,
            session_id,
            collection=collection,
            token_budget=token_budget,
            recent_token_budget=recent_token_budget,
            protected_token_budget=protected_token_budget,
            max_books=max_books,
            recent_ring_events=recent_ring_events,
            work_ring_capacity=work_ring_capacity,
            worker_poll_seconds=worker_poll_seconds,
            start_worker=start_worker,
        )

    def close(self) -> None:
        if self.redis is not None:
            self.redis.close()
        self.store.close()

    def __enter__(self) -> "ContextCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
