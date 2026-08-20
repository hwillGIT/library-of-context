# Roadmap and Research Agenda

The Library of Context is an alpha and a design collaboration. The roadmap is ordered
by correctness and evidence, not by feature novelty.

Roadmap items are not mandatory dependencies for every installation. Milestone 1 is the
evidence and correctness foundation. Milestones 2 and 3 address independent pressures:
catalog cardinality and concurrent local agents. Milestone 4 is context-quality
research. Milestone 5 exists only for teams that need cross-workstation knowledge.

Read [Why These Improvements?](docs/WHY_THE_ROADMAP.md) for the affirmative case,
skeptical case, alternatives, adoption triggers, and evidence gates. New substantial
proposals should use the [decision brief template](docs/DECISION_BRIEF_TEMPLATE.md).
The [related-work landscape](docs/RELATED_WORK.md) supplies comparative evidence;
inclusion there is neither adoption nor a roadmap commitment.

## Shipped foundation — 0.3

- SQLite/RAM/optional Redis memory hierarchy.
- Hybrid FTS and exact-vector retrieval.
- Token-bounded reading desks and swap deltas.
- Durable thread events and transactional outbox.
- Token-aware recent ring and bounded asynchronous work ring.
- Context governor `prepare -> model -> commit` lifecycle.
- Protected context, idempotent event IDs, recovery, and watermarks.
- Python, HTTP, CLI, and MCP surfaces.
- Common-term FTS join removal and oversized-message budget protection.

## Milestone 1 — measurable retrieval

- Build a reproducible cardinality/concurrency/quality benchmark harness.
- Define agent-thread retrieval datasets and expected evidence.
- Add tokenizer adapters and enforce actual model-token budgets.
- Complete cache-key identity and index-version tracking.
- Push metadata and future ACL predicates into candidate generation.
- Add structured metrics for latency, candidates, queue age, lag, RSS, and disk.

**Why:** without comparable measurements, a faster retriever may quietly lose important
context, and an architectural rewrite may optimize the wrong bottleneck. Token and cache
identity are also correctness concerns, not optional tuning.

**Why not overbuild it:** a full Cartesian benchmark matrix and fleet telemetry would be
expensive and could expose private labels. Begin with deterministic local workloads,
adversarial token cases, low-cardinality metrics, and scheduled scale suites.

**Exit gate:** published benchmark results and zero tested prompt-budget violations.

## Milestone 2 — bounded search work

- Introduce a vector-index adapter interface.
- Implement and compare at least one local ANN backend.
- Union bounded ANN and FTS candidates, then exact-rerank.
- Add diversity and duplicate suppression.
- Batch embeddings by token count and use pooled connections.
- Publish immutable source versions atomically.

**Why:** exact vector scoring still grows with the namespace, redundant candidates waste
the desk, and per-item embedding work accumulates round trips. Bounded candidates and
atomic editions address measured work and visibility problems.

**Why not automatically:** ANN is approximate, platform-sensitive, and operationally
heavier than exact scoring. Batching adds visibility lag and partial-failure policy.
Small catalogs should retain exact search when it meets their latency and memory target.

**Adoption trigger:** the exact path crosses the declared cold-query or peak-memory SLO,
or source replacement and embedding lag become observable under the target workload.

**Exit gate:** candidate work and peak retrieval memory are bounded independently of
catalog size, with recall@12 at or above the agreed reference target.

## Milestone 3 — workstation daemon

- Run one supervised Library daemon per workstation.
- Replace per-governor workers and timer threads with one scheduler and fixed pools.
- Partition work by project/thread while preserving thread order.
- Add priority lanes, queue admission control, coalescing, and stale-result rejection.
- Add session/desk TTLs, quotas, graceful shutdown, and low-disk policy.
- Provide thin MCP, HTTP, and local IPC bridges.

**Why:** several local agents otherwise duplicate caches, workers, outbox scans, and
connections. One owner can enforce fair queues, quotas, health, and recovery.

**Why not automatically:** a daemon adds installation, supervision, IPC compatibility,
local authentication, upgrades, and a workstation failure boundary. Embedded mode is
the simpler and better design for one small process.

**Adoption trigger:** measured duplicate work, aggregate cache memory, thread count,
SQLite contention, or queue age breaks the declared multi-agent workstation profile.
Agent count alone is not sufficient.

**Exit gate:** declared ten-agent workload meets local append, prompt, and recovery SLOs.

## Milestone 4 — context intelligence

- Derived thread-state capsules with objectives, decisions, constraints, and open work.
- Pluggable protection and release policies with visible explanations.
- Branch/fork inheritance and explicit context snapshots.
- Summaries as versioned navigation aids while retaining originals.
- Retrieval routing across thread, personal, and project scopes.
- Quality evaluation for focus shifts, stale state, contradictions, and supersession.

**Why:** raw retrieval may repeatedly rediscover objectives and decisions poorly, while
explicit snapshots can make forks reproducible and scope selection explainable.

**Why not automatically:** summaries and capsules can hallucinate, hide qualifications,
or preserve superseded instructions. Automatic protection can fill the prompt with
stale state. Derived context must remain versioned, explainable, and linked to originals.

**Adoption trigger:** curated long-thread evaluations demonstrate continuity, branch, or
scope failures that simpler recent/protected/retrieved paging does not solve.

**Exit gate:** agent-thread evaluations demonstrate improved continuity without hidden
instruction or stale-context regressions.

## Milestone 5 — selective team memory

- Define a knowledge-card and promotion-review format.
- Add device identity, projects, principals, scopes, provenance, and tombstones.
- Enforce authorization before retrieval and hydration.
- Implement idempotent sync outbox/inbox and durable cursor recovery.
- Compare Redis Streams, NATS JetStream, and alternatives with failure tests.
- Keep the team plane optional and outside the local prompt critical path.

**Why:** teams need reusable decisions and evidence across workstations, with provenance,
authorization, replay, deletion, and offline recovery.

**Why not automatically:** synchronizing raw threads expands privacy and compliance
scope. A broker transports events but does not define promotion, identity, conflicts, or
revocation. A small team may be better served first by reviewed cards and a simple
authenticated database sync API.

**Adoption trigger:** multiple principals need approved knowledge across independently
failing devices. Authorization is mandatory before shared retrieval; a broker is added
only when fan-out, event rate, or replay requirements exceed the simpler sync path.

**Exit gate:** current-policy authorization tests show zero cross-project leakage;
offline nodes continue local prompt construction; and the deployment explicitly chooses
an authorization-lease or maximum-revocation-lag contract for cached team data.

## Questions looking for owners

### Context policy

- What is the correct high-watermark policy for intervention?
- Which instructions should be protected automatically?
- How should conflicting protected items be surfaced and resolved?
- Should older protected events be truncated, summarized, or cause a hard error?

### Retrieval quality

- What benchmark represents real agentic recall rather than generic document QA?
- How should recency compete with durable decisions and superseding facts?
- When should a retrieval miss trigger clarification instead of speculative recall?
- How should the system quantify provenance and confidence in the prompt?

### Branches and collaboration

- What context should a child thread inherit at its fork point?
- How should two thread branches merge decisions without merging raw transcripts?
- What makes a local memory worthy of project or team promotion?
- How should team members inspect, dispute, and supersede promoted knowledge?

### Systems engineering

- Which local ANN implementation provides the best reliability-to-complexity ratio?
- Can a single daemon safely support many agents without becoming a workstation SPOF?
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
- Use an RFC issue before large schema, broker, or retrieval-policy changes.
- Contribute small adapters behind interfaces rather than coupling the core to a vendor.
- Add failure and privacy tests with every distributed-systems feature.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.
