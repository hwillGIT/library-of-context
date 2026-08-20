# Capability Status

Version 0.3 includes local context storage and governed text-call integration. Team
sync, ACLs, ANN retrieval, model-specific tokenization, and native compaction hooks are
not implemented.

| Capability | Status | Evidence or limitation |
|---|---|---|
| Durable thread events plus transactional outbox | Implemented | SQLite schema, restart and overflow-recovery tests |
| Bounded governed model envelope | Implemented | Python, HTTP, and MCP tests; estimator is approximate |
| Recent-event read-your-own-context overlay | Implemented | Unindexed recent events remain directly visible |
| Explicit protect and release | Implemented | Protected set remains subject to its token budget |
| Bounded work ring with durable spill | Implemented | Ring is dispatch acceleration; outbox is recovery truth |
| Recorded, embedded, and indexed watermarks | Implemented | Embedding and indexing currently advance together |
| SQLite FTS plus exact vector hybrid retrieval | Implemented | FTS output is bounded; vector work grows with namespace size |
| Process RAM cache | Implemented | Disposable byte-estimated LRU |
| Local Redis hot cache | Experimental and optional | Disposable; no TLS; not a broker or source of truth |
| Local Ollama embeddings | Experimental and optional | Local HTTP adapter; no batching or connection pool yet |
| Loopback HTTP service | Development surface | No authentication or TLS; do not expose to a network |
| Python text-agent adapter | Implemented | Automatically governs stateless text callbacks; structured tools and multimodal values need custom adapters |
| STDIO MCP Library and desk tools | Implemented cooperative integration | Cannot seize an undocumented host compaction hook |
| STDIO MCP governor tools | Implemented gateway surface | Useful only when the MCP client owns the subsequent model call |
| Model-accurate tokenizer adapters | Planned correctness work | Needed before claiming a hard limit for a selected model |
| Complete retrieval/cache version identity | Planned correctness work | Required as ranking, model, index, and authorization vary |
| Local ANN and bounded vector candidates | Scale-triggered design | Adopt only after exact retrieval crosses a measured SLO |
| Atomic source-edition publication | Planned conditional work | Needed for concurrent replacement or versioned team provenance |
| Outbox claims, leases, and poison policy | Planned concurrency work | Required before several workers consume the same work reliably |
| Supervised workstation daemon | Scale-triggered design | Embedded mode remains appropriate for solo/small use |
| Automatic protection, capsules, and summaries | Research | Must remain derived, explainable, and linked to originals |
| Branch/fork inheritance and merge | Research | No implemented lineage or semantic conflict protocol |
| Scope routing across personal/project/team | Planned | Team scope requires authorization before retrieval |
| Knowledge-card promotion workflow | Team-only design | Not implemented; raw threads remain local by default |
| Shared identity, ACL, tombstones, and audit | Team-only design | Mandatory before any mixed-trust catalog |
| Durable team sync or broker | Team-only design | No broker is deployed; simple database sync should be tested first |
| Cloud control plane | Optional future design | Must remain outside local prompt construction |

## Status meanings

- **Implemented:** covered by the current public code and functional tests.
- **Experimental:** usable for local evaluation but not a production support claim.
- **Development surface:** limited to its stated boundary and unsafe to expose beyond
  it.
- **Planned:** a design direction that still needs its adoption trigger and evidence
  gate satisfied.
- **Scale-triggered:** not inherently better; add only after measured local pressure.
- **Research:** desired behavior is not settled and may be rejected after evaluation.
- **Team-only:** irrelevant to the default single-user local installation.

For the rationale and counterarguments behind planned work, read
[Why These Improvements?](WHY_THE_ROADMAP.md). For sequencing, read the
[Roadmap](roadmap.md).
