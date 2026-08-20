from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter
from typing import Protocol, Sequence

TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)


class Embedder(Protocol):
    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def estimate_tokens(text: str) -> int:
    """Cheap, conservative token estimate suitable for context budgeting."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


class HashingEmbedder:
    """Dependency-free local embedding baseline using signed feature hashing.

    It is deterministic and surprisingly useful for topical/lexical recall. Replace it
    with a semantic embedder for production without changing the rest of the system.
    """

    def __init__(self, dimensions: int = 384, include_bigrams: bool = True) -> None:
        if dimensions < 32:
            raise ValueError("dimensions must be at least 32")
        self._dimensions = dimensions
        self.include_bigrams = include_bigrams

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _features(self, text: str) -> list[str]:
        tokens = [token.lower() for token in TOKEN_RE.findall(text)]
        if not self.include_bigrams:
            return tokens
        return tokens + [f"{left}::{right}" for left, right in zip(tokens, tokens[1:])]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        counts = Counter(self._features(text))
        for feature, count in counts.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        return normalize(vector)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]


class OllamaEmbedder:
    """Optional local semantic embeddings through an Ollama server."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11434",
        dimensions: int = 768,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dimensions = dimensions
        self.timeout = timeout

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc
        vectors = [
            normalize([float(value) for value in row]) for row in body["embeddings"]
        ]
        if vectors:
            self._dimensions = len(vectors[0])
        return vectors
