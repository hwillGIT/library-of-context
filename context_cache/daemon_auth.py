from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

MINIMUM_TOKEN_CHARACTERS = 32
MAXIMUM_TOKEN_FILE_BYTES = 4096


def _secure_open_flags(flags: int) -> int:
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    return flags


def _validate_token_file(
    token_path: Path,
    descriptor: int,
    *,
    path_status: os.stat_result,
) -> None:
    opened_status = os.fstat(descriptor)
    if not stat.S_ISREG(opened_status.st_mode):
        raise RuntimeError(f"daemon token file is not a regular file: {token_path}")
    if (opened_status.st_dev, opened_status.st_ino) != (
        path_status.st_dev,
        path_status.st_ino,
    ):
        raise RuntimeError(f"daemon token file changed while opening: {token_path}")
    if opened_status.st_nlink != 1:
        raise RuntimeError(f"daemon token file must have one link: {token_path}")
    if os.name == "posix":
        if opened_status.st_uid != os.geteuid():
            raise RuntimeError(
                f"daemon token file is not owned by the current user: {token_path}"
            )
        if stat.S_IMODE(opened_status.st_mode) & 0o077:
            raise RuntimeError(
                f"daemon token file grants group or other access: {token_path}"
            )


def _open_existing_token_file(token_path: Path) -> int:
    try:
        path_status = os.lstat(token_path)
    except OSError as exc:
        raise RuntimeError(
            f"cannot inspect daemon token file {token_path}: {exc}"
        ) from exc
    if stat.S_ISLNK(path_status.st_mode):
        raise RuntimeError(f"daemon token file cannot be a symbolic link: {token_path}")

    try:
        descriptor = os.open(token_path, _secure_open_flags(os.O_RDONLY))
    except OSError as exc:
        raise RuntimeError(
            f"cannot open daemon token file {token_path}: {exc}"
        ) from exc
    try:
        _validate_token_file(token_path, descriptor, path_status=path_status)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def generate_daemon_token() -> str:
    """Generate one bearer credential for a loopback daemon."""

    return validate_daemon_token(secrets.token_urlsafe(32))


def validate_daemon_token(token: str) -> str:
    """Validate one bearer credential for the loopback daemon."""

    if not isinstance(token, str):
        raise TypeError("daemon bearer token must be a string")
    if len(token) < MINIMUM_TOKEN_CHARACTERS:
        raise ValueError(
            f"daemon bearer token must contain at least {MINIMUM_TOKEN_CHARACTERS} "
            "characters"
        )
    if token != token.strip() or any(character.isspace() for character in token):
        raise ValueError("daemon bearer token cannot contain whitespace")
    return token


def read_daemon_token(path: str | Path) -> str:
    """Read a daemon bearer credential from a local file."""

    token_path = Path(path)
    try:
        descriptor = _open_existing_token_file(token_path)
        try:
            payload = os.read(descriptor, MAXIMUM_TOKEN_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(payload) > MAXIMUM_TOKEN_FILE_BYTES:
            raise RuntimeError(f"daemon token file is too large: {token_path}")
        if payload.endswith(b"\n"):
            payload = payload[:-1]
        token = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(
            f"cannot read daemon token file {token_path}: {exc}"
        ) from exc
    return validate_daemon_token(token)


def load_or_create_daemon_token(path: str | Path) -> str:
    """Read or atomically create a local daemon bearer-token file."""

    token_path = Path(path)
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = generate_daemon_token()
    flags = _secure_open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        descriptor = os.open(token_path, flags, 0o600)
    except FileExistsError:
        return read_daemon_token(token_path)
    opened_status = os.fstat(descriptor)
    opened_identity = (opened_status.st_dev, opened_status.st_ino)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            path_status = os.lstat(token_path)
            _validate_token_file(
                token_path,
                handle.fileno(),
                path_status=path_status,
            )
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path_status = os.lstat(token_path)
            if (path_status.st_dev, path_status.st_ino) == opened_identity:
                token_path.unlink()
        except OSError:
            pass
        raise
    return validate_daemon_token(token)
