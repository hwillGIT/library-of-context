# Changelog

This changelog records notable project changes. The project will use semantic
versioning after it defines stable release guarantees. The
[glossary](docs/GLOSSARY.md) defines shared terms.

## [Unreleased]

### Added

- `GovernedTextAgent` adds bounded context to stateless text-agent callbacks.
- The disposable `library-of-context quickstart` self-test uses no Redis or cloud
  service. It retains no data.
- Normal-user installation and existing-agent integration guides.
- Acceptance tests for clean-directory installation, process restart recovery, agent
  isolation, and the documented first-run path.
- Model Context Protocol (MCP) profile acceptance tests.
- Explicit `ThreadKey(collection, session_id)` identity and thread, project, and team
  scopes. These scopes cover records, retrieval, pinning, desks, and query caches.
- Copy-based project and team promotion with retained private sources and provenance.
- One shared `LibraryRuntime` with fixed outbox workers and a fixed desk scheduler.
- Bounded thread-state and desk registries.
- Atomic outbox claims, claim tokens, and lease recovery.
- Exponential retry with jitter and terminal quarantine.
- Operator retry through the Python governor.
- A loopback daemon with required pre-open database ownership.
- Bounded Hypertext Transfer Protocol (HTTP) admission.
- Thin MCP bridges without local database, Redis, embedder, or worker resources.
- Bearer-authenticated daemon transport with browser-origin and hostile `Host`
  rejection.
- Rejection of requests without JavaScript Object Notation data.
- Owner-only Portable Operating System Interface storage checks.
- Symbolic-link, hard-link, and non-regular-file rejection for credentials, SQLite,
  sidecars, and owner locks.
- Staged SQLite migrations through schema 6.
- Migration tests for interruption recovery, idempotence, foreign keys, and future
  schema rejection.
- A normative thread-scope and shared-runtime architecture decision record.
- A contributor quality workflow for contracts, migrations, concurrency, faults,
  specialist review, and release evidence.

### Changed

- The Codex MCP template isolates projects and supports virtual environments.
- The template makes Redis optional and exposes cooperative-host tools only.
- HTTP context status honors a non-default collection query.
- Command-line, HTTP, MCP, prompt, index, retrieval, queue, and budget responsibilities
  use separate modules. Compatibility tests cover these boundaries.
- Query-cache identity includes the ranker version, ranking policy, and stable
  embedder configuration.
- Stateful Python, MCP, HTTP, and command-line operations require a session identifier.
- Governor, session, desk, MCP, and HTTP handles share process-owned infrastructure.
- Per-thread random-access memory uses time and least-recently-used limits.
- SQLite can reconstruct per-thread memory state.
- Thin MCP bridges apply a project-specific collection default, preserve explicit tool
  routing, and use a configurable 120-second daemon request deadline.
- Redis hot-cache entries use a versioned runtime keyspace. Process restarts use an
  empty cache view. A runtime ignores data from other runtimes.
- Retrieved books use escaped structural markup and an explicit untrusted-reference
  boundary in governed and virtual-session prompts.
- Agent-facing desk and search payloads use bounded excerpts and small record
  references.
- Record endpoints retain complete administrative payloads.
- Context commit and protect responses use bounded acknowledgements without echoing
  event content or metadata.
- The Windows Subsystem for Linux Redis installer creates a separate authenticated
  service. It does not change the default Ubuntu Redis configuration.

### Fixed

- Virtual sessions filter history by thread scope and serialize concurrent writes. A
  project record cannot enter a chat through a copied source label.
- Record upserts cannot change an existing visibility boundary.
- Scope expansion requires a separate destination through explicit promotion.
- Concurrent outbox scans cannot claim more work than the bounded dispatch ring can
  accept.
- HTTP desk lookup and periodic-watch stop operations honor the requested collection.
- Oversized recent events use a token-capped RAM projection while SQLite retains the
  complete content.
- Durable source events reserve event-derived record identifiers. Direct records and
  other events cannot replace these records.
- Shutdown retains database ownership until SQLite closes and retries incomplete
  resource teardown without admitting new work.

## [0.3.0] - 2026-08-20

### Added

- `LibraryContextGovernor` with `prepare`, `commit`, `protect`, `release`, `flush`, and
  `status` operations.
- Durable `thread_events`, `thread_heads`, and transactional `context_outbox` storage.
- Token-aware recent-event ring and bounded asynchronous indexing work ring.
- Recorded, embedded, indexed, and team-synchronization watermarks.
- Context-governor MCP tools and local HTTP endpoints.
- Crash-recovery, ring-overflow, protected-context, idempotency, HTTP, and MCP tests.
- Contributor, security, governance, team, and performance documentation.

### Changed

- Full-text search candidate generation stopped joining each match to a computed record
  key.
- Oversized recent messages are prompt-truncated while their full durable copies remain.
- Package metadata and the MCP server version changed to 0.3.0.

## [0.2.0] - 2026-08-20

- Added stateless bounded virtual sessions and MCP integration.
- Added a local Redis installer and the Library metaphor interface.

## [0.1.0] - 2026-08-20

- Added the first SQLite, memory, Redis, hybrid retrieval, and reading-desk
  implementation.

[Unreleased]: https://github.com/hwillGIT/library-of-context/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/hwillGIT/library-of-context/releases/tag/v0.3.0
