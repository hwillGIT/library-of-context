# Changelog

All notable changes will be documented here. The project follows semantic versioning
once stable release guarantees are established.

## [Unreleased]

- Benchmark harness and local ANN adapter planned.

## [0.3.0] - 2026-08-20

### Added

- `LibraryContextGovernor` with `prepare`, `commit`, `protect`, `release`, `flush`, and
  `status` lifecycle operations.
- Durable `thread_events`, `thread_heads`, and transactional `context_outbox` storage.
- Token-aware recent-event ring and bounded asynchronous indexing work ring.
- Recorded, embedded, indexed, and team-sync watermarks.
- Context-governor MCP tools and loopback HTTP endpoints.
- Crash-recovery, ring-overflow, protected-context, idempotency, HTTP, and MCP tests.
- Open-source contributor, security, governance, team, and performance documentation.

### Changed

- FTS lexical candidate generation no longer joins every match against a computed
  records-table key.
- Oversized recent messages are prompt-truncated while their full durable copies remain.
- Package metadata and MCP server version advanced to 0.3.0.

## [0.2.0] - 2026-08-20

- Added stateless bounded virtual sessions, MCP integration, local Redis installer, and
  Library metaphor API.

## [0.1.0] - 2026-08-20

- Initial SQLite, RAM, Redis, hybrid retrieval, and reading-desk implementation.

[Unreleased]: https://github.com/hwillGIT/library-of-context/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/hwillGIT/library-of-context/releases/tag/v0.3.0
