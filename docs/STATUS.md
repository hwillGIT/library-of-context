# Capability Status

Version 0.4 provides explicit thread identity, scoped retrieval, a shared bounded runtime, leased outbox processing, and a loopback daemon.

A runtime owns the shared services for one database. A daemon is a background process that serves several clients on the same computer.

An outbox is a durable table of indexing work. A lease grants one runtime temporary ownership of an outbox item.

It also provides thin Model Context Protocol bridges. Model Context Protocol (MCP) defines an interface for agent tools and resources.

The version does not provide team synchronization or authenticated access-control lists. An access-control list (ACL) defines which identities can access each resource.

The version also lacks approximate nearest-neighbor retrieval, model-specific tokenization, native compaction hooks, and operating-system service supervision.

Approximate nearest-neighbor (ANN) retrieval searches likely vector matches without scoring every vector. Service supervision starts, monitors, and restarts a background process.

A tokenizer converts text into the token units that a model processes. Compaction replaces older conversation text with a shorter continuation state.

The [Glossary](GLOSSARY.md) defines shared technical terms.

Redis is an optional in-memory key-value cache. A reading desk is the bounded set of retrieved books for one thread and subject.

SQLite is the embedded database that stores authoritative Library data in one file.

## Status meanings

- **Implemented:** Public code and functional tests cover the capability.
- **Implemented trusted-local routing:** The code enforces a supplied route but does not authenticate the identity behind that route.
- **Implemented trusted-local interface:** The public interface works with trusted local callers and does not provide multi-user authorization.
- **Experimental and optional:** Local evaluation is possible, but the status does not claim operational support.
- **Development surface:** The capability works only within its stated boundary. Operators must not expose it beyond that boundary.
- **Implemented cooperative integration:** The host chooses when to call the integration. The integration cannot control an undocumented host function.
- **Implemented gateway surface:** The capability works when a gateway controls the next model call.
- **Implemented Python and status surface:** Python exposes the operation, and status output reports its state.
- **Planned correctness work:** The system needs the capability to support a stated correctness claim.
- **Planned conditional work:** Implementation depends on a stated use case and evidence gate.
- **Planned operational work:** Deployment support requires a defined platform contract and operational tests.
- **Scale-triggered design:** Adoption requires measured resource or latency pressure. The mechanism is not a default requirement.
- **Research:** The desired behavior and acceptance evidence remain unsettled.
- **Team-only design:** The capability applies only to shared deployments with several identities.
- **Planned and optional:** The capability is not necessary for local prompt construction.

## Capability matrix

| Capability | Status | Evidence or limitation |
|---|---|---|
| Durable thread events with transactional outbox | Implemented | SQLite schema and restart and overflow-recovery tests |
| Bounded governed model envelope | Implemented | Python, Hypertext Transfer Protocol, and MCP tests. The estimator is approximate. |
| Immediate visibility for recent events | Implemented | Unindexed recent events remain directly visible. |
| Explicit protection and release | Implemented | The protected set remains subject to its token budget. |
| Bounded work ring with durable overflow storage | Implemented | The ring accelerates dispatch. The outbox provides recovery. |
| `ThreadKey(collection, session_id)` identity | Implemented | Stateful MCP, command-line, Python, and local service paths reject invalid session identity. |
| Thread, project, and team record scopes | Implemented trusted-local routing | Search, lookup, pinning, desks, and cache keys enforce scope. The Library does not authenticate team identifiers. |
| Explicit record promotion | Implemented trusted-local interface | Promotion copies records to project or team scope. It preserves source and provenance. |
| Recorded, embedded, and indexed watermarks | Implemented | A watermark identifies the highest completed sequence. Embedding and indexing advance together. |
| SQLite full-text search with exact vector ranking | Implemented | The query limits full-text search output. Vector work grows with namespace size. |
| Process random-access memory cache | Implemented | A disposable cache uses estimated byte limits and least-recently-used eviction. |
| Local Redis hot cache | Experimental and optional | Runtime key-space version 2 starts empty after restart. Redis is disposable and provides no broker or Transport Layer Security. |
| Local Ollama embeddings | Experimental and optional | A local service converts text to vectors. The adapter has no batching or connection pool. |
| Loopback Hypertext Transfer Protocol service | Development surface | A bearer token protects local clients. The service rejects browser origins and provides no user authorization or encryption. |
| Python text-agent adapter | Implemented | The adapter governs stateless text callbacks. Structured tools and mixed media need custom adapters. |
| Standard-input and standard-output MCP Library tools | Implemented cooperative integration | The tools cannot control an undocumented host compaction function. |
| Standard-input and standard-output MCP governor tools | Implemented gateway surface | The client must own the model call that follows `prepare`. |
| Shared `LibraryRuntime` | Implemented | Sessions share fixed workers and bounded registries. Metadata and selected results have no strict byte quota. |
| Atomic outbox claims and leases | Implemented | Tests cover thread order, claim tokens, owner replacement, queue bounds, and concurrency. |
| Retry and terminal quarantine | Implemented Python and status surface | Retries use increasing delays and random variation. An attempt limit moves failed work to quarantine. |
| Loopback daemon and thin MCP bridge | Development surface | One daemon owns the runtime and database lock. It limits requests but provides no user authorization or encryption. |
| SQLite schema migration from version 2 through version 6 | Implemented | Tests cover resumable stages, scope classification, identity rebuilding, claims, quarantine, and rejection of future schemas. |
| Model-accurate tokenizer adapters | Planned correctness work | A selected model needs an accurate token count before the system can claim its hard limit. |
| Complete retrieval and cache identity | Planned correctness work | Cache keys must represent ranking, model, index, and authorization versions. |
| Local ANN and bounded vector candidates | Scale-triggered design | Adopt only when exact retrieval exceeds a measured service target. |
| Atomic source-edition publication | Planned conditional work | Concurrent replacement or versioned team provenance needs atomic publication. |
| Operating-system daemon supervision and upgrades | Planned operational work | Platforms need service installation, restart, interface upgrade, and schema-upgrade contracts. |
| Automatic protection, capsules, and summaries | Research | Derived content must remain explainable and linked to source records. |
| Branch and fork inheritance and merge | Research | The project defines no implemented lineage or conflict protocol. |
| Authenticated team routing | Team-only design | Local team scope trusts route input. The Library does not verify identity or membership. |
| Knowledge-card promotion workflow | Team-only design | The code does not implement this workflow. Raw threads remain local by default. |
| Shared identity, ACLs, deletion markers, and audit | Team-only design | A mixed-trust catalog requires these controls. |
| Durable team synchronization or broker | Team-only design | The project deploys no broker. Tests should evaluate simple database synchronization first. |
| Cloud control plane | Planned and optional | The control plane must remain outside local prompt construction. |

A capsule is a concise, derived context record that refers to its source records. A deletion marker is a durable record that marks data as deleted.

For planned-work rationale, read [Why These Improvements?](WHY_THE_ROADMAP.md). For implementation sequence, read the [Roadmap](roadmap.md).
