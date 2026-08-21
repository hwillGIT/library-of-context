from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_cache.store import SCHEMA_VERSION, SQLiteStore
from library_of_context import ContextScope, LibraryOfContext, ScopeSelection


def _create_v2_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE cache_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO cache_meta VALUES('schema_version', '2');
        CREATE TABLE records(
            namespace TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL,
            embedding BLOB NOT NULL, embedding_dim INTEGER NOT NULL,
            metadata_json TEXT NOT NULL, source TEXT NOT NULL,
            importance REAL NOT NULL, token_count INTEGER NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            accessed_at REAL NOT NULL, expires_at REAL, content_hash TEXT NOT NULL,
            PRIMARY KEY(namespace, id)
        );
        CREATE TABLE thread_heads(
            namespace TEXT NOT NULL, session_id TEXT NOT NULL,
            last_sequence INTEGER NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(namespace, session_id)
        );
        CREATE TABLE thread_events(
            namespace TEXT NOT NULL, session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, event_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, metadata_json TEXT NOT NULL,
            importance REAL NOT NULL, protected INTEGER NOT NULL,
            token_count INTEGER NOT NULL, record_id TEXT NOT NULL,
            created_at REAL NOT NULL, indexed_at REAL,
            PRIMARY KEY(namespace, session_id, sequence),
            UNIQUE(namespace, event_id)
        );
        CREATE TABLE context_outbox(
            namespace TEXT NOT NULL, event_id TEXT NOT NULL,
            session_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL, created_at REAL NOT NULL,
            available_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            processed_at REAL, last_error TEXT,
            PRIMARY KEY(namespace, event_id),
            FOREIGN KEY(namespace, event_id)
                REFERENCES thread_events(namespace, event_id)
        );
        """
    )
    records = (
        (
            "default",
            "manual",
            "Project handbook entry.",
            b"",
            0,
            "{}",
            "handbook",
            0.5,
            5,
            1.0,
            1.0,
            1.0,
            None,
            "manual-hash",
        ),
        (
            "default",
            "conversation",
            "Private migrated conversation.",
            b"",
            0,
            json.dumps({"kind": "conversation", "session_id": "thread-a"}),
            "conversation:thread-a",
            0.5,
            5,
            1.0,
            1.0,
            1.0,
            None,
            "conversation-hash",
        ),
        (
            "default",
            "ambiguous-conversation",
            "Private conversation with no recoverable owner.",
            b"",
            0,
            json.dumps({"kind": "conversation"}),
            "conversation",
            0.5,
            7,
            1.0,
            1.0,
            1.0,
            None,
            "ambiguous-hash",
        ),
    )
    connection.executemany(
        "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        records,
    )
    connection.execute(
        "INSERT INTO thread_heads VALUES ('default', 'thread-a', 1, 1.0)"
    )
    connection.execute(
        """
        INSERT INTO thread_events VALUES(
            'default', 'thread-a', 1, 'turn-1', 'user', 'private', '{}',
            0.5, 0, 1, 'conversation', 1.0, NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO context_outbox VALUES(
            'default', 'turn-1', 'thread-a', 1, 'index', 1.0, 1.0, 0, NULL, NULL
        )
        """
    )
    connection.commit()
    connection.close()


class SchemaMigrationTests(unittest.TestCase):
    @staticmethod
    def _counts(path: Path) -> dict[str, int]:
        connection = sqlite3.connect(path)
        try:
            return {
                "records": connection.execute(
                    "SELECT COUNT(*) FROM records"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM thread_events"
                ).fetchone()[0],
                "outbox": connection.execute(
                    "SELECT COUNT(*) FROM context_outbox"
                ).fetchone()[0],
            }
        finally:
            connection.close()

    @staticmethod
    def _schema_v2_reader(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "schema_v2_reader.py"),
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_v2_orphaned_outbox_row_is_rejected_by_schema_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orphaned-v2.sqlite"
            _create_v2_database(path)
            connection = sqlite3.connect(path)
            connection.execute(
                """
                INSERT INTO context_outbox VALUES(
                    'default', 'orphan', 'missing-thread', 1, 'index',
                    2.0, 2.0, 0, NULL, NULL
                )
                """
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(RuntimeError, "foreign-key"):
                SQLiteStore(path)

    def test_migrated_pending_event_reindexes_its_legacy_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite"
            _create_v2_database(path)

            with LibraryOfContext(path, redis_url="") as library:
                governor = library.open_context_governor("thread-a")
                self.assertTrue(governor.flush(timeout=2.0))
                record = library.store.get("default", "conversation")
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.metadata["event_id"], "turn-1")
                self.assertEqual(record.metadata["session_id"], "thread-a")
                self.assertEqual(record.text, "private")

    def test_v2_records_and_event_identity_migrate_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite"
            backup = Path(directory) / "v2.backup.sqlite"
            _create_v2_database(path)
            baseline = self._counts(path)
            shutil.copy2(path, backup)
            source_read = self._schema_v2_reader(backup)
            self.assertEqual(source_read.returncode, 0, source_read.stderr)
            self.assertEqual(json.loads(source_read.stdout), baseline)

            with LibraryOfContext(path, redis_url="") as library:
                manual = library.store.get("default", "manual")
                conversation = library.store.get("default", "conversation")
                ambiguous = library.store.get("default", "ambiguous-conversation")
                assert (
                    manual is not None
                    and conversation is not None
                    and ambiguous is not None
                )
                self.assertEqual(manual.scope, ContextScope.PROJECT)
                self.assertEqual(conversation.scope, ContextScope.THREAD)
                self.assertEqual(conversation.owner_session_id, "thread-a")
                self.assertEqual(ambiguous.scope, ContextScope.THREAD)
                self.assertEqual(
                    ambiguous.owner_session_id,
                    "__migration_unassigned__:ambiguous-conversation",
                )
                self.assertNotIn(
                    conversation.id,
                    [hit.record.id for hit in library.consult("private migrated")],
                )
                private_hits = library.retrieve(
                    "private migrated conversation",
                    scopes=(ContextScope.THREAD,),
                    session_id="thread-a",
                )
                self.assertIn(
                    conversation.id,
                    [hit.record.id for hit in private_hits],
                )
                self.assertTrue(library.store.fts_enabled)
                owner_fts = library.store.lexical_search(
                    "default",
                    "private migrated conversation",
                    limit=10,
                    selection=ScopeSelection.for_thread("thread-a"),
                )
                project_fts = library.store.lexical_search(
                    "default",
                    "private migrated conversation",
                    limit=10,
                    selection=ScopeSelection.resolve((ContextScope.PROJECT,)),
                )
                self.assertIn(conversation.id, dict(owner_fts))
                self.assertNotIn(conversation.id, dict(project_fts))
                self.assertNotIn(
                    ambiguous.id,
                    [hit.record.id for hit in library.consult("no recoverable owner")],
                )
                self.assertEqual(self._counts(path), baseline)
                pending = library.store.pending_outbox_event_ids()
                self.assertIn(("default", "turn-1"), pending)

                other = library.open_context_governor("thread-b", start_worker=False)
                self.assertEqual(
                    other.record("user", "Other thread.", event_id="turn-1").sequence,
                    1,
                )

            connection = sqlite3.connect(path)
            version = connection.execute(
                "SELECT value FROM cache_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            self.assertEqual(int(version), SCHEMA_VERSION)
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT content, record_id FROM thread_events
                    WHERE namespace = 'default' AND session_id = 'thread-a'
                      AND event_id = 'turn-1'
                    """
                ).fetchone(),
                ("private", "conversation"),
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT processed_at FROM context_outbox
                    WHERE namespace = 'default' AND session_id = 'thread-a'
                      AND event_id = 'turn-1'
                    """
                ).fetchone(),
                (None,),
            )
            connection.close()

            target_read = self._schema_v2_reader(path)
            self.assertEqual(target_read.returncode, 2)
            self.assertIn("rejects", target_read.stderr)
            source_read = self._schema_v2_reader(backup)
            self.assertEqual(source_read.returncode, 0, source_read.stderr)

            with LibraryOfContext(path, redis_url="") as reopened:
                reopened_conversation = reopened.store.get("default", "conversation")
                assert reopened_conversation is not None
                self.assertEqual(reopened_conversation.scope, ContextScope.THREAD)
                self.assertEqual(reopened_conversation.owner_session_id, "thread-a")

    def test_migration_is_idempotent_and_future_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite"
            _create_v2_database(path)
            SQLiteStore(path).close()
            SQLiteStore(path).close()

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE cache_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION + 1),),
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "newer"):
                SQLiteStore(path)

    def test_interrupted_stage_resumes_from_its_durable_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite"
            _create_v2_database(path)

            with patch.object(
                SQLiteStore,
                "_migrate_event_identity",
                side_effect=RuntimeError("injected migration interruption"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    SQLiteStore(path)

            connection = sqlite3.connect(path)
            version = connection.execute(
                "SELECT value FROM cache_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            record_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(records)")
            }
            self.assertEqual(int(version), 3)
            self.assertTrue(
                {"scope", "owner_session_id", "team_id"}.issubset(record_columns)
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name LIKE '%_scoped'"
                ).fetchall(),
                [],
            )
            connection.close()

            SQLiteStore(path).close()
            connection = sqlite3.connect(path)
            version = connection.execute(
                "SELECT value FROM cache_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            self.assertEqual(int(version), SCHEMA_VERSION)
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            connection.close()

    def test_each_outbox_migration_stage_resumes_from_its_durable_version(
        self,
    ) -> None:
        stages = (
            ("_migrate_outbox_claim_columns", 4),
            ("_migrate_outbox_terminal_columns", 5),
        )
        for method_name, expected_version in stages:
            with self.subTest(stage=method_name):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "v2.sqlite"
                    _create_v2_database(path)

                    with patch.object(
                        SQLiteStore,
                        method_name,
                        side_effect=RuntimeError("injected migration interruption"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "injected"):
                            SQLiteStore(path)

                    connection = sqlite3.connect(path)
                    version = connection.execute(
                        "SELECT value FROM cache_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                    self.assertEqual(int(version), expected_version)
                    self.assertEqual(
                        connection.execute(
                            "SELECT name FROM sqlite_master WHERE name LIKE '%_scoped'"
                        ).fetchall(),
                        [],
                    )
                    connection.close()

                    SQLiteStore(path).close()
                    connection = sqlite3.connect(path)
                    version = connection.execute(
                        "SELECT value FROM cache_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                    self.assertEqual(int(version), SCHEMA_VERSION)
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM context_outbox "
                            "WHERE processed_at IS NULL"
                        ).fetchone(),
                        (1,),
                    )
                    connection.close()

    def test_fts_population_recovers_after_interrupted_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite"
            _create_v2_database(path)

            with patch.object(
                SQLiteStore,
                "_verify_schema",
                side_effect=RuntimeError("injected post-FTS interruption"),
            ):
                with self.assertRaisesRegex(RuntimeError, "post-FTS"):
                    SQLiteStore(path)

            connection = sqlite3.connect(path)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM records").fetchone(),
                (3,),
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM records_fts").fetchone(),
                (0,),
            )
            connection.close()

            store = SQLiteStore(path)
            try:
                self.assertEqual(
                    store.lexical_search(
                        "default",
                        "Project handbook",
                        limit=10,
                        selection=ScopeSelection.resolve((ContextScope.PROJECT,)),
                    ),
                    [("manual", 1.0)],
                )
                connection = sqlite3.connect(path)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM records_fts").fetchone(),
                    (3,),
                )
                connection.close()
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
