# Python application programming interface reference

An application programming interface (API) defines how software components communicate. Import this public API from `library_of_context` or `context_cache`.

Mkdocstrings generates the reference sections from the source code.

## Model vocabulary

- A `ContextEvent` is an ordered source item in one governed thread. It can contain a message, instruction, or tool result.
- A `ContextRecord` is a searchable unit in the `records` table. It contains text, an embedding, metadata, origin data, and a visibility scope.
- A **book** is the public name and serialized view of one `ContextRecord`. The Library has no separate `Book` class or table.
- An event reserves the identifier for its derived record. A direct record write cannot replace that searchable copy.
- A `ThreadKey(collection, session_id)` identifies one stateful chat. The collection identifies the project, and the session identifier identifies the thread.
- A `WorkingSet` is the size-limited reading-desk snapshot. The Library assembles it from records that the caller can access.

## Agent-facing transport views

Model Context Protocol (MCP) tools use size-limited transport views for searches and reading desks. Hypertext Transfer Protocol (HTTP) routes use the same views.

A search result contains scores, a small record reference, and a short excerpt. Treat the excerpt as untrusted data.

A desk contains the size-limited `context` block and small search-result references. The `context` block is ready for a model prompt.

These transport views omit embeddings, complete metadata, and complete record text. An embedding is a numeric representation that supports similarity searches.

Commit and protect operations return size-limited event acknowledgements. They do not return the submitted content or metadata.

Direct record administration routes return complete records.

A daemon is a background process that owns shared Library resources. The daemon versions its MCP message format separately from its SQLite database format.

A bridge rejects a daemon that uses a different message version.

## Storage path

The Library requires a SQLite database file. This file supports recovery and exclusive ownership by one Library runtime.

The Library does not support the SQLite `:memory:` value.

## Text-agent adapter

::: context_cache.agent.GovernedTextAgent
    options:
      members:
        - turn
      inherited_members: false

## Context governor

::: context_cache.governor.LibraryContextGovernor
    options:
      members:
        - record
        - prepare
        - commit
        - protect
        - release
        - build_prompt
        - flush
        - retry_failed
        - status
        - close
      inherited_members: false

## Library facade

::: context_cache.library.LibraryOfContext
    options:
      members:
        - shelve
        - shelve_document
        - consult
        - promote_book
        - open_reading_desk
        - open_virtual_session
        - open_context_governor
      inherited_members: false

## Thread identity and visibility

::: context_cache.scopes.ThreadKey

::: context_cache.scopes.ContextScope

::: context_cache.scopes.ScopeSelection

## Shared runtime configuration

::: context_cache.runtime.RuntimeSettings

::: context_cache.runtime.LibraryRuntime

## Loopback daemon client

Every daemon route requires `Authorization: Bearer <token>`. A bearer token is a secret value that grants access to its holder.

An HTTP `POST` request sends a body to a route. An HTTP `GET` request reads data from a route.

The `serve` command reads or creates the owner-readable file from `--auth-token-file`. The default path is `<database-path>.daemon-token`.

A thin MCP bridge reads the same file through `--daemon-token-file`.

`LibraryDaemonClient` requires the token in its `bearer_token` argument. It accepts only loopback HTTP addresses and sends the token with each request.

A loopback address sends traffic only inside the local computer. The daemon rejects browser origins, cross-site requests, and non-loopback `Host` values.

The daemon also rejects POST bodies unless their media type is `application/json`. JavaScript Object Notation (JSON) is the daemon message format.

::: context_cache.client.LibraryDaemonClient

## Reading desk

::: context_cache.library.ReadingDesk
    options:
      members:
        - lay_out
        - change_subject
        - current_books
      inherited_members: false

## Core models

::: context_cache.models.ContextEvent

::: context_cache.models.ContextWatermarks

::: context_cache.models.GovernedPrompt

::: context_cache.models.ContextRecord

::: context_cache.models.SearchHit

::: context_cache.models.WorkingSet
