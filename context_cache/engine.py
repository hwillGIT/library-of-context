from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .concurrency import ReadWriteGate
from .embeddings import Embedder, HashingEmbedder, estimate_tokens
from .limits import MAX_RESULT_BOOKS
from .models import ContextEvent, ContextRecord, OutboxClaim, SearchHit
from .process_lock import DatabaseRuntimeLock
from .ram import ByteLRU, record_size
from .redis_hot import RedisHotCache
from .retrieval import RANKER_VERSION, RetrievalPolicy, rank_records
from .runtime import LibraryRuntime, RuntimeSettings
from .scopes import ContextScope, ScopeSelection, ThreadKey, validate_record_scope
from .store import SQLiteStore

if TYPE_CHECKING:
    from .governor import LibraryContextGovernor

OperationResult = TypeVar("OperationResult")


def _open_operation(
    method: Callable[..., OperationResult],
) -> Callable[..., OperationResult]:
    """Hold a lifecycle lease for one public cache operation."""

    @wraps(method)
    def guarded(self: "ContextCache", *args: Any, **kwargs: Any) -> OperationResult:
        with self._operation():
            return method(self, *args, **kwargs)

    return cast(Callable[..., OperationResult], guarded)


def _status_operation(
    method: Callable[..., OperationResult],
) -> Callable[..., OperationResult]:
    """Hold a lifecycle lease while reading shutdown status."""

    @wraps(method)
    def guarded(self: "ContextCache", *args: Any, **kwargs: Any) -> OperationResult:
        with self._close_lock:
            with self._operation(allow_closing=True):
                return method(self, *args, **kwargs)

    return cast(Callable[..., OperationResult], guarded)


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
        runtime_settings: RuntimeSettings | None = None,
        exclusive_database_owner: bool = False,
    ) -> None:
        del exclusive_database_owner
        sqlite_path_value = os.fspath(sqlite_path)
        if sqlite_path_value == ":memory:":
            raise ValueError(
                "Library storage requires a filesystem-backed SQLite database; "
                "':memory:' is unsupported"
            )
        resolved_sqlite_path = Path(os.path.abspath(sqlite_path_value))
        if not isinstance(namespace, str):
            raise TypeError("namespace must be a string")
        ThreadKey(namespace, "runtime")
        self.namespace = namespace
        self.database_runtime_lock = DatabaseRuntimeLock(resolved_sqlite_path)
        try:
            self.store = SQLiteStore(resolved_sqlite_path)
        except Exception:
            if self.database_runtime_lock is not None:
                self.database_runtime_lock.close()
            raise
        self.redis: RedisHotCache | None = None
        try:
            self.embedder = embedder or HashingEmbedder()
            self.ram = ByteLRU[ContextRecord](ram_bytes)
            self.query_ram = ByteLRU[list[SearchHit]](query_cache_bytes)
            self._lock = threading.RLock()
            self._close_lock = threading.Lock()
            self._operation_condition = threading.Condition(self._lock)
            self._operation_local = threading.local()
            self._active_operations = 0
            self._operations_sealed = False
            self._close_reaper_started = False
            self._resource_close_errors: dict[str, str] = {}
            self._redis_closed = False
            self._closed_redis_resource: object | None = None
            self._store_closed = False
            self._database_lock_closed = False
            self._mutation_gate = ReadWriteGate()
            self._record_write_locks = tuple(threading.RLock() for _ in range(64))
            self._closed = False
            self._lifecycle = "open"
            self._local_generation = 0
            resolved_redis_url = redis_url
            if resolved_redis_url is None:
                resolved_redis_url = os.environ.get(
                    "LIBRARY_OF_CONTEXT_REDIS_URL",
                    os.environ.get(
                        "CONTEXT_CACHE_REDIS_URL",
                        "redis://127.0.0.1:6379/0",
                    ),
                )
            self.redis = (
                None
                if resolved_redis_url == ""
                else RedisHotCache(resolved_redis_url, required=redis_required)
            )
            self._redis_closed = self.redis is None
            self.runtime = LibraryRuntime(self, settings=runtime_settings)
        except Exception:
            if self.redis is not None:
                self.redis.close()
            self.store.close()
            if self.database_runtime_lock is not None:
                self.database_runtime_lock.close()
            raise

    @staticmethod
    def _ram_key(namespace: str, record_id: str) -> str:
        return f"{namespace}\x1f{record_id}"

    @staticmethod
    def _copy_record(record: ContextRecord) -> ContextRecord:
        return replace(
            record,
            embedding=list(record.embedding),
            metadata=deepcopy(record.metadata),
        )

    @classmethod
    def _copy_hit(cls, hit: SearchHit) -> SearchHit:
        return SearchHit(
            record=cls._copy_record(hit.record),
            score=hit.score,
            vector_score=hit.vector_score,
            lexical_score=hit.lexical_score,
            importance_score=hit.importance_score,
            recency_score=hit.recency_score,
        )

    @classmethod
    def _copy_hits(cls, hits: Iterable[SearchHit]) -> list[SearchHit]:
        return [cls._copy_hit(hit) for hit in hits]

    def _record_write_lock(self, namespace: str, record_id: str) -> threading.RLock:
        slot = hash((namespace, record_id)) % len(self._record_write_locks)
        return self._record_write_locks[slot]

    def _ensure_open(self) -> None:
        with self._lock:
            operation_depth = getattr(self._operation_local, "depth", 0)
            if self._lifecycle != "open" and operation_depth == 0:
                raise RuntimeError(f"Context cache is {self._lifecycle}")

    @contextmanager
    def _operation(self, *, allow_closing: bool = False) -> Iterator[None]:
        depth = getattr(self._operation_local, "depth", 0)
        if depth:
            self._operation_local.depth = depth + 1
            try:
                yield
            finally:
                self._operation_local.depth -= 1
            return
        with self._operation_condition:
            permitted = self._lifecycle == "open" or (
                allow_closing
                and self._lifecycle == "closing"
                and not self._operations_sealed
            )
            if not permitted:
                raise RuntimeError(f"Context cache is {self._lifecycle}")
            self._active_operations += 1
            self._operation_local.depth = 1
        try:
            yield
        finally:
            with self._operation_condition:
                self._operation_local.depth = 0
                self._active_operations -= 1
                self._operation_condition.notify_all()

    def _wait_for_operations(self, timeout: float) -> bool:
        with self._operation_condition:
            drained = self._operation_condition.wait_for(
                lambda: self._active_operations == 0,
                timeout=max(0.0, timeout),
            )
            if drained:
                self._operations_sealed = True
            return drained

    def _start_close_reaper(self) -> None:
        with self._lock:
            if self._close_reaper_started or self._closed:
                return
            self._close_reaper_started = True

        def reap() -> None:
            while True:
                time.sleep(0.05)
                if self.close():
                    return

        threading.Thread(
            target=reap,
            name="library-close-reaper",
            daemon=True,
        ).start()

    def _resolve_namespace(self, namespace: str | None) -> str:
        resolved = self.namespace if namespace is None else namespace
        if not isinstance(resolved, str):
            raise TypeError("namespace must be a string")
        ThreadKey(resolved, "runtime")
        return resolved

    def _namespace(self, namespace: str | None) -> str:
        self._ensure_open()
        return self._resolve_namespace(namespace)

    def _generation(self) -> int:
        return self._local_generation

    def _invalidate(self) -> None:
        with self._lock:
            self._local_generation += 1

    @_open_operation
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
        scope: ContextScope | str = ContextScope.PROJECT,
        owner_session_id: str | None = None,
        team_id: str | None = None,
    ) -> ContextRecord:
        record = self._prepare_record(
            text,
            record_id=record_id,
            namespace=namespace,
            metadata=metadata,
            source=source,
            importance=importance,
            ttl_seconds=ttl_seconds,
            scope=scope,
            owner_session_id=owner_session_id,
            team_id=team_id,
        )
        return self._copy_record(self._persist_record(record, ttl_seconds=ttl_seconds))

    def _prepare_record(
        self,
        text: str,
        *,
        record_id: str | None,
        namespace: str | None,
        metadata: dict[str, Any] | None,
        source: str,
        importance: float,
        ttl_seconds: float | None,
        scope: ContextScope | str,
        owner_session_id: str | None,
        team_id: str | None,
    ) -> ContextRecord:
        if not text.strip():
            raise ValueError("text cannot be empty")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        ns = self._resolve_namespace(namespace)
        resolved_scope, resolved_owner, resolved_team = validate_record_scope(
            scope,
            owner_session_id,
            team_id,
        )
        record_id = record_id or uuid.uuid4().hex
        existing = self.store.get(ns, record_id)
        if existing is not None and (
            existing.scope is not resolved_scope
            or existing.owner_session_id != resolved_owner
            or existing.team_id != resolved_team
        ):
            raise ValueError(
                "record visibility cannot be changed by upsert; use a distinct "
                "record ID or an explicit promotion"
            )
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
            scope=resolved_scope,
            owner_session_id=resolved_owner,
            team_id=resolved_team,
        )
        return record

    def _persist_record(
        self,
        record: ContextRecord,
        *,
        ttl_seconds: float | None = None,
    ) -> ContextRecord:
        with (
            self._mutation_gate.read(),
            self._record_write_lock(record.namespace, record.id),
        ):
            self._invalidate()
            persisted = self.store.upsert(record)
            self.ram.put(
                self._ram_key(persisted.namespace, persisted.id),
                persisted,
                size=record_size(persisted),
                ttl_seconds=ttl_seconds,
            )
            if self.redis is not None:
                self.redis.put_record(persisted)
            self._invalidate()
            return persisted

    def _persist_claimed_record(
        self,
        record: ContextRecord,
        event: ContextEvent,
        claim: OutboxClaim,
        *,
        indexed_at: float,
    ) -> ContextRecord:
        with (
            self._mutation_gate.read(),
            self._record_write_lock(record.namespace, record.id),
        ):
            self._invalidate()
            persisted = self.store.complete_outbox_claim(
                record,
                session_id=event.session_id,
                event_id=event.event_id,
                indexed_at=indexed_at,
                claim_token=claim.claim_token,
            )
            self.ram.put(
                self._ram_key(persisted.namespace, persisted.id),
                persisted,
                size=record_size(persisted),
            )
            if self.redis is not None:
                self.redis.put_record(persisted)
            self._invalidate()
            return persisted

    @_open_operation
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
        scope: ContextScope | str = ContextScope.PROJECT,
        owner_session_id: str | None = None,
        team_id: str | None = None,
    ) -> list[ContextRecord]:
        ns = self._namespace(namespace)
        resolved_scope, resolved_owner, resolved_team = validate_record_scope(
            scope,
            owner_session_id,
            team_id,
        )
        selection = ScopeSelection.resolve(
            (resolved_scope,),
            session_id=resolved_owner,
            team_ids=() if resolved_team is None else (resolved_team,),
        )
        if replace_source:
            self.delete_source(
                source,
                namespace=ns,
                scopes=selection.scopes,
                session_id=selection.session_id,
                team_ids=selection.team_ids,
            )
        records: list[ContextRecord] = []
        for index, chunk in enumerate(chunk_text(text, chunk_tokens, overlap_tokens)):
            digest = hashlib.sha256(
                (
                    f"{ns}\x00{source}\x00{resolved_scope.value}\x00"
                    f"{resolved_owner or ''}\x00{resolved_team or ''}\x00"
                    f"{index}\x00{chunk}"
                ).encode("utf-8")
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
                    scope=resolved_scope,
                    owner_session_id=resolved_owner,
                    team_id=resolved_team,
                )
            )
        return records

    @_open_operation
    def get(
        self,
        record_id: str,
        *,
        namespace: str | None = None,
        scopes: Iterable[ContextScope | str] | None = None,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> ContextRecord | None:
        ns = self._namespace(namespace)
        selection = ScopeSelection.resolve(
            scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        with self._mutation_gate.read(), self._record_write_lock(ns, record_id):
            key = self._ram_key(ns, record_id)
            record = self.ram.get(key)
            now = time.time()
            if record is not None:
                if (
                    record.namespace == ns
                    and record.id == record_id
                    and (record.expires_at is None or record.expires_at > now)
                ) and selection.allows(
                    record.scope,
                    record.owner_session_id,
                    record.team_id,
                ):
                    return self._copy_record(record)
            if self.redis is not None:
                cached = self.redis.get_record(ns, record_id)
                durable = self.store.get_visible(ns, record_id, selection=selection)
                if cached is not None and cached != durable:
                    self.redis.delete_record(ns, record_id)
                record = durable
            else:
                record = self.store.get_visible(ns, record_id, selection=selection)
            if record is not None:
                ttl = (
                    None
                    if record.expires_at is None
                    else max(0.0, record.expires_at - now)
                )
                self.ram.put(key, record, size=record_size(record), ttl_seconds=ttl)
                if self.redis is not None:
                    self.redis.put_record(record)
            return None if record is None else self._copy_record(record)

    @_open_operation
    def set_thread_event_protected(
        self,
        namespace: str,
        session_id: str,
        event_id: str,
        protected: bool,
    ) -> bool:
        """Synchronize protection across the event log and cache tiers."""

        namespace = self._namespace(namespace)
        event = self.store.get_thread_event(namespace, session_id, event_id)
        if event is None:
            return False
        with (
            self._mutation_gate.read(),
            self._record_write_lock(namespace, event.record_id),
        ):
            self._invalidate()
            found, persisted = self.store.set_thread_event_protected(
                namespace,
                session_id,
                event_id,
                protected,
            )
            if not found:
                self._invalidate()
                return False
            key = self._ram_key(namespace, event.record_id)
            if persisted is None:
                self.ram.delete(key)
                if self.redis is not None:
                    self.redis.delete_record(namespace, event.record_id)
            else:
                self.ram.put(key, persisted, size=record_size(persisted))
                if self.redis is not None:
                    self.redis.put_record(persisted)
            self._invalidate()
            return True

    @_open_operation
    def promote(
        self,
        record_id: str,
        *,
        target_scope: ContextScope | str,
        namespace: str | None = None,
        source_session_id: str | None = None,
        promoted_record_id: str | None = None,
        target_team_id: str | None = None,
    ) -> ContextRecord:
        """Copy a record into project or team scope with retained provenance."""

        ns = self._namespace(namespace)
        target = ContextScope(target_scope)
        if target is ContextScope.THREAD:
            raise ValueError("promotion target must be project or team scope")
        if target is ContextScope.TEAM and not (target_team_id or "").strip():
            raise ValueError("team promotion requires target_team_id")
        if target is ContextScope.PROJECT and target_team_id is not None:
            raise ValueError("project promotion cannot set target_team_id")
        with self._mutation_gate.read(), self._record_write_lock(ns, record_id):
            source = self.store.get(ns, record_id)
        if source is None:
            raise KeyError(f"record not found: {record_id}")
        if source.scope is ContextScope.THREAD and (
            not source_session_id or source.owner_session_id != source_session_id
        ):
            raise PermissionError("thread record promotion requires its owner session")
        destination_id = (
            promoted_record_id
            or hashlib.sha256(
                (
                    f"{ns}\x00{source.id}\x00{target.value}\x00{target_team_id or ''}"
                ).encode("utf-8")
            ).hexdigest()[:24]
        )
        with self._mutation_gate.read(), self._record_write_lock(ns, destination_id):
            existing = self.store.get(ns, destination_id)
            if existing is not None and (
                existing.scope is not target
                or existing.owner_session_id is not None
                or existing.team_id != target_team_id
            ):
                raise ValueError(
                    "promotion destination belongs to a different visibility boundary"
                )
            if (
                existing is not None
                and existing.metadata.get("promoted_from_record_id") != source.id
            ):
                raise ValueError(
                    "promotion destination belongs to a different source record"
                )
            now = time.time()
            metadata = dict(source.metadata)
            metadata.update(
                {
                    "promoted_from_record_id": source.id,
                    "promoted_from_scope": source.scope.value,
                    "promoted_from_owner_session_id": source.owner_session_id,
                    "promoted_from_team_id": source.team_id,
                }
            )
            promoted = ContextRecord(
                id=destination_id,
                namespace=ns,
                text=source.text,
                embedding=list(source.embedding),
                metadata=metadata,
                source=source.source,
                importance=source.importance,
                token_count=source.token_count,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                accessed_at=now,
                expires_at=source.expires_at,
                content_hash=source.content_hash,
                scope=target,
                team_id=target_team_id,
            )
            ttl = (
                None
                if promoted.expires_at is None
                else max(0.0, promoted.expires_at - now)
            )
            return self._copy_record(self._persist_record(promoted, ttl_seconds=ttl))

    @_open_operation
    def delete(
        self,
        record_id: str,
        *,
        namespace: str | None = None,
        scopes: Iterable[ContextScope | str] | None = None,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> bool:
        ns = self._namespace(namespace)
        selection = ScopeSelection.resolve(
            scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        with self._mutation_gate.read(), self._record_write_lock(ns, record_id):
            if self.store.get_visible(ns, record_id, selection=selection) is None:
                return False
            self._invalidate()
            deleted = self.store.delete(ns, record_id)
            self.ram.delete(self._ram_key(ns, record_id))
            if self.redis is not None:
                self.redis.delete_record(ns, record_id)
            self._invalidate()
            return deleted

    @_open_operation
    def delete_source(
        self,
        source: str,
        *,
        namespace: str | None = None,
        scopes: Iterable[ContextScope | str] | None = None,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> int:
        ns = self._namespace(namespace)
        selection = ScopeSelection.resolve(
            scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        with self._mutation_gate.write():
            self._invalidate()
            ids = self.store.delete_source(ns, source, selection=selection)
            for record_id in ids:
                self.ram.delete(self._ram_key(ns, record_id))
                if self.redis is not None:
                    self.redis.delete_record(ns, record_id)
            if ids:
                self._invalidate()
            return len(ids)

    @_open_operation
    def source_records(
        self,
        source: str,
        *,
        namespace: str | None = None,
        limit: int | None = None,
        scopes: Iterable[ContextScope | str] | None = None,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> list[ContextRecord]:
        selection = ScopeSelection.resolve(
            scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        return self.store.list_source_records(
            self._namespace(namespace),
            source,
            limit=limit,
            selection=selection,
        )

    @_open_operation
    def source_count(
        self,
        source: str,
        *,
        namespace: str | None = None,
        scopes: Iterable[ContextScope | str] | None = None,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> int:
        selection = ScopeSelection.resolve(
            scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        return self.store.count_source(
            self._namespace(namespace),
            source,
            selection=selection,
        )

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
        selection: ScopeSelection,
    ) -> str:
        value = {
            "namespace": namespace,
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "minimum_score": minimum_score,
            "generation": self._generation(),
            "ranker": RANKER_VERSION,
            "policy": policy.cache_identity(),
            "embedder": self._embedder_cache_identity(),
            "scope_selection": selection.cache_identity(),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    def _get_cached_hits(
        self,
        namespace: str,
        cache_key: str,
        digest: str,
    ) -> list[SearchHit] | None:
        cached = self.query_ram.get(digest)
        now = time.time()
        if cached is not None:
            if all(
                hit.record.expires_at is None or hit.record.expires_at > now
                for hit in cached
            ):
                return self._copy_hits(cached)
            self.query_ram.delete(digest)
        if self.redis is None:
            return None
        cached = self.redis.get_query(namespace, cache_key)
        if cached is None or any(
            hit.record.expires_at is not None and hit.record.expires_at <= now
            for hit in cached
        ):
            return None
        encoded_size = len(json.dumps([hit.to_dict() for hit in cached]))
        self.query_ram.put(
            digest,
            cached,
            size=encoded_size,
            ttl_seconds=self.redis.query_ttl,
        )
        return self._copy_hits(cached)

    def _rank_query(
        self,
        query: str,
        namespace: str,
        *,
        top_k: int,
        filters: dict[str, Any] | None,
        minimum_score: float,
        policy: RetrievalPolicy,
        selection: ScopeSelection,
    ) -> list[SearchHit]:
        query_vector = self.embedder.embed([query])[0]
        records = self.store.list_records(namespace, selection=selection)
        lexical_scores = dict(
            self.store.lexical_search(
                namespace,
                query,
                limit=max(64, top_k * 8),
                selection=selection,
            )
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
        published: list[SearchHit] = []
        with self._mutation_gate.read():
            for hit in hits:
                with self._record_write_lock(namespace, hit.record.id):
                    durable = self.store.get(namespace, hit.record.id)
                    if (
                        durable is None
                        or durable.updated_at != hit.record.updated_at
                        or durable.content_hash != hit.record.content_hash
                        or durable.scope is not hit.record.scope
                        or durable.owner_session_id != hit.record.owner_session_id
                        or durable.team_id != hit.record.team_id
                    ):
                        continue
                    self.store.touch_many(namespace, (durable.id,), touched_at)
                    current = replace(durable, accessed_at=touched_at)
                    hit.record = current
                    self.ram.put(
                        self._ram_key(namespace, current.id),
                        current,
                        size=record_size(current),
                    )
                    if self.redis is not None:
                        self.redis.put_record(current)
                    published.append(hit)
        hits[:] = published

    def _cache_hits(
        self,
        namespace: str,
        cache_key: str,
        digest: str,
        hits: list[SearchHit],
    ) -> None:
        encoded_size = len(json.dumps([hit.to_dict() for hit in hits])) + 128
        query_ttl = 60 if self.redis is None else self.redis.query_ttl
        cached_hits = self._copy_hits(hits)
        self.query_ram.put(
            digest,
            cached_hits,
            size=encoded_size,
            ttl_seconds=query_ttl,
        )
        if self.redis is not None:
            self.redis.put_query(namespace, cache_key, cached_hits)

    @_open_operation
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
        scopes: Iterable[ContextScope | str] | None = None,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if top_k <= 0:
            return []
        if top_k > MAX_RESULT_BOOKS:
            raise ValueError(f"top_k cannot exceed {MAX_RESULT_BOOKS}")
        ns = self._namespace(namespace)
        selection = ScopeSelection.resolve(
            scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        policy = RetrievalPolicy(
            vector_weight=vector_weight,
            lexical_weight=lexical_weight,
            importance_weight=importance_weight,
            recency_weight=recency_weight,
            recency_half_life_days=recency_half_life_days,
        )
        with self._mutation_gate.read():
            cache_key = self._query_cache_key(
                ns,
                query,
                top_k,
                filters,
                minimum_score,
                policy,
                selection,
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
            selection=selection,
        )
        self._publish_records(ns, hits)
        self._cache_hits(ns, cache_key, digest, hits)
        return self._copy_hits(hits)

    @_open_operation
    def purge_expired(self) -> int:
        self._ensure_open()
        with self._mutation_gate.write():
            self._invalidate()
            purged = self.store.purge_expired()
            if purged:
                self.ram.clear()
                self.query_ram.clear()
                self._invalidate()
            return purged

    @_status_operation
    def stats(self, *, namespace: str | None = None) -> dict[str, Any]:
        ns = self._resolve_namespace(namespace)
        with self._lock:
            lifecycle = self._lifecycle
        if lifecycle == "closed":
            raise RuntimeError("Context cache is closed")
        return {
            "namespace": ns,
            "lifecycle": lifecycle,
            "sqlite": self.store.stats(ns),
            "ram_records": self.ram.stats(),
            "ram_queries": self.query_ram.stats(),
            "redis": None if self.redis is None else self.redis.stats(),
            "embedder": {
                "name": type(self.embedder).__name__,
                "dimensions": self.embedder.dimensions,
            },
            "generation": self._generation(),
            "runtime": self.runtime.status(collection=ns),
        }

    @_open_operation
    def open_context_governor(
        self,
        session_id: str,
        *,
        collection: str | None = None,
        token_budget: int = 12000,
        recent_token_budget: int = 4000,
        protected_token_budget: int = 2000,
        max_books: int = 12,
        recent_ring_events: int | None = None,
        work_ring_capacity: int | None = None,
        worker_poll_seconds: float | None = None,
        start_worker: bool = True,
    ) -> "LibraryContextGovernor":
        """Create a context governor for one agent thread."""

        from .governor import LibraryContextGovernor

        self._ensure_open()
        if work_ring_capacity is not None and (
            work_ring_capacity != self.runtime.settings.outbox_capacity
        ):
            raise ValueError(
                "configure outbox_capacity through ContextCache.runtime_settings"
            )
        if recent_ring_events is not None and (
            recent_ring_events != self.runtime.settings.recent_ring_events
        ):
            raise ValueError(
                "configure recent_ring_events through ContextCache.runtime_settings"
            )
        if worker_poll_seconds is not None and (
            worker_poll_seconds != self.runtime.settings.outbox_poll_seconds
        ):
            raise ValueError(
                "configure outbox_poll_seconds through ContextCache.runtime_settings"
            )
        return LibraryContextGovernor(
            self,
            session_id,
            collection=collection,
            token_budget=token_budget,
            recent_token_budget=recent_token_budget,
            protected_token_budget=protected_token_budget,
            max_books=max_books,
            start_worker=start_worker,
        )

    def close(self) -> bool:
        """Close storage after every runtime worker has stopped."""

        complete = False
        with self._close_lock:
            with self._lock:
                if self._closed:
                    return True
                self._lifecycle = "closing"
                self._operations_sealed = False
            operation_timeout = max(
                self.runtime.settings.outbox_shutdown_timeout_seconds,
                self.runtime.settings.desk_shutdown_timeout_seconds,
            )
            operations_drained = self._wait_for_operations(operation_timeout)
            runtime_drained = operations_drained and self.runtime.close()
            if runtime_drained and operations_drained:
                complete = self._close_resources()
                if complete:
                    with self._lock:
                        self._closed = True
                        self._lifecycle = "closed"
            elif operations_drained:
                with self._operation_condition:
                    self._operations_sealed = False
                    self._operation_condition.notify_all()
        if not complete:
            self._start_close_reaper()
        return complete

    def _close_resources(self) -> bool:
        errors: dict[str, str] = {}

        if self.redis is None:
            self._redis_closed = True
        elif not self._redis_closed or self._closed_redis_resource is not self.redis:
            self._redis_closed = False
            error = self._attempt_resource_close(self.redis.close)
            if error is None:
                self._redis_closed = True
                self._closed_redis_resource = self.redis
            else:
                errors["redis"] = error

        if not self._store_closed:
            error = self._attempt_resource_close(self.store.close)
            if error is None:
                self._store_closed = True
            else:
                errors["sqlite"] = error

        if self._store_closed and not self._database_lock_closed:
            callback = (
                (lambda: None)
                if self.database_runtime_lock is None
                else self.database_runtime_lock.close
            )
            error = self._attempt_resource_close(callback)
            if error is None:
                self._database_lock_closed = True
            else:
                errors["database_lock"] = error

        with self._lock:
            self._resource_close_errors = errors
        return bool(
            self._redis_closed and self._store_closed and self._database_lock_closed
        )

    @staticmethod
    def _attempt_resource_close(callback: Callable[[], None]) -> str | None:
        try:
            callback()
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    def __enter__(self) -> "ContextCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
