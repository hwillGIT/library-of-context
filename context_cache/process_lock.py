from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO


def _secure_open_flags(flags: int) -> int:
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    return flags


def _validate_lock_file(path: Path, descriptor: int) -> None:
    try:
        path_status = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"cannot inspect database owner lock {path}: {exc}") from exc
    if stat.S_ISLNK(path_status.st_mode):
        raise RuntimeError(f"database owner lock cannot be a symbolic link: {path}")

    opened_status = os.fstat(descriptor)
    if not stat.S_ISREG(opened_status.st_mode):
        raise RuntimeError(f"database owner lock is not a regular file: {path}")
    if (opened_status.st_dev, opened_status.st_ino) != (
        path_status.st_dev,
        path_status.st_ino,
    ):
        raise RuntimeError(f"database owner lock changed while opening: {path}")
    if opened_status.st_nlink != 1:
        raise RuntimeError(f"database owner lock must have one link: {path}")
    if os.name == "posix":
        if opened_status.st_uid != os.geteuid():
            raise RuntimeError(
                f"database owner lock is not owned by the current user: {path}"
            )
        os.fchmod(descriptor, 0o600)


def _open_lock_file(path: Path) -> int:
    flags = _secure_open_flags(os.O_RDWR)
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            path_status = os.lstat(path)
        except OSError as exc:
            raise RuntimeError(
                f"cannot inspect database owner lock {path}: {exc}"
            ) from exc
        if stat.S_ISLNK(path_status.st_mode):
            raise RuntimeError(
                f"database owner lock cannot be a symbolic link: {path}"
            ) from None
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise RuntimeError(
                f"cannot open database owner lock {path}: {exc}"
            ) from exc
    try:
        _validate_lock_file(path, descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


class DatabaseRuntimeLock:
    """Hold the single process-owner lock for a Library SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        database = Path(os.path.abspath(os.fspath(database_path)))
        database.parent.mkdir(parents=True, exist_ok=True)
        self.path = database.with_name(database.name + ".daemon.lock")
        descriptor = _open_lock_file(self.path)
        try:
            self._file: BinaryIO | None = os.fdopen(descriptor, "r+b", buffering=0)
        except Exception:
            os.close(descriptor)
            raise
        try:
            self._ensure_lock_byte()
            self._lock()
        except Exception:
            self._file.close()
            self._file = None
            raise

    def _ensure_lock_byte(self) -> None:
        assert self._file is not None
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"\0")
        self._file.seek(0)

    def _lock(self) -> None:
        assert self._file is not None
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                f"another Library runtime owns database {self.path.name}"
            ) from exc

    def close(self) -> None:
        file = self._file
        if file is None:
            return
        self._file = None
        try:
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()

    def __enter__(self) -> DatabaseRuntimeLock:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
