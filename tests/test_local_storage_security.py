from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from context_cache.process_lock import DatabaseRuntimeLock
from context_cache.store import SQLiteStore


class LocalStorageSecurityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX mode bits required")
    def test_sqlite_database_and_live_sidecars_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            store = SQLiteStore(database)
            try:
                artifacts = (
                    database,
                    Path(f"{database}-wal"),
                    Path(f"{database}-shm"),
                )
                for artifact in artifacts:
                    with self.subTest(artifact=artifact.name):
                        self.assertTrue(artifact.is_file())
                        self.assertEqual(
                            stat.S_IMODE(artifact.stat().st_mode),
                            0o600,
                        )
            finally:
                store.close()

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits required")
    def test_existing_database_permissions_are_constrained_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE sample(value TEXT)")
            connection.commit()
            connection.close()
            database.chmod(0o644)

            store = SQLiteStore(database)
            try:
                self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
            finally:
                store.close()

    def test_symbolic_link_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.sqlite"
            database = root / "library.sqlite"
            target.touch()
            try:
                database.symlink_to(target)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                SQLiteStore(database)

    def test_symbolic_link_wal_is_rejected_without_writing_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "library.sqlite"
            sqlite3.connect(database).close()
            target = root / "unrelated.txt"
            target.write_text("unchanged", encoding="utf-8")
            wal = Path(f"{database}-wal")
            try:
                wal.symlink_to(target)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                SQLiteStore(database)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits required")
    def test_database_owner_lock_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            lock = DatabaseRuntimeLock(database)
            try:
                self.assertEqual(stat.S_IMODE(lock.path.stat().st_mode), 0o600)
            finally:
                lock.close()

    def test_symbolic_link_database_owner_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "library.sqlite"
            target = root / "target.lock"
            target.touch()
            lock_path = Path(f"{database}.daemon.lock")
            try:
                lock_path.symlink_to(target)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                DatabaseRuntimeLock(database)


if __name__ == "__main__":
    unittest.main()
