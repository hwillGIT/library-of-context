from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class ReadWriteGate:
    """Allow concurrent record operations and exclusive bulk mutations."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        self._local = threading.local()

    @contextmanager
    def read(self) -> Iterator[None]:
        depth = getattr(self._local, "read_depth", 0)
        with self._condition:
            if depth == 0:
                self._condition.wait_for(
                    lambda: not self._writer and self._waiting_writers == 0
                )
            self._readers += 1
            self._local.read_depth = depth + 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._local.read_depth -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        if getattr(self._local, "read_depth", 0):
            raise RuntimeError("a record operation cannot upgrade to a bulk mutation")
        with self._condition:
            self._waiting_writers += 1
            try:
                self._condition.wait_for(
                    lambda: not self._writer and self._readers == 0
                )
                self._writer = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()
