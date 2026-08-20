# Improvement Decision Brief Template

Use this template before proposing a substantial retrieval, storage, context-policy,
daemon, broker, security, or public-API change. Its purpose is to make the affirmative
case and the skeptical case reviewable before implementation begins.

Small bug fixes do not need a separate brief when the correct behavior is already
defined by tests or an existing invariant.

```markdown
# <Improvement name>

Status: proposed | investigating | accepted | deferred | rejected | shipped
Class: correctness | evidence | scale-triggered | optional accelerator | team-only | research
Owner: <GitHub handle or unowned>
Issue/RFC: <link>
Last reviewed: YYYY-MM-DD

## Problem and current evidence

What observable failure, workload, or quality problem exists now? Include current
implementation behavior, measurements, affected users, and the invariant at risk.

## Desired outcome

State the public behavior or non-functional result. Avoid prescribing an implementation
unless the implementation itself is the decision.

## Why make this change

Explain the benefit and the failure mode it prevents.

## Why not make this change

Explain complexity, cost, recall or latency regressions, new failure modes, privacy
expansion, operational ownership, and situations where the current design is better.

## Non-goals

List adjacent problems outside the proposal's scope.

## Alternatives

Include at least two viable options when available. Always include “keep the current
design” or “defer” and state when that option remains reasonable.

## Recommended seam or contract

Describe the adapter, schema, API, policy boundary, or state machine that keeps the
change replaceable. Identify authoritative and rebuildable state.

## Dependencies and ordering

What correctness, measurement, identity, schema, or security work must exist first?
Can this work proceed independently of other roadmap branches?

## Adoption trigger

Give the measurable condition that makes the added complexity worthwhile. Examples:
declared p95/RSS limit exceeded, several local processes duplicating work, first
cross-principal deployment, or demonstrated continuity failure.

## Evidence gate

Define the measurements and tests required to accept the change. Performance work must
include retrieval quality and resource cost. Distributed work must include crash,
duplicate, reorder, offline, and recovery behavior. Security work must include deny and
revocation tests.

## Failure, privacy, and overload behavior

What happens on timeout, queue saturation, disk full, process stop, network partition,
stale authorization, malformed data, and partial migration? Which data may leave the
workstation, enter logs, or appear in metrics?

## Compatibility, migration, and rollback

Cover schema/API/configuration compatibility, mixed versions, index rebuild, cache
invalidation, interrupted migration, rollback, and old-data cleanup.

## Open decisions

List unresolved questions, decision owners, and the experiment or evidence that will
close each one.
```

## Review standard

A brief is ready for implementation discussion when a reviewer can answer:

- Why is this better than leaving the system alone for the declared workload?
- Which users should not enable it?
- What new failure can it introduce?
- What triggers adoption and what evidence permits rejection?
- Can the local Library still operate if this component is absent or unavailable?
- Can the change be disabled, rebuilt, migrated, and rolled back safely?

The completed brief should link to benchmark artifacts, threat models, architecture
decision records, and implementation issues rather than duplicating those materials.
