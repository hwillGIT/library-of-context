# Roadmap and Research Agenda

The Library of Context has alpha status. The roadmap orders work by correctness and
evidence. The [glossary](docs/GLOSSARY.md) defines shared terms.

Roadmap items are not requirements for every installation. Milestone 1 establishes
correctness evidence. Milestones 2 and 3 address catalog size and concurrent local
agents. Milestone 4 covers context-quality research. Milestone 5 serves teams with a
cross-workstation need.

Read [Why These Improvements?](docs/WHY_THE_ROADMAP.md) for supporting and opposing
cases. It also defines alternatives, adoption triggers, and evidence gates. Use the
[decision brief template](docs/DECISION_BRIEF_TEMPLATE.md) for a substantial proposal.

The [related-work landscape](docs/RELATED_WORK.md) provides comparative evidence. An
entry there does not mean adoption or commitment.

## Implemented foundation — 0.4

- SQLite, random-access memory, and optional Redis hierarchy.
- Hybrid full-text search and exact-vector retrieval.
- Token-bounded reading desks and swap deltas.
- Durable thread events and transactional outbox.
- Token-aware recent ring and bounded asynchronous work ring.
- Context governor `prepare -> model -> commit` lifecycle.
- Protected context, idempotent event IDs, recovery, and watermarks.
- Python, Hypertext Transfer Protocol, command-line, and Model Context Protocol
  interfaces.
- Common-term full-text join removal and large-message budget protection.
- Explicit thread identity and thread/project/team retrieval scopes.
- Copy-based promotion with source preservation and provenance.
- One shared runtime with fixed outbox workers, one desk scheduler, bounded thread
  state, and bounded desk snapshots.
- Atomic outbox claims, leases, ordered work, retry jitter, and terminal quarantine.
- Loopback daemon and thin MCP bridges with exclusive database ownership and bounded
  request admission.
- Staged SQLite migration through schema version 6.

## Milestone 1 — measurable retrieval

- Build a reproducible cardinality/concurrency/quality benchmark harness.
- Define agent-thread retrieval datasets and expected evidence.
- Add tokenizer adapters and enforce actual model-token budgets.
- Complete cache-key identity and index-version tracking.
- Apply metadata and access-control filters during candidate generation.
- Add metrics for latency, candidates, queue age, lag, resident memory, and disk use.

**Reason:** A faster retriever can lose important context without comparable
measurements. A design change can also address the wrong limit. Token and cache
identity affect correctness.

**Reason to limit scope:** A full test matrix and fleet telemetry add cost. They can
also expose private labels. Start with controlled local workloads, difficult token
cases, small metrics sets, and scheduled scale tests.

**Exit gate:** Publish benchmark results. The tested workloads must have no prompt-budget
violations.

## Milestone 2 — bounded search work

- Introduce a vector-index adapter interface.
- Implement and compare one local approximate nearest neighbor (ANN) backend.
- Combine bounded ANN and full-text candidates. Apply exact reranking.
- Add diversity and duplicate suppression.
- Batch embeddings by token count and use pooled connections.
- Publish immutable source versions atomically.

**Reason:** Exact vector-scoring work increases with namespace size. Repeated candidates
consume desk capacity. Per-item embedding also adds round trips. Bounded candidates and
atomic editions address these measured problems.

**Reason for optional use:** ANN is approximate and platform-sensitive. It requires more
operations than exact scoring. Batching adds visibility delay and partial-failure rules.

Keep exact search for a small catalog that meets its latency and memory targets.

**Adoption trigger:** Adopt ANN when exact search exceeds a declared latency or memory
service-level objective. Embedding delay or source-replacement delay can also trigger
adoption.

**Exit gate:** Candidate work and peak retrieval memory do not depend on catalog size.
Recall at 12 results meets the agreed target.

## Milestone 3 — workstation operations

- Package and supervise the daemon with platform service managers.
- Add weighted priority lanes and fair-share scheduling under mixed interactive and bulk
  load.
- Add disk quotas, low-disk admission, backup/restore checks, and operator remediation.
- Version daemon upgrades and mixed-client compatibility.
- Evaluate authenticated interprocess communication when trusted loopback is
  insufficient.

**Reason:** The shared runtime bounds caches, workers, outbox scans, and connections.
Workstation operation also needs supervision, fair queues, disk policy, safe upgrades,
and recovery.

**Reason for optional use:** Service installation, authentication, upgrades, and resource
policy add operational responsibility. Embedded mode has fewer components for one
small process.

**Adoption trigger:** Adopt daemon operations when measured resource use exceeds the
declared workstation profile. Relevant measures include duplicate work, cache memory,
thread count, SQLite contention, and queue age. Agent count alone is insufficient.

**Exit gate:** The declared ten-agent workload meets append, prompt, and recovery
service-level objectives.

## Milestone 4 — context intelligence

- Derived thread-state capsules with objectives, decisions, constraints, and open work.
- Pluggable protection and release policies with visible explanations.
- Branch/fork inheritance and explicit context snapshots.
- Summaries as versioned navigation aids while retaining originals.
- Adaptive retrieval routing across thread, personal, project, and team indexes.
- Quality evaluation for focus shifts, stale state, contradictions, and supersession.

**Reason:** Raw retrieval can repeatedly miss objectives and decisions. Explicit
snapshots can make forks reproducible. They can also explain scope selection.

**Reason for optional use:** Summaries and capsules can add false statements or hide
qualifications. They can preserve replaced instructions. Automatic protection can fill
the prompt with stale state.

Derived context must remain versioned and explainable. It must link to its source.

**Adoption trigger:** Adopt context intelligence when long-thread tests show continuity,
branch, or scope failures. Basic recent, protected, and retrieved paging must not solve
those failures.

**Exit gate:** Agent-thread tests show higher continuity than basic paging. They show no
increase in hidden instructions or stale context.

## Milestone 5 — selective team memory

- Define a knowledge-card and promotion-review format.
- Add device identity, projects, principals, scopes, provenance, and tombstones.
- Enforce authorization before retrieval and before loading complete records.
- Implement idempotent sync outbox/inbox and durable cursor recovery.
- Compare Redis Streams, NATS JetStream, and alternatives with failure tests.
- Keep the team plane optional and outside the local prompt critical path.

**Reason:** Teams need reusable decisions and evidence across workstations. Shared
knowledge needs origin, authorization, replay, deletion, and offline recovery.

**Reason for optional use:** Synchronizing raw threads increases privacy and compliance
scope. A broker transports events. It does not define promotion, identity, conflicts,
or revocation.

Reviewed cards and an authenticated database synchronization interface can meet a small
team's needs.

**Adoption trigger:** Adopt team memory when multiple users need approved knowledge
across independent devices. Authorization is required before shared retrieval.

Add a broker only when distribution, event rate, or replay exceeds direct
synchronization limits.

**Exit gate:** Authorization tests show no cross-project exposure. Offline nodes continue
local prompt construction. The deployment defines authorization-lease or maximum
revocation-delay behavior for cached team data.

## Questions looking for owners

### Context policy

- What is the correct high-watermark policy for intervention?
- Which instructions should be protected automatically?
- How should conflicting protected items be surfaced and resolved?
- Should older protected events be truncated, summarized, or cause a hard error?

### Retrieval quality

- What benchmark represents agent-thread recall instead of document question answering?
- How should recency compete with durable decisions and superseding facts?
- When should a retrieval miss trigger clarification instead of speculative recall?
- How should the system quantify provenance and confidence in the prompt?

### Branches and collaboration

- What context should a child thread inherit at its fork point?
- How should two thread branches merge decisions without merging raw transcripts?
- What makes a local memory worthy of project or team promotion?
- How should team members inspect, dispute, and supersede promoted knowledge?

### Systems engineering

- Which local ANN implementation meets the declared reliability and complexity limits?
- Can one daemon support many agents without becoming a workstation single point of
  failure?
- Which queue metrics and backpressure signals are understandable to agent developers?
- What recovery guarantees are realistic under abrupt power loss?

### Privacy and security

- How can useful evaluation run without centralizing private prompt content?
- Which fields require encryption beyond full-disk protection?
- How should deletion propagate to offline nodes and derived indexes?
- How can authorization be proven at candidate generation rather than after retrieval?

### Human experience

- How should a user see which “books” were paged in and why?
- When should the agent disclose stale or partially indexed context?
- What controls make protection, release, and promotion predictable?
- Can the library metaphor remain helpful without hiding technical truth?

## How to participate

- Open a **Research question** issue for an unresolved design problem.
- Include reproducible measurements with performance proposals.
- Use a request for comments issue before a large schema, broker, or retrieval-policy
  change.
- Contribute small adapters behind interfaces rather than coupling the core to a vendor.
- Add failure and privacy tests with every distributed-systems feature.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.
