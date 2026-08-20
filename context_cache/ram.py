from __future__ import annotations

import json
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

from .models import ContextRecord

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    size: int
    expires_at: float | None


class ByteLRU(Generic[T]):
    def __init__(self, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        self._bytes = 0
        self._items: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._bytes

    def get(self, key: str) -> T | None:
        now = time.time()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at is not None and entry.expires_at <= now:
                self._delete_unlocked(key)
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return entry.value

    def put(
        self,
        key: str,
        value: T,
        *,
        size: int,
        ttl_seconds: float | None = None,
    ) -> None:
        expires_at = (
            None if ttl_seconds is None else time.time() + max(0.0, ttl_seconds)
        )
        with self._lock:
            self._delete_unlocked(key)
            if size > self.max_bytes:
                return
            self._items[key] = _Entry(value=value, size=size, expires_at=expires_at)
            self._bytes += size
            while self._bytes > self.max_bytes and self._items:
                _, entry = self._items.popitem(last=False)
                self._bytes -= entry.size
                self.evictions += 1

    def delete(self, key: str) -> None:
        with self._lock:
            self._delete_unlocked(key)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def _delete_unlocked(self, key: str) -> None:
        entry = self._items.pop(key, None)
        if entry is not None:
            self._bytes -= entry.size

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "items": len(self._items),
                "used_bytes": self._bytes,
                "max_bytes": self.max_bytes,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
            }


def record_size(record: ContextRecord) -> int:
    return (
        sys.getsizeof(record)
        + sys.getsizeof(record.text)
        + sys.getsizeof(record.embedding)
        + sum(sys.getsizeof(value) for value in record.embedding)
        + sys.getsizeof(record.metadata)
        + len(json.dumps(record.metadata, ensure_ascii=False).encode("utf-8"))
    )
