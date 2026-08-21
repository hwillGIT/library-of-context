"""The Library of Context: virtual memory for bounded model context."""

from .agent import GovernedTextAgent
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
from .runtime import LibraryRuntime, RuntimeSettings
from .scopes import ContextScope, ScopeSelection, ThreadKey
from .session import VirtualContextSession
from .swapper import ContextSwapper
from .thread_state import ThreadCapacityError, ThreadState, ThreadStateRegistry
from .version import __version__

__all__ = [
    "__version__",
    "ContextCache",
    "ContextEvent",
    "ContextRecord",
    "ContextScope",
    "ContextSwapper",
    "ContextWatermarks",
    "GovernedPrompt",
    "GovernedTextAgent",
    "HashingEmbedder",
    "LibraryOfContext",
    "LibraryRuntime",
    "LibraryContextGovernor",
    "OllamaEmbedder",
    "PromptEnvelope",
    "ReadingDesk",
    "SearchHit",
    "ScopeSelection",
    "RuntimeSettings",
    "ThreadKey",
    "ThreadCapacityError",
    "ThreadState",
    "ThreadStateRegistry",
    "VirtualContextSession",
    "WorkingSet",
]
