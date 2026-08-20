"""The Library of Context: virtual memory for bounded model context."""

from .embeddings import HashingEmbedder, OllamaEmbedder
from .engine import ContextCache
from .governor import LibraryContextGovernor
from .library import LibraryOfContext, ReadingDesk
from .models import (
    ContextEvent,
    ContextRecord,
    ContextWatermarks,
    GovernedPrompt,
    PromptEnvelope,
    SearchHit,
    WorkingSet,
)
from .session import VirtualContextSession
from .swapper import ContextSwapper

__all__ = [
    "ContextCache",
    "ContextEvent",
    "ContextRecord",
    "ContextSwapper",
    "ContextWatermarks",
    "GovernedPrompt",
    "HashingEmbedder",
    "LibraryOfContext",
    "LibraryContextGovernor",
    "OllamaEmbedder",
    "PromptEnvelope",
    "ReadingDesk",
    "SearchHit",
    "VirtualContextSession",
    "WorkingSet",
]

__version__ = "0.3.0"
