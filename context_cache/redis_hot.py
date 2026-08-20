from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import urllib.parse
import zlib
from collections.abc import Callable
from typing import Any

from .models import ContextRecord, SearchHit, WorkingSet


class RedisError(RuntimeError):
    pass


class RedisClient:
    """Small RESP2 client covering only the commands this cache needs."""

    def __init__(self, url: str, timeout: float = 0.4) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"redis", "rediss"}:
            raise ValueError("Redis URL must use redis:// or rediss://")
        if parsed.scheme == "rediss":
            raise ValueError("rediss:// is not supported by the dependency-free client")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 6379
        self.username = (
            urllib.parse.unquote(parsed.username) if parsed.username else None
        )
        self.password = (
            urllib.parse.unquote(parsed.password) if parsed.password else None
        )
        self.database = int((parsed.path or "/0").lstrip("/") or 0)
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._stream: Any = None
        self._lock = threading.RLock()
        self._response_readers: dict[bytes, Callable[[bytes], Any]] = {
            b"+": self._read_text,
            b"-": self._read_error,
            b":": self._read_integer,
            b"$": self._read_bulk,
            b"*": self._read_array,
        }

    @staticmethod
    def _encode(parts: tuple[Any, ...]) -> bytes:
        chunks = [f"*{len(parts)}\r\n".encode("ascii")]
        for part in parts:
            value = part if isinstance(part, bytes) else str(part).encode("utf-8")
            chunks.append(f"${len(value)}\r\n".encode("ascii"))
            chunks.append(value)
            chunks.append(b"\r\n")
        return b"".join(chunks)

    def _read(self) -> Any:
        prefix = self._stream.read(1)
        if not prefix:
            raise OSError("Redis connection closed")
        line = self._stream.readline()
        if not line.endswith(b"\r\n"):
            raise OSError("Invalid Redis response")
        payload = line[:-2]
        reader = self._response_readers.get(prefix)
        if reader is None:
            raise OSError(f"Unknown Redis response prefix: {prefix!r}")
        return reader(payload)

    @staticmethod
    def _read_text(payload: bytes) -> str:
        return payload.decode("utf-8")

    @staticmethod
    def _read_error(payload: bytes) -> None:
        raise RedisError(payload.decode("utf-8", errors="replace"))

    @staticmethod
    def _read_integer(payload: bytes) -> int:
        return int(payload)

    def _read_bulk(self, payload: bytes) -> bytes | None:
        length = int(payload)
        if length == -1:
            return None
        value = self._stream.read(length)
        trailer = self._stream.read(2)
        if trailer != b"\r\n":
            raise OSError("Invalid Redis bulk response")
        return value

    def _read_array(self, payload: bytes) -> list[Any] | None:
        length = int(payload)
        if length == -1:
            return None
        return [self._read() for _ in range(length)]

    def _send(self, *parts: Any) -> Any:
        self._socket.sendall(self._encode(parts))
        return self._read()

    def _connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._stream = sock.makefile("rb")
        if self.password is not None:
            if self.username:
                self._send("AUTH", self.username, self.password)
            else:
                self._send("AUTH", self.password)
        if self.database:
            self._send("SELECT", self.database)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._stream = None
        self._socket = None

    def command(self, *parts: Any) -> Any:
        with self._lock:
            for attempt in range(2):
                try:
                    if self._socket is None:
                        self._connect()
                    return self._send(*parts)
                except (OSError, EOFError):
                    self.close()
                    if attempt:
                        raise
        raise AssertionError("unreachable")


def _dump(value: Any) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"Z" + zlib.compress(raw, level=3)


def _load(value: bytes) -> Any:
    if value.startswith(b"Z"):
        value = zlib.decompress(value[1:])
    return json.loads(value.decode("utf-8"))


class RedisHotCache:
    """Disposable shared RAM tier. SQLite remains authoritative."""

    def __init__(
        self,
        url: str,
        *,
        prefix: str = "library-of-context:v1",
        record_ttl: int = 3600,
        query_ttl: int = 60,
        working_set_ttl: int = 900,
        required: bool = False,
    ) -> None:
        self.client = RedisClient(url)
        self.prefix = prefix.rstrip(":")
        self.record_ttl = record_ttl
        self.query_ttl = query_ttl
        self.working_set_ttl = working_set_ttl
        self.enabled = False
        self.last_error: str | None = None
        self._next_retry_at = 0.0
        try:
            self.enabled = self.client.command("PING") == "PONG"
        except (OSError, RedisError, ValueError) as exc:
            self.last_error = str(exc)
            self._next_retry_at = time.monotonic() + 5.0
            self.client.close()
            if required:
                raise RedisError(f"Redis is required but unavailable: {exc}") from exc

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    def _ns(self, namespace: str) -> str:
        return f"{self.prefix}:n:{self._digest(namespace)}"

    def _record_key(self, namespace: str, record_id: str) -> str:
        return f"{self._ns(namespace)}:r:{self._digest(record_id)}"

    def _safe(self, callback: Any, default: Any = None) -> Any:
        if not self.enabled and time.monotonic() < self._next_retry_at:
            return default
        try:
            result = callback()
            self.enabled = True
            self.last_error = None
            return result
        except (
            OSError,
            RedisError,
            ValueError,
            KeyError,
            TypeError,
            zlib.error,
            json.JSONDecodeError,
        ) as exc:
            self.enabled = False
            self.last_error = str(exc)
            self._next_retry_at = time.monotonic() + 5.0
            self.client.close()
            return default

    def get_record(self, namespace: str, record_id: str) -> ContextRecord | None:
        key = self._record_key(namespace, record_id)

        def operation() -> ContextRecord | None:
            raw = self.client.command("GET", key)
            if raw is None:
                return None
            self.client.command("EXPIRE", key, self.record_ttl)
            return ContextRecord.from_dict(_load(raw))

        return self._safe(operation)

    def put_record(self, record: ContextRecord) -> None:
        self._safe(
            lambda: self.client.command(
                "SET",
                self._record_key(record.namespace, record.id),
                _dump(record.to_dict()),
                "EX",
                self.record_ttl,
            )
        )

    def delete_record(self, namespace: str, record_id: str) -> None:
        self._safe(
            lambda: self.client.command("DEL", self._record_key(namespace, record_id))
        )

    def generation(self, namespace: str) -> int:
        raw = self._safe(
            lambda: self.client.command("GET", f"{self._ns(namespace)}:gen")
        )
        return 0 if raw is None else int(raw)

    def bump_generation(self, namespace: str) -> int:
        return int(
            self._safe(
                lambda: self.client.command("INCR", f"{self._ns(namespace)}:gen"),
                0,
            )
        )

    def _query_key(self, namespace: str, cache_key: str) -> str:
        return f"{self._ns(namespace)}:q:{self._digest(cache_key)}"

    def get_query(self, namespace: str, cache_key: str) -> list[SearchHit] | None:
        raw = self._safe(
            lambda: self.client.command("GET", self._query_key(namespace, cache_key))
        )
        if raw is None:
            return None
        return [SearchHit.from_dict(item) for item in _load(raw)]

    def put_query(self, namespace: str, cache_key: str, hits: list[SearchHit]) -> None:
        self._safe(
            lambda: self.client.command(
                "SET",
                self._query_key(namespace, cache_key),
                _dump([hit.to_dict() for hit in hits]),
                "EX",
                self.query_ttl,
            )
        )

    def _working_key(self, namespace: str, session_id: str) -> str:
        return f"{self._ns(namespace)}:w:{self._digest(session_id)}"

    def put_working_set(self, working_set: WorkingSet) -> None:
        self._safe(
            lambda: self.client.command(
                "SET",
                self._working_key(working_set.namespace, working_set.session_id),
                _dump(working_set.to_dict()),
                "EX",
                self.working_set_ttl,
            )
        )

    def get_working_set(self, namespace: str, session_id: str) -> WorkingSet | None:
        raw = self._safe(
            lambda: self.client.command("GET", self._working_key(namespace, session_id))
        )
        return None if raw is None else WorkingSet.from_dict(_load(raw))

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "last_error": self.last_error,
            "database_keys": self._safe(lambda: int(self.client.command("DBSIZE"))),
            "record_ttl_seconds": self.record_ttl,
            "query_ttl_seconds": self.query_ttl,
            "working_set_ttl_seconds": self.working_set_ttl,
        }

    def close(self) -> None:
        self.client.close()
