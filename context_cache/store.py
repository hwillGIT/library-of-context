from __future__ import annotations

import json
import os
import sqlite3
import stat
import struct
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Iterator

from .embeddings import TOKEN_RE
from .models import ContextEvent, ContextRecord, ContextWatermarks, OutboxClaim
from .scopes import ContextScope, ScopeSelection

SCHEMA_VERSION = 6
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _secure_open_flags(flags: int) -> int:
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    return flags


def _validate_sqlite_artifact(path: Path, descriptor: int) -> None:
    try:
        path_status = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"cannot inspect SQLite artifact {path}: {exc}") from exc
    if stat.S_ISLNK(path_status.st_mode):
        raise RuntimeError(f"SQLite artifact cannot be a symbolic link: {path}")

    opened_status = os.fstat(descriptor)
    if not stat.S_ISREG(opened_status.st_mode):
        raise RuntimeError(f"SQLite artifact is not a regular file: {path}")
    if (opened_status.st_dev, opened_status.st_ino) != (
        path_status.st_dev,
        path_status.st_ino,
    ):
        raise RuntimeError(f"SQLite artifact changed while opening: {path}")
    if opened_status.st_nlink != 1:
        raise RuntimeError(f"SQLite artifact must have one link: {path}")
    if os.name == "posix":
        if opened_status.st_uid != os.geteuid():
            raise RuntimeError(
                f"SQLite artifact is not owned by the current user: {path}"
            )
        os.fchmod(descriptor, 0o600)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            raise RuntimeError(f"SQLite artifact is not owner-only: {path}")


def _secure_existing_sqlite_artifact(path: Path) -> None:
    try:
        path_status = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"cannot inspect SQLite artifact {path}: {exc}") from exc
    if stat.S_ISLNK(path_status.st_mode):
        raise RuntimeError(f"SQLite artifact cannot be a symbolic link: {path}")

    try:
        descriptor = os.open(path, _secure_open_flags(os.O_RDWR))
    except OSError as exc:
        raise RuntimeError(f"cannot open SQLite artifact {path}: {exc}") from exc
    try:
        _validate_sqlite_artifact(path, descriptor)
    finally:
        os.close(descriptor)


def _prepare_sqlite_storage(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = _secure_open_flags(os.O_RDWR | os.O_CREAT | os.O_EXCL)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _secure_existing_sqlite_artifact(path)
    except OSError as exc:
        raise RuntimeError(f"cannot create SQLite database {path}: {exc}") from exc
    else:
        try:
            _validate_sqlite_artifact(path, descriptor)
        finally:
            os.close(descriptor)

    for suffix in SQLITE_SIDECAR_SUFFIXES:
        _secure_existing_sqlite_artifact(Path(f"{path}{suffix}"))


class OutboxLeaseLost(RuntimeError):
    """Raised when a worker no longer owns an outbox claim."""


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
        _prepare_sqlite_storage(self.path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level="DEFERRED"
        )
        self._connection.row_factory = sqlite3.Row
        self._fts_enabled = True
        try:
            self._initialize()
            self._harden_sqlite_artifacts()
        except Exception:
            self._connection.close()
            raise

    def _harden_sqlite_artifacts(self) -> None:
        _secure_existing_sqlite_artifact(self.path)
        for suffix in SQLITE_SIDECAR_SUFFIXES:
            _secure_existing_sqlite_artifact(Path(f"{self.path}{suffix}"))

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
                """
            )
            version_row = self._connection.execute(
                "SELECT value FROM cache_meta WHERE key = 'schema_version'"
            ).fetchone()
            stored_version = 0
            if version_row is not None:
                try:
                    stored_version = int(version_row["value"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("invalid SQLite schema version") from exc
                if stored_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        "SQLite schema is newer than this Library version: "
                        f"{stored_version} > {SCHEMA_VERSION}"
                    )
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
                    scope TEXT NOT NULL DEFAULT 'project',
                    owner_session_id TEXT,
                    team_id TEXT,
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
                    UNIQUE (namespace, session_id, event_id)
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
                    lease_owner TEXT,
                    lease_until REAL,
                    claim_token TEXT,
                    terminal_at REAL,
                    terminal_error TEXT,
                    PRIMARY KEY (namespace, session_id, event_id),
                    FOREIGN KEY (namespace, session_id, event_id)
                        REFERENCES thread_events(namespace, session_id, event_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS context_outbox_pending
                    ON context_outbox(processed_at, available_at, created_at);
                """
            )
            self._migrate_schema(stored_version)
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS thread_events_recent
                    ON thread_events(namespace, session_id, sequence DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS thread_events_protected
                    ON thread_events(namespace, session_id, protected, sequence DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS thread_events_record_id
                    ON thread_events(namespace, record_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS context_outbox_pending
                    ON context_outbox(processed_at, available_at, created_at)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS records_scope_owner
                    ON records(
                        namespace, scope, owner_session_id, team_id, accessed_at DESC
                    )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS context_outbox_claimable
                    ON context_outbox(
                        processed_at, available_at, lease_until, created_at, sequence
                    )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS context_outbox_ready_v2
                    ON context_outbox(
                        processed_at, terminal_at, available_at, lease_until,
                        created_at, sequence
                    )
                """
            )
            self._initialize_fts()
            self._verify_schema()
            self._connection.commit()

    def _table_columns(self, table: str) -> set[str]:
        rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}

    def _set_schema_version(self, version: int) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO cache_meta(key, value) VALUES('schema_version', ?)",
            (str(version),),
        )

    def _migrate_schema(self, stored_version: int) -> None:
        version = stored_version
        if version < 3:
            self._migrate_record_scope_columns()
            version = 3
        if version < 4:
            self._migrate_event_identity()
            version = 4
        if version < 5:
            self._migrate_outbox_claim_columns()
            version = 5
        if version < 6:
            self._migrate_outbox_terminal_columns()

    def _migrate_record_scope_columns(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._ensure_record_scope_columns()
            self._set_schema_version(3)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _ensure_record_scope_columns(self) -> None:
        record_columns = self._table_columns("records")
        scope_added = "scope" not in record_columns
        if "scope" not in record_columns:
            self._connection.execute(
                "ALTER TABLE records ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'"
            )
        if "owner_session_id" not in record_columns:
            self._connection.execute(
                "ALTER TABLE records ADD COLUMN owner_session_id TEXT"
            )
        if "team_id" not in record_columns:
            self._connection.execute("ALTER TABLE records ADD COLUMN team_id TEXT")
        if scope_added:
            self._classify_legacy_record_scopes()

    def _migrate_outbox_claim_columns(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._ensure_outbox_claim_columns()
            self._set_schema_version(5)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _ensure_outbox_claim_columns(self) -> None:
        outbox_columns = self._table_columns("context_outbox")
        if "lease_owner" not in outbox_columns:
            self._connection.execute(
                "ALTER TABLE context_outbox ADD COLUMN lease_owner TEXT"
            )
        if "lease_until" not in outbox_columns:
            self._connection.execute(
                "ALTER TABLE context_outbox ADD COLUMN lease_until REAL"
            )
        if "claim_token" not in outbox_columns:
            self._connection.execute(
                "ALTER TABLE context_outbox ADD COLUMN claim_token TEXT"
            )

    def _migrate_outbox_terminal_columns(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            outbox_columns = self._table_columns("context_outbox")
            if "terminal_at" not in outbox_columns:
                self._connection.execute(
                    "ALTER TABLE context_outbox ADD COLUMN terminal_at REAL"
                )
            if "terminal_error" not in outbox_columns:
                self._connection.execute(
                    "ALTER TABLE context_outbox ADD COLUMN terminal_error TEXT"
                )
            self._set_schema_version(6)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _classify_legacy_record_scopes(self) -> None:
        rows = self._connection.execute(
            "SELECT namespace, id, metadata_json, source FROM records"
        ).fetchall()
        updates: list[tuple[str, str, str, str]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            source = str(row["source"])
            conversation = metadata.get("kind") == "conversation" or source.startswith(
                "conversation:"
            )
            if not conversation:
                continue
            owner = str(metadata.get("session_id") or "").strip()
            if not owner and source.startswith("conversation:"):
                owner = source.removeprefix("conversation:").strip()
            if not owner:
                owner = f"__migration_unassigned__:{row['id']}"
            updates.append(
                (ContextScope.THREAD.value, owner, row["namespace"], row["id"])
            )
        self._connection.executemany(
            """
            UPDATE records SET scope = ?, owner_session_id = ?
            WHERE namespace = ? AND id = ?
            """,
            updates,
        )

    def _migrate_event_identity(self) -> None:
        old_identity = False
        for index in self._connection.execute(
            "PRAGMA index_list(thread_events)"
        ).fetchall():
            if not bool(index["unique"]):
                continue
            columns = [
                row["name"]
                for row in self._connection.execute(
                    f"PRAGMA index_info({index['name']})"
                ).fetchall()
            ]
            if columns == ["namespace", "event_id"]:
                old_identity = True
                break
        if not old_identity:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._set_schema_version(4)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            return

        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            outbox_columns = self._table_columns("context_outbox")
            lease_owner = "lease_owner" if "lease_owner" in outbox_columns else "NULL"
            lease_until = "lease_until" if "lease_until" in outbox_columns else "NULL"
            claim_token = "claim_token" if "claim_token" in outbox_columns else "NULL"
            terminal_at = "terminal_at" if "terminal_at" in outbox_columns else "NULL"
            terminal_error = (
                "terminal_error" if "terminal_error" in outbox_columns else "NULL"
            )
            self._connection.execute(
                """
                CREATE TABLE thread_events_scoped (
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
                    UNIQUE (namespace, session_id, event_id)
                )
                """
            )
            self._connection.execute(
                "INSERT INTO thread_events_scoped SELECT * FROM thread_events"
            )
            self._connection.execute(
                """
                CREATE TABLE context_outbox_scoped (
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
                    lease_owner TEXT,
                    lease_until REAL,
                    claim_token TEXT,
                    terminal_at REAL,
                    terminal_error TEXT,
                    PRIMARY KEY (namespace, session_id, event_id),
                    FOREIGN KEY (namespace, session_id, event_id)
                        REFERENCES thread_events_scoped(
                            namespace, session_id, event_id
                        ) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                f"""
                INSERT INTO context_outbox_scoped
                SELECT namespace, event_id, session_id, sequence, event_type,
                       created_at, available_at, attempts, processed_at, last_error,
                       {lease_owner}, {lease_until}, {claim_token},
                       {terminal_at}, {terminal_error}
                FROM context_outbox
                """
            )
            self._connection.execute("DROP TABLE context_outbox")
            self._connection.execute("DROP TABLE thread_events")
            self._connection.execute(
                "ALTER TABLE thread_events_scoped RENAME TO thread_events"
            )
            self._connection.execute(
                "ALTER TABLE context_outbox_scoped RENAME TO context_outbox"
            )
            self._set_schema_version(4)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")

    def _verify_schema(self) -> None:
        required_record_columns = {"scope", "owner_session_id", "team_id"}
        required_outbox_columns = {
            "lease_owner",
            "lease_until",
            "claim_token",
            "terminal_at",
            "terminal_error",
        }
        missing_records = required_record_columns - self._table_columns("records")
        missing_outbox = required_outbox_columns - self._table_columns("context_outbox")
        if missing_records or missing_outbox:
            missing = sorted(missing_records | missing_outbox)
            raise RuntimeError(
                "SQLite schema is incomplete: missing " + ", ".join(missing)
            )
        version_row = self._connection.execute(
            "SELECT value FROM cache_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version_row is None or int(version_row["value"]) != SCHEMA_VERSION:
            raise RuntimeError(
                "SQLite schema migration did not reach the target version"
            )
        foreign_key_violations = self._connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise RuntimeError("SQLite schema contains foreign-key violations")

    def _initialize_fts(self) -> None:
        try:
            exists = self._connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'records_fts'
                """
            ).fetchone()
            required = {
                "record_key",
                "namespace",
                "scope",
                "owner_session_id",
                "team_id",
                "text",
            }
            rebuild = exists is None or not required.issubset(
                self._table_columns("records_fts")
            )
            if exists is not None and not rebuild:
                record_count = int(
                    self._connection.execute("SELECT COUNT(*) FROM records").fetchone()[
                        0
                    ]
                )
                fts_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM records_fts"
                    ).fetchone()[0]
                )
                rebuild = record_count != fts_count
            if exists is not None and rebuild:
                self._connection.execute("DROP TABLE records_fts")
            if rebuild:
                self._connection.execute(
                    """
                    CREATE VIRTUAL TABLE records_fts USING fts5(
                        record_key UNINDEXED,
                        namespace UNINDEXED,
                        scope UNINDEXED,
                        owner_session_id UNINDEXED,
                        team_id UNINDEXED,
                        text,
                        tokenize='unicode61'
                    )
                    """
                )
                rows = self._connection.execute(
                    """
                    SELECT namespace, id, scope, owner_session_id, team_id, text
                    FROM records
                    """
                ).fetchall()
                self._connection.executemany(
                    """
                    INSERT INTO records_fts(
                        record_key, namespace, scope, owner_session_id, team_id, text
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            self._record_key(row["namespace"], row["id"]),
                            row["namespace"],
                            row["scope"],
                            row["owner_session_id"],
                            row["team_id"],
                            row["text"],
                        )
                        for row in rows
                    ],
                )
        except sqlite3.OperationalError:
            self._fts_enabled = False

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
            scope=ContextScope(row["scope"]),
            owner_session_id=row["owner_session_id"],
            team_id=row["team_id"],
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

    def _upsert_record_locked(
        self,
        record: ContextRecord,
        *,
        event_identity: tuple[str, str] | None = None,
    ) -> None:
        linked_events = self._connection.execute(
            """
            SELECT session_id, event_id FROM thread_events
            WHERE namespace = ? AND record_id = ?
            """,
            (record.namespace, record.id),
        ).fetchall()
        if event_identity is None:
            if linked_events:
                raise ValueError(
                    "record ID is reserved for an authoritative thread event"
                )
        else:
            if (
                len(linked_events) != 1
                or (
                    str(linked_events[0]["session_id"]),
                    str(linked_events[0]["event_id"]),
                )
                != event_identity
            ):
                raise ValueError(
                    "event-derived record ID does not identify its source event"
                )
            session_id, event_id = event_identity
            if (
                record.scope is not ContextScope.THREAD
                or record.owner_session_id != session_id
                or record.team_id is not None
                or record.metadata.get("kind") != "conversation"
                or record.metadata.get("session_id") != session_id
                or record.metadata.get("event_id") != event_id
            ):
                raise ValueError("event-derived record does not match its source event")

        existing = self._connection.execute(
            "SELECT * FROM records WHERE namespace = ? AND id = ?",
            (record.namespace, record.id),
        ).fetchone()
        if existing is not None and event_identity is not None:
            existing_metadata = json.loads(existing["metadata_json"])
            session_id, event_id = event_identity
            matching_provenance = (
                existing_metadata.get("kind") == "conversation"
                and existing_metadata.get("session_id") == session_id
                and existing_metadata.get("event_id") == event_id
            )
            migratable_legacy_projection = (
                existing_metadata.get("kind") == "conversation"
                and existing_metadata.get("event_id") is None
                and (
                    existing_metadata.get("session_id") == session_id
                    or existing["source"] == f"conversation:{session_id}"
                )
            )
            if (
                existing["scope"] != ContextScope.THREAD.value
                or existing["owner_session_id"] != session_id
                or existing["team_id"] is not None
                or not (matching_provenance or migratable_legacy_projection)
            ):
                raise ValueError("event-derived record ID collides with another record")
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
            record.scope.value,
            record.owner_session_id,
            record.team_id,
        )
        cursor = self._connection.execute(
            """
                INSERT INTO records(
                    namespace, id, text, embedding, embedding_dim, metadata_json,
                    source, importance, token_count, created_at, updated_at,
                    accessed_at, expires_at, content_hash, scope, owner_session_id,
                    team_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                WHERE records.scope = excluded.scope
                  AND records.owner_session_id IS excluded.owner_session_id
                  AND records.team_id IS excluded.team_id
            """,
            params,
        )
        if cursor.rowcount != 1:
            raise ValueError(
                "record visibility cannot be changed by upsert; use a distinct "
                "record ID or an explicit promotion"
            )
        if self._fts_enabled:
            key = self._record_key(record.namespace, record.id)
            self._connection.execute(
                "DELETE FROM records_fts WHERE record_key = ?", (key,)
            )
            self._connection.execute(
                """
                    INSERT INTO records_fts(
                        record_key, namespace, scope, owner_session_id, team_id, text
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    record.namespace,
                    record.scope.value,
                    record.owner_session_id,
                    record.team_id,
                    record.text,
                ),
            )

    def upsert(self, record: ContextRecord) -> ContextRecord:
        with self._lock, self._connection:
            self._upsert_record_locked(record)
            row = self._connection.execute(
                "SELECT * FROM records WHERE namespace = ? AND id = ?",
                (record.namespace, record.id),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"record persistence failed: {record.id}")
        return self._from_row(row)

    def complete_outbox_claim(
        self,
        record: ContextRecord,
        *,
        session_id: str,
        event_id: str,
        indexed_at: float,
        claim_token: str,
    ) -> ContextRecord:
        """Persist an indexed record and complete its live lease atomically."""

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                claim = self._connection.execute(
                    """
                SELECT e.protected, e.record_id
                    FROM context_outbox AS o
                    JOIN thread_events AS e
                      ON e.namespace = o.namespace
                     AND e.session_id = o.session_id
                     AND e.event_id = o.event_id
                    WHERE o.namespace = ? AND o.session_id = ? AND o.event_id = ?
                      AND o.processed_at IS NULL AND o.terminal_at IS NULL
                      AND o.claim_token = ?
                    """,
                    (
                        record.namespace,
                        session_id,
                        event_id,
                        claim_token,
                    ),
                ).fetchone()
                if claim is None:
                    raise OutboxLeaseLost(
                        f"outbox claim no longer belongs to event {event_id}"
                    )
                if claim["record_id"] != record.id:
                    raise ValueError(
                        "indexed record ID does not match its source event"
                    )
                metadata = dict(record.metadata)
                metadata["protected"] = bool(claim["protected"])
                record = replace(record, metadata=metadata)
                self._upsert_record_locked(
                    record,
                    event_identity=(session_id, event_id),
                )
                cursor = self._connection.execute(
                    """
                    UPDATE context_outbox
                    SET processed_at = ?, last_error = NULL,
                        lease_owner = NULL, lease_until = NULL, claim_token = NULL
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                      AND processed_at IS NULL AND terminal_at IS NULL
                      AND claim_token = ?
                    """,
                    (
                        indexed_at,
                        record.namespace,
                        session_id,
                        event_id,
                        claim_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise OutboxLeaseLost(
                        f"outbox claim no longer belongs to event {event_id}"
                    )
                event_cursor = self._connection.execute(
                    """
                    UPDATE thread_events SET indexed_at = COALESCE(indexed_at, ?)
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                    """,
                    (indexed_at, record.namespace, session_id, event_id),
                )
                if event_cursor.rowcount != 1:
                    raise RuntimeError(
                        f"indexed event has no durable source event: {event_id}"
                    )
                row = self._connection.execute(
                    "SELECT * FROM records WHERE namespace = ? AND id = ?",
                    (record.namespace, record.id),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"record persistence failed: {record.id}")
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._from_row(row)

    def get(self, namespace: str, record_id: str) -> ContextRecord | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM records WHERE namespace = ? AND id = ?",
                (namespace, record_id),
            ).fetchone()
            if row is None:
                return None
            record = self._from_row(row)
            if record.expires_at is not None and record.expires_at <= time.time():
                self._connection.execute(
                    "DELETE FROM records WHERE namespace = ? AND id = ?",
                    (namespace, record_id),
                )
                if self._fts_enabled:
                    self._connection.execute(
                        "DELETE FROM records_fts WHERE record_key = ?",
                        (self._record_key(namespace, record_id),),
                    )
                return None
            return record

    def get_visible(
        self,
        namespace: str,
        record_id: str,
        *,
        selection: ScopeSelection,
    ) -> ContextRecord | None:
        """Read one record only when it belongs to an authorized scope."""

        predicate, scope_params = self._scope_predicate(selection)
        with self._lock, self._connection:
            row = self._connection.execute(
                f"""
                SELECT * FROM records
                WHERE namespace = ? AND id = ? AND {predicate}
                """,
                (namespace, record_id, *scope_params),
            ).fetchone()
            if row is None:
                return None
            record = self._from_row(row)
            if record.expires_at is not None and record.expires_at <= time.time():
                self._connection.execute(
                    "DELETE FROM records WHERE namespace = ? AND id = ?",
                    (namespace, record_id),
                )
                if self._fts_enabled:
                    self._connection.execute(
                        "DELETE FROM records_fts WHERE record_key = ?",
                        (self._record_key(namespace, record_id),),
                    )
                return None
            return record

    @staticmethod
    def _scope_predicate(
        selection: ScopeSelection,
        *,
        alias: str = "",
    ) -> tuple[str, list[str]]:
        prefix = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[str] = []
        for scope in selection.scopes:
            if scope is ContextScope.THREAD:
                clauses.append(f"({prefix}scope = ? AND {prefix}owner_session_id = ?)")
                params.extend((scope.value, str(selection.session_id)))
            elif scope is ContextScope.TEAM:
                placeholders = ",".join("?" for _ in selection.team_ids)
                clauses.append(
                    f"({prefix}scope = ? AND {prefix}team_id IN ({placeholders}))"
                )
                params.append(scope.value)
                params.extend(sorted(selection.team_ids))
            else:
                clauses.append(f"{prefix}scope = ?")
                params.append(scope.value)
        return "(" + " OR ".join(clauses) + ")", params

    def list_records(
        self,
        namespace: str,
        *,
        selection: ScopeSelection | None = None,
    ) -> list[ContextRecord]:
        now = time.time()
        resolved = selection or ScopeSelection.resolve()
        predicate, scope_params = self._scope_predicate(resolved)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM records
                WHERE namespace = ? AND (expires_at IS NULL OR expires_at > ?)
                  AND {predicate}
                """,
                (namespace, now, *scope_params),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_source_records(
        self,
        namespace: str,
        source: str,
        *,
        limit: int | None = None,
        selection: ScopeSelection | None = None,
    ) -> list[ContextRecord]:
        now = time.time()
        predicate = ""
        scope_params: list[str] = []
        if selection is not None:
            predicate, scope_params = self._scope_predicate(selection)
        sql = """
            SELECT * FROM records
            WHERE namespace = ? AND source = ?
              AND (expires_at IS NULL OR expires_at > ?)
        """
        params: tuple[Any, ...] = (namespace, source, now)
        if selection is not None:
            sql += f" AND {predicate}"
            params += tuple(scope_params)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params += (max(0, limit),)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in reversed(rows)]

    def count_source(
        self,
        namespace: str,
        source: str,
        *,
        selection: ScopeSelection | None = None,
    ) -> int:
        now = time.time()
        predicate = ""
        scope_params: list[str] = []
        if selection is not None:
            predicate, scope_params = self._scope_predicate(selection)
        sql = """
            SELECT COUNT(*) AS count FROM records
            WHERE namespace = ? AND source = ?
              AND (expires_at IS NULL OR expires_at > ?)
        """
        params: tuple[Any, ...] = (namespace, source, now)
        if selection is not None:
            sql += f" AND {predicate}"
            params += tuple(scope_params)
        with self._lock:
            row = self._connection.execute(sql, params).fetchone()
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
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                    """,
                    (namespace, session_id, event_id),
                ).fetchone()
                if existing is not None:
                    event = self._event_from_row(existing)
                    if (
                        event.session_id != session_id
                        or event.role != role
                        or event.content != content
                        or event.metadata != metadata
                        or event.importance != importance
                        or event.protected is not protected
                        or event.token_count != token_count
                        or event.record_id != record_id
                    ):
                        raise ValueError(
                            "event_id already belongs to a different thread event"
                        )
                    self._connection.commit()
                    return event

                linked_event = self._connection.execute(
                    """
                    SELECT session_id, event_id FROM thread_events
                    WHERE namespace = ? AND record_id = ?
                    LIMIT 1
                    """,
                    (namespace, record_id),
                ).fetchone()
                if linked_event is not None:
                    raise ValueError(
                        "record ID already belongs to another thread event"
                    )
                linked_record = self._connection.execute(
                    "SELECT 1 FROM records WHERE namespace = ? AND id = ?",
                    (namespace, record_id),
                ).fetchone()
                if linked_record is not None:
                    raise ValueError("thread event record ID collides with a record")

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

    def get_thread_event(
        self,
        namespace: str,
        session_id: str,
        event_id: str,
    ) -> ContextEvent | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM thread_events
                WHERE namespace = ? AND session_id = ? AND event_id = ?
                """,
                (namespace, session_id, event_id),
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

    def iter_thread_events_descending(
        self,
        namespace: str,
        session_id: str,
        *,
        protected_only: bool = False,
        page_size: int = 128,
    ) -> Iterator[ContextEvent]:
        """Yield newest events first through bounded SQLite result pages."""

        if not 1 <= page_size <= 1024:
            raise ValueError("page_size must be between 1 and 1024")
        before_sequence: int | None = None
        while True:
            sql = """
                SELECT * FROM thread_events
                WHERE namespace = ? AND session_id = ?
            """
            params: tuple[Any, ...] = (namespace, session_id)
            if protected_only:
                sql += " AND protected = 1"
            if before_sequence is not None:
                sql += " AND sequence < ?"
                params += (before_sequence,)
            sql += " ORDER BY sequence DESC LIMIT ?"
            params += (page_size,)
            with self._lock:
                rows = self._connection.execute(sql, params).fetchall()
            if not rows:
                return
            for row in rows:
                yield self._event_from_row(row)
            if len(rows) < page_size:
                return
            before_sequence = int(rows[-1]["sequence"])

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
    ) -> tuple[bool, ContextRecord | None]:
        """Set event protection and synchronize its indexed record atomically."""

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                event = self._connection.execute(
                    """
                    SELECT record_id FROM thread_events
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                    """,
                    (namespace, session_id, event_id),
                ).fetchone()
                if event is None:
                    self._connection.commit()
                    return False, None
                self._connection.execute(
                    """
                    UPDATE thread_events SET protected = ?
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                    """,
                    (int(protected), namespace, session_id, event_id),
                )
                record = self._connection.execute(
                    """
                    SELECT * FROM records
                    WHERE namespace = ? AND id = ?
                    """,
                    (namespace, event["record_id"]),
                ).fetchone()
                persisted: ContextRecord | None = None
                if record is not None:
                    metadata = json.loads(record["metadata_json"])
                    is_event_projection = (
                        record["scope"] == ContextScope.THREAD.value
                        and record["owner_session_id"] == session_id
                        and record["team_id"] is None
                        and metadata.get("kind") == "conversation"
                        and metadata.get("session_id") == session_id
                        and metadata.get("event_id") == event_id
                    )
                    if is_event_projection:
                        metadata["protected"] = bool(protected)
                        updated_at = time.time()
                        self._connection.execute(
                            """
                            UPDATE records
                            SET metadata_json = ?, updated_at = ?
                            WHERE namespace = ? AND id = ?
                            """,
                            (
                                json.dumps(
                                    metadata,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                updated_at,
                                namespace,
                                event["record_id"],
                            ),
                        )
                        synchronized = self._connection.execute(
                            """
                            SELECT * FROM records
                            WHERE namespace = ? AND id = ?
                            """,
                            (namespace, event["record_id"]),
                        ).fetchone()
                        if synchronized is None:
                            raise RuntimeError(
                                f"protected event record disappeared: {event_id}"
                            )
                        persisted = self._from_row(synchronized)
                    else:
                        persisted = self._from_row(record)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return True, persisted

    def pending_outbox_event_ids(self, *, limit: int = 128) -> list[tuple[str, str]]:
        now = time.time()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT namespace, event_id FROM context_outbox
                WHERE processed_at IS NULL AND terminal_at IS NULL
                  AND available_at <= ?
                ORDER BY created_at, sequence
                LIMIT ?
                """,
                (now, max(1, limit)),
            ).fetchall()
        return [(row["namespace"], row["event_id"]) for row in rows]

    def claim_outbox_events(
        self,
        owner: str,
        *,
        limit: int = 128,
        lease_seconds: float = 30.0,
    ) -> list[OutboxClaim]:
        """Lease eligible thread heads for bounded, ordered indexing."""

        if not owner.strip():
            raise ValueError("outbox lease owner cannot be empty")
        now = time.time()
        lease_until = now + max(1.0, lease_seconds)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                rows = self._connection.execute(
                    """
                    SELECT o.namespace, o.event_id, o.session_id, o.sequence, o.attempts
                    FROM context_outbox AS o
                    WHERE o.processed_at IS NULL
                      AND o.terminal_at IS NULL
                      AND o.available_at <= ?
                      AND (
                          o.lease_owner IS NULL
                          OR (o.lease_owner <> ? AND o.lease_until <= ?)
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM context_outbox AS earlier
                          WHERE earlier.namespace = o.namespace
                            AND earlier.session_id = o.session_id
                            AND earlier.processed_at IS NULL
                            AND earlier.terminal_at IS NULL
                            AND earlier.sequence < o.sequence
                      )
                    ORDER BY o.created_at, o.sequence
                    LIMIT ?
                    """,
                    (now, owner, now, max(1, limit)),
                ).fetchall()
                claims = []
                for row in rows:
                    claims.append(
                        OutboxClaim(
                            namespace=row["namespace"],
                            event_id=row["event_id"],
                            session_id=row["session_id"],
                            sequence=int(row["sequence"]),
                            attempts=int(row["attempts"]),
                            claim_token=uuid.uuid4().hex,
                        )
                    )
                self._connection.executemany(
                    """
                    UPDATE context_outbox
                    SET lease_owner = ?, lease_until = ?, claim_token = ?
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                      AND processed_at IS NULL
                      AND terminal_at IS NULL
                      AND (
                          lease_owner IS NULL
                          OR (lease_owner <> ? AND lease_until <= ?)
                      )
                    """,
                    [
                        (
                            owner,
                            lease_until,
                            claim.claim_token,
                            claim.namespace,
                            claim.session_id,
                            claim.event_id,
                            owner,
                            now,
                        )
                        for claim in claims
                    ],
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return claims

    def release_outbox_leases(self, owner: str) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE context_outbox
                SET lease_owner = NULL, lease_until = NULL, claim_token = NULL
                WHERE lease_owner = ? AND processed_at IS NULL
                """,
                (owner,),
            )
        return cursor.rowcount

    def release_outbox_claim(
        self,
        namespace: str,
        session_id: str,
        event_id: str,
        *,
        claim_token: str,
    ) -> bool:
        """Release one uncompleted claim only when its fencing token matches."""

        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE context_outbox
                SET lease_owner = NULL, lease_until = NULL, claim_token = NULL
                WHERE namespace = ? AND session_id = ? AND event_id = ?
                  AND claim_token = ? AND processed_at IS NULL
                """,
                (namespace, session_id, event_id, claim_token),
            )
        return cursor.rowcount == 1

    def mark_thread_event_indexed(
        self,
        namespace: str,
        session_id: str,
        event_id: str,
        *,
        indexed_at: float,
        claim_token: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            if claim_token is not None:
                cursor = self._connection.execute(
                    """
                    UPDATE context_outbox
                    SET processed_at = ?, last_error = NULL,
                        lease_owner = NULL, lease_until = NULL, claim_token = NULL
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                      AND processed_at IS NULL AND terminal_at IS NULL
                      AND claim_token = ?
                    """,
                    (
                        indexed_at,
                        namespace,
                        session_id,
                        event_id,
                        claim_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise OutboxLeaseLost(
                        f"outbox claim no longer belongs to event {event_id}"
                    )
            self._connection.execute(
                """
                UPDATE thread_events SET indexed_at = COALESCE(indexed_at, ?)
                WHERE namespace = ? AND session_id = ? AND event_id = ?
                """,
                (indexed_at, namespace, session_id, event_id),
            )
            if claim_token is None:
                self._connection.execute(
                    """
                    UPDATE context_outbox
                    SET processed_at = ?, last_error = NULL,
                        lease_owner = NULL, lease_until = NULL, claim_token = NULL
                    WHERE namespace = ? AND session_id = ? AND event_id = ?
                    """,
                    (indexed_at, namespace, session_id, event_id),
                )

    def fail_outbox_event(
        self,
        namespace: str,
        session_id: str,
        event_id: str,
        *,
        error: str,
        retry_after_seconds: float,
        claim_token: str | None = None,
        terminal: bool = False,
    ) -> None:
        with self._lock, self._connection:
            token_predicate = "" if claim_token is None else " AND claim_token = ?"
            error_text = error[:2000]
            terminal_at = time.time() if terminal else None
            params: tuple[Any, ...] = (
                error_text,
                time.time() + max(0.01, retry_after_seconds),
                terminal_at,
                error_text if terminal else None,
                namespace,
                session_id,
                event_id,
            )
            if claim_token is not None:
                params += (claim_token,)
            cursor = self._connection.execute(
                f"""
                UPDATE context_outbox
                SET attempts = attempts + 1, last_error = ?, available_at = ?,
                    terminal_at = ?, terminal_error = ?,
                    lease_owner = NULL, lease_until = NULL, claim_token = NULL
                WHERE namespace = ? AND session_id = ? AND event_id = ?
                  AND processed_at IS NULL
                  {token_predicate}
                """,
                params,
            )
            if claim_token is not None and cursor.rowcount != 1:
                raise OutboxLeaseLost(
                    f"outbox claim no longer belongs to event {event_id}"
                )

    def failed_outbox_events(
        self,
        namespace: str,
        session_id: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_id, sequence, attempts, terminal_at, terminal_error
                FROM context_outbox
                WHERE namespace = ? AND session_id = ? AND terminal_at IS NOT NULL
                ORDER BY sequence
                LIMIT ?
                """,
                (namespace, session_id, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def retry_failed_outbox_event(
        self,
        namespace: str,
        session_id: str,
        event_id: str,
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE context_outbox
                SET attempts = 0, available_at = ?, last_error = NULL,
                    terminal_at = NULL, terminal_error = NULL,
                    lease_owner = NULL, lease_until = NULL, claim_token = NULL
                WHERE namespace = ? AND session_id = ? AND event_id = ?
                  AND processed_at IS NULL AND terminal_at IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM context_outbox AS active
                      WHERE active.namespace = context_outbox.namespace
                        AND active.session_id = context_outbox.session_id
                        AND active.claim_token IS NOT NULL
                        AND active.event_id <> context_outbox.event_id
                  )
                """,
                (time.time(), namespace, session_id, event_id),
            )
        return cursor.rowcount == 1

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
                  AND terminal_at IS NULL
                """,
                (namespace, session_id),
            ).fetchone()
            failed = self._connection.execute(
                """
                SELECT COUNT(*) AS count FROM context_outbox
                WHERE namespace = ? AND session_id = ? AND terminal_at IS NOT NULL
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
            failed_events=int(failed["count"]),
        )

    def lexical_search(
        self,
        namespace: str,
        query: str,
        *,
        limit: int = 64,
        selection: ScopeSelection | None = None,
    ) -> list[tuple[str, float]]:
        tokens = list(
            dict.fromkeys(token.lower() for token in TOKEN_RE.findall(query))
        )[:24]
        if not tokens:
            return []
        now = time.time()
        resolved = selection or ScopeSelection.resolve()
        if self._fts_enabled:
            match = " OR ".join(
                f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
            )
            try:
                predicate, scope_params = self._scope_predicate(resolved, alias="f")
                with self._lock:
                    rows = self._connection.execute(
                        f"""
                        SELECT f.record_key
                        FROM records_fts AS f
                        WHERE records_fts MATCH ? AND f.namespace = ?
                          AND {predicate}
                        ORDER BY bm25(records_fts)
                        LIMIT ?
                        """,
                        (match, namespace, *scope_params, limit),
                    ).fetchall()
                return [
                    (row["record_key"].split("\x1f", 1)[1], 1.0 / (1.0 + rank))
                    for rank, row in enumerate(rows)
                ]
            except sqlite3.OperationalError:
                pass
        pattern = "%" + "%".join(tokens[:4]) + "%"
        predicate, scope_params = self._scope_predicate(resolved)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT id FROM records
                WHERE namespace = ? AND lower(text) LIKE ?
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND {predicate}
                ORDER BY accessed_at DESC LIMIT ?
                """,
                (namespace, pattern, now, *scope_params, limit),
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

    def delete_source(
        self,
        namespace: str,
        source: str,
        *,
        selection: ScopeSelection | None = None,
    ) -> list[str]:
        predicate = ""
        scope_params: list[str] = []
        if selection is not None:
            predicate, scope_params = self._scope_predicate(selection)
        where = "namespace = ? AND source = ?"
        params: tuple[Any, ...] = (namespace, source)
        if selection is not None:
            where += f" AND {predicate}"
            params += tuple(scope_params)
        with self._lock, self._connection:
            rows = self._connection.execute(
                f"SELECT id FROM records WHERE {where}",
                params,
            ).fetchall()
            ids = [row["id"] for row in rows]
            self._connection.execute(
                f"DELETE FROM records WHERE {where}",
                params,
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
                SELECT
                    COALESCE(SUM(CASE WHEN terminal_at IS NULL THEN 1 ELSE 0 END), 0)
                        AS pending,
                    COALESCE(SUM(CASE WHEN terminal_at IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS failed
                FROM context_outbox
                WHERE namespace = ? AND processed_at IS NULL
                """,
                (namespace,),
            ).fetchone()
            scope_rows = self._connection.execute(
                """
                SELECT scope, COUNT(*) AS count
                FROM records WHERE namespace = ?
                GROUP BY scope
                """,
                (namespace,),
            ).fetchall()
        return {
            "path": str(self.path.resolve()),
            "records": row["records"],
            "text_bytes": row["text_bytes"],
            "vector_bytes": row["vector_bytes"],
            "fts_enabled": self._fts_enabled,
            "thread_events": event_row["events"],
            "unindexed_events": event_row["unindexed_events"],
            "pending_outbox": outbox_row["pending"],
            "failed_outbox": outbox_row["failed"],
            "records_by_scope": {row["scope"]: int(row["count"]) for row in scope_rows},
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
