from __future__ import annotations

import json
import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .embeddings import TOKEN_RE
from .models import ContextEvent, ContextRecord, ContextWatermarks

SCHEMA_VERSION = 2


def _pack_vector(vector: list[float]) -> bytes:
    if not vector:
        return b""
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(value: bytes, dimensions: int) -> list[float]:
    if not value or dimensions == 0:
        return []
    return list(struct.unpack(f"<{dimensions}f", value))


class SQLiteStore:
    """Durable source of truth for records, vectors, and lexical search."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level="DEFERRED"
        )
        self._connection.row_factory = sqlite3.Row
        self._fts_enabled = True
        self._initialize()

    @property
    def fts_enabled(self) -> bool:
        return self._fts_enabled

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                PRAGMA foreign_keys = ON;
                PRAGMA busy_timeout = 5000;

                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS records (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    embedding_dim INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    importance REAL NOT NULL,
                    token_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    expires_at REAL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY (namespace, id)
                );

                CREATE INDEX IF NOT EXISTS records_namespace_accessed
                    ON records(namespace, accessed_at DESC);
                CREATE INDEX IF NOT EXISTS records_namespace_source
                    ON records(namespace, source);
                CREATE INDEX IF NOT EXISTS records_expires
                    ON records(expires_at) WHERE expires_at IS NOT NULL;

                CREATE TABLE IF NOT EXISTS thread_heads (
                    namespace TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, session_id)
                );

                CREATE TABLE IF NOT EXISTS thread_events (
                    namespace TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    importance REAL NOT NULL,
                    protected INTEGER NOT NULL,
                    token_count INTEGER NOT NULL,
                    record_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    indexed_at REAL,
                    PRIMARY KEY (namespace, session_id, sequence),
                    UNIQUE (namespace, event_id)
                );

                CREATE INDEX IF NOT EXISTS thread_events_recent
                    ON thread_events(namespace, session_id, sequence DESC);
                CREATE INDEX IF NOT EXISTS thread_events_protected
                    ON thread_events(namespace, session_id, protected, sequence DESC);

                CREATE TABLE IF NOT EXISTS context_outbox (
                    namespace TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    processed_at REAL,
                    last_error TEXT,
                    PRIMARY KEY (namespace, event_id),
                    FOREIGN KEY (namespace, event_id)
                        REFERENCES thread_events(namespace, event_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS context_outbox_pending
                    ON context_outbox(processed_at, available_at, created_at);
                """
            )
            try:
                self._connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                        record_key UNINDEXED,
                        namespace UNINDEXED,
                        text,
                        tokenize='unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError:
                self._fts_enabled = False
            self._connection.execute(
                "INSERT OR REPLACE INTO cache_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._connection.commit()

    @staticmethod
    def _record_key(namespace: str, record_id: str) -> str:
        return f"{namespace}\x1f{record_id}"

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ContextRecord:
        return ContextRecord(
            id=row["id"],
            namespace=row["namespace"],
            text=row["text"],
            embedding=_unpack_vector(row["embedding"], row["embedding_dim"]),
            metadata=json.loads(row["metadata_json"]),
            source=row["source"],
            importance=row["importance"],
            token_count=row["token_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            accessed_at=row["accessed_at"],
            expires_at=row["expires_at"],
            content_hash=row["content_hash"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ContextEvent:
        return ContextEvent(
            event_id=row["event_id"],
            namespace=row["namespace"],
            session_id=row["session_id"],
            sequence=row["sequence"],
            role=row["role"],
            content=row["content"],
            metadata=json.loads(row["metadata_json"]),
            importance=row["importance"],
            protected=bool(row["protected"]),
            token_count=row["token_count"],
            record_id=row["record_id"],
            created_at=row["created_at"],
            indexed_at=row["indexed_at"],
        )

    def upsert(self, record: ContextRecord) -> None:
        params = (
            record.namespace,
            record.id,
            record.text,
            _pack_vector(record.embedding),
            len(record.embedding),
            json.dumps(record.metadata, ensure_ascii=False, separators=(",", ":")),
            record.source,
            record.importance,
            record.token_count,
            record.created_at,
            record.updated_at,
            record.accessed_at,
            record.expires_at,
            record.content_hash,
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO records(
                    namespace, id, text, embedding, embedding_dim, metadata_json,
                    source, importance, token_count, created_at, updated_at,
                    accessed_at, expires_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, id) DO UPDATE SET
                    text=excluded.text,
                    embedding=excluded.embedding,
                    embedding_dim=excluded.embedding_dim,
                    metadata_json=excluded.metadata_json,
                    source=excluded.source,
                    importance=excluded.importance,
                    token_count=excluded.token_count,
                    updated_at=excluded.updated_at,
                    accessed_at=excluded.accessed_at,
                    expires_at=excluded.expires_at,
                    content_hash=excluded.content_hash
                """,
                params,
            )
            if self._fts_enabled:
                key = self._record_key(record.namespace, record.id)
                self._connection.execute(
                    "DELETE FROM records_fts WHERE record_key = ?", (key,)
                )
                self._connection.execute(
                    "INSERT INTO records_fts(record_key, namespace, text) VALUES (?, ?, ?)",
                    (key, record.namespace, record.text),
                )

    def get(self, namespace: str, record_id: str) -> ContextRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM records WHERE namespace = ? AND id = ?",
                (namespace, record_id),
            ).fetchone()
        if row is None:
            return None
        record = self._from_row(row)
        if record.expires_at is not None and record.expires_at <= time.time():
            self.delete(namespace, record_id)
            return None
        return record

    def list_records(self, namespace: str) -> list[ContextRecord]:
        now = time.time()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM records
                WHERE namespace = ? AND (expires_at IS NULL OR expires_at > ?)
                """,
                (namespace, now),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_source_records(
        self, namespace: str, source: str, *, limit: int | None = None
    ) -> list[ContextRecord]:
        now = time.time()
        sql = """
            SELECT * FROM records
            WHERE namespace = ? AND source = ?
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC, id DESC
        """
        params: tuple[Any, ...] = (namespace, source, now)
        if limit is not None:
            sql += " LIMIT ?"
            params += (max(0, limit),)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in reversed(rows)]

    def count_source(self, namespace: str, source: str) -> int:
        now = time.time()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count FROM records
                WHERE namespace = ? AND source = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (namespace, source, now),
            ).fetchone()
        return int(row["count"])

    def append_thread_event(
        self,
        *,
        namespace: str,
        session_id: str,
        event_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any],
        importance: float,
        protected: bool,
        token_count: int,
        record_id: str,
        created_at: float,
    ) -> ContextEvent:
        """Append an event and its indexing outbox item in one durable transaction."""

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                existing = self._connection.execute(
                    """
                    SELECT * FROM thread_events
                    WHERE namespace = ? AND event_id = ?
                    """,
                    (namespace, event_id),
                ).fetchone()
                if existing is not None:
                    event = self._event_from_row(existing)
                    if (
                        event.session_id != session_id
                        or event.role != role
                        or event.content != content
                    ):
                        raise ValueError(
                            "event_id already belongs to different thread content"
                        )
                    self._connection.commit()
                    return event

                head = self._connection.execute(
                    """
                    SELECT last_sequence FROM thread_heads
                    WHERE namespace = ? AND session_id = ?
                    """,
                    (namespace, session_id),
                ).fetchone()
                sequence = 1 if head is None else int(head["last_sequence"]) + 1
                self._connection.execute(
                    """
                    INSERT INTO thread_heads(namespace, session_id, last_sequence, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(namespace, session_id) DO UPDATE SET
                        last_sequence=excluded.last_sequence,
                        updated_at=excluded.updated_at
                    """,
                    (namespace, session_id, sequence, created_at),
                )
                self._connection.execute(
                    """
                    INSERT INTO thread_events(
                        namespace, session_id, sequence, event_id, role, content,
                        metadata_json, importance, protected, token_count, record_id,
                        created_at, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        namespace,
                        session_id,
                        sequence,
                        event_id,
                        role,
                        content,
                        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                        importance,
                        int(protected),
                        token_count,
                        record_id,
                        created_at,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO context_outbox(
                        namespace, event_id, session_id, sequence, event_type,
                        created_at, available_at
                    ) VALUES (?, ?, ?, ?, 'index', ?, ?)
                    """,
                    (
                        namespace,
                        event_id,
                        session_id,
                        sequence,
                        created_at,
                        created_at,
                    ),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return ContextEvent(
            event_id=event_id,
            namespace=namespace,
            session_id=session_id,
            sequence=sequence,
            role=role,
            content=content,
            metadata=dict(metadata),
            importance=importance,
            protected=protected,
            token_count=token_count,
            record_id=record_id,
            created_at=created_at,
        )

    def get_thread_event(self, namespace: str, event_id: str) -> ContextEvent | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM thread_events
                WHERE namespace = ? AND event_id = ?
                """,
                (namespace, event_id),
            ).fetchone()
        return None if row is None else self._event_from_row(row)

    def list_thread_events(
        self,
        namespace: str,
        session_id: str,
        *,
        limit: int | None = None,
        protected_only: bool = False,
    ) -> list[ContextEvent]:
        sql = """
            SELECT * FROM thread_events
            WHERE namespace = ? AND session_id = ?
        """
        params: tuple[Any, ...] = (namespace, session_id)
        if protected_only:
            sql += " AND protected = 1"
        sql += " ORDER BY sequence DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params += (max(0, limit),)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._event_from_row(row) for row in reversed(rows)]

    def count_thread_events(self, namespace: str, session_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT last_sequence FROM thread_heads
                WHERE namespace = ? AND session_id = ?
                """,
                (namespace, session_id),
            ).fetchone()
        return 0 if row is None else int(row["last_sequence"])

    def set_thread_event_protected(
        self, namespace: str, session_id: str, event_id: str, protected: bool
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE thread_events SET protected = ?
                WHERE namespace = ? AND session_id = ? AND event_id = ?
                """,
                (int(protected), namespace, session_id, event_id),
            )
        return cursor.rowcount > 0

    def pending_outbox_event_ids(self, *, limit: int = 128) -> list[tuple[str, str]]:
        now = time.time()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT namespace, event_id FROM context_outbox
                WHERE processed_at IS NULL AND available_at <= ?
                ORDER BY created_at, sequence
                LIMIT ?
                """,
                (now, max(1, limit)),
            ).fetchall()
        return [(row["namespace"], row["event_id"]) for row in rows]

    def mark_thread_event_indexed(
        self, namespace: str, event_id: str, *, indexed_at: float
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE thread_events SET indexed_at = COALESCE(indexed_at, ?)
                WHERE namespace = ? AND event_id = ?
                """,
                (indexed_at, namespace, event_id),
            )
            self._connection.execute(
                """
                UPDATE context_outbox
                SET processed_at = ?, last_error = NULL
                WHERE namespace = ? AND event_id = ?
                """,
                (indexed_at, namespace, event_id),
            )

    def fail_outbox_event(
        self,
        namespace: str,
        event_id: str,
        *,
        error: str,
        retry_after_seconds: float,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE context_outbox
                SET attempts = attempts + 1, last_error = ?, available_at = ?
                WHERE namespace = ? AND event_id = ? AND processed_at IS NULL
                """,
                (
                    error[:2000],
                    time.time() + max(0.01, retry_after_seconds),
                    namespace,
                    event_id,
                ),
            )

    def thread_watermarks(self, namespace: str, session_id: str) -> ContextWatermarks:
        with self._lock:
            head = self._connection.execute(
                """
                SELECT last_sequence FROM thread_heads
                WHERE namespace = ? AND session_id = ?
                """,
                (namespace, session_id),
            ).fetchone()
            recorded = 0 if head is None else int(head["last_sequence"])
            missing = self._connection.execute(
                """
                SELECT MIN(sequence) AS first_missing
                FROM thread_events
                WHERE namespace = ? AND session_id = ? AND indexed_at IS NULL
                """,
                (namespace, session_id),
            ).fetchone()
            pending = self._connection.execute(
                """
                SELECT COUNT(*) AS count FROM context_outbox
                WHERE namespace = ? AND session_id = ? AND processed_at IS NULL
                """,
                (namespace, session_id),
            ).fetchone()
        first_missing = missing["first_missing"]
        indexed = recorded if first_missing is None else max(0, int(first_missing) - 1)
        return ContextWatermarks(
            recorded_through=recorded,
            embedded_through=indexed,
            indexed_through=indexed,
            team_synced_through=0,
            pending_events=int(pending["count"]),
        )

    def lexical_search(
        self, namespace: str, query: str, *, limit: int = 64
    ) -> list[tuple[str, float]]:
        tokens = list(
            dict.fromkeys(token.lower() for token in TOKEN_RE.findall(query))
        )[:24]
        if not tokens:
            return []
        now = time.time()
        if self._fts_enabled:
            match = " OR ".join(
                f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
            )
            try:
                with self._lock:
                    rows = self._connection.execute(
                        """
                        SELECT f.record_key
                        FROM records_fts AS f
                        WHERE records_fts MATCH ? AND f.namespace = ?
                        ORDER BY bm25(records_fts)
                        LIMIT ?
                        """,
                        (match, namespace, limit),
                    ).fetchall()
                return [
                    (row["record_key"].split("\x1f", 1)[1], 1.0 / (1.0 + rank))
                    for rank, row in enumerate(rows)
                ]
            except sqlite3.OperationalError:
                pass
        pattern = "%" + "%".join(tokens[:4]) + "%"
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id FROM records
                WHERE namespace = ? AND lower(text) LIKE ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY accessed_at DESC LIMIT ?
                """,
                (namespace, pattern, now, limit),
            ).fetchall()
        return [(row["id"], 1.0 / (1.0 + rank)) for rank, row in enumerate(rows)]

    def touch_many(self, namespace: str, record_ids: Iterable[str], at: float) -> None:
        ids = list(record_ids)
        if not ids:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                "UPDATE records SET accessed_at = ? WHERE namespace = ? AND id = ?",
                [(at, namespace, record_id) for record_id in ids],
            )

    def delete(self, namespace: str, record_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM records WHERE namespace = ? AND id = ?",
                (namespace, record_id),
            )
            if self._fts_enabled:
                self._connection.execute(
                    "DELETE FROM records_fts WHERE record_key = ?",
                    (self._record_key(namespace, record_id),),
                )
        return cursor.rowcount > 0

    def delete_source(self, namespace: str, source: str) -> list[str]:
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT id FROM records WHERE namespace = ? AND source = ?",
                (namespace, source),
            ).fetchall()
            ids = [row["id"] for row in rows]
            self._connection.execute(
                "DELETE FROM records WHERE namespace = ? AND source = ?",
                (namespace, source),
            )
            if self._fts_enabled:
                self._connection.executemany(
                    "DELETE FROM records_fts WHERE record_key = ?",
                    [(self._record_key(namespace, record_id),) for record_id in ids],
                )
        return ids

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT namespace, id FROM records WHERE expires_at <= ?", (now,)
            ).fetchall()
            self._connection.execute(
                "DELETE FROM records WHERE expires_at <= ?", (now,)
            )
            if self._fts_enabled:
                self._connection.executemany(
                    "DELETE FROM records_fts WHERE record_key = ?",
                    [(self._record_key(row["namespace"], row["id"]),) for row in rows],
                )
        return len(rows)

    def stats(self, namespace: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS records,
                       COALESCE(SUM(length(text)), 0) AS text_bytes,
                       COALESCE(SUM(length(embedding)), 0) AS vector_bytes
                FROM records WHERE namespace = ?
                """,
                (namespace,),
            ).fetchone()
            event_row = self._connection.execute(
                """
                SELECT COUNT(*) AS events,
                       COALESCE(SUM(CASE WHEN indexed_at IS NULL THEN 1 ELSE 0 END), 0)
                           AS unindexed_events
                FROM thread_events WHERE namespace = ?
                """,
                (namespace,),
            ).fetchone()
            outbox_row = self._connection.execute(
                """
                SELECT COUNT(*) AS pending
                FROM context_outbox
                WHERE namespace = ? AND processed_at IS NULL
                """,
                (namespace,),
            ).fetchone()
        return {
            "path": str(self.path.resolve()),
            "records": row["records"],
            "text_bytes": row["text_bytes"],
            "vector_bytes": row["vector_bytes"],
            "fts_enabled": self._fts_enabled,
            "thread_events": event_row["events"],
            "unindexed_events": event_row["unindexed_events"],
            "pending_outbox": outbox_row["pending"],
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
