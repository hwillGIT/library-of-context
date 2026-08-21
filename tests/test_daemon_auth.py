from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from context_cache.daemon_auth import (
    load_or_create_daemon_token,
    read_daemon_token,
    validate_daemon_token,
)


class DaemonTokenFileTests(unittest.TestCase):
    def test_token_file_is_created_once_and_read_consistently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "daemon.token"
            first = load_or_create_daemon_token(path)
            second = load_or_create_daemon_token(path)

            self.assertEqual(first, second)
            self.assertEqual(read_daemon_token(path), first)
            self.assertGreaterEqual(len(first), 32)

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits required")
    def test_created_token_file_is_accessible_only_to_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.token"
            load_or_create_daemon_token(path)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits required")
    def test_existing_token_file_rejects_group_or_other_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.token"
            path.write_text("x" * 32 + "\n", encoding="utf-8")
            path.chmod(0o640)

            with self.assertRaisesRegex(RuntimeError, "group or other access"):
                read_daemon_token(path)
            with self.assertRaisesRegex(RuntimeError, "group or other access"):
                load_or_create_daemon_token(path)

    def test_symbolic_link_token_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.token"
            link = root / "daemon.token"
            target.write_text("x" * 32 + "\n", encoding="utf-8")
            if os.name == "posix":
                target.chmod(0o600)
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                read_daemon_token(link)
            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                load_or_create_daemon_token(link)

    def test_hard_link_token_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.token"
            link = root / "daemon.token"
            target.write_text("x" * 32 + "\n", encoding="utf-8")
            if os.name == "posix":
                target.chmod(0o600)
            try:
                os.link(target, link)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "one link"):
                read_daemon_token(link)

    def test_token_validation_rejects_weak_or_whitespace_values(self) -> None:
        for token in ("short", "x" * 31, "x" * 32 + "\n", "x" * 31 + " "):
            with self.subTest(token=repr(token)):
                with self.assertRaises(ValueError):
                    validate_daemon_token(token)

    def test_missing_token_file_reports_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.token"
            with self.assertRaisesRegex(RuntimeError, "missing.token"):
                read_daemon_token(path)


if __name__ == "__main__":
    unittest.main()
