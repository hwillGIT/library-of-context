# Improvement Decision Brief Template

Use this template for a substantial retrieval, storage, context-policy, daemon, broker,
security, or public-interface change. The template presents supporting and opposing
cases before implementation.

A small bug fix does not need a brief when tests or an invariant define the correct
behavior. The [glossary](GLOSSARY.md) defines shared terms.

```markdown
# <Improvement name>

Status: proposed | investigating | accepted | deferred | rejected | shipped
Class: correctness | evidence | scale-triggered | optional accelerator | team-only | research
Owner: <GitHub handle or unowned>
Issue/RFC: <issue or request for comments link>
Last reviewed: YYYY-MM-DD

## Problem and current evidence

Describe the observable failure, workload, or quality problem. Include measured
behavior. Identify affected users and the invariant at risk.

## Desired outcome

State the public behavior or quality result. Do not prescribe an implementation unless
the decision requires that implementation.

## Why make this change

Explain the benefit. Identify the failure that the change prevents.

## Why not make this change

Explain complexity and cost. Describe possible recall or latency regressions. Identify
failure modes, privacy effects, and operational ownership. State when the baseline is
preferable.

## Non-goals

List related problems outside the proposal scope.

## Alternatives

Include at least two viable options when possible. Include the baseline or deferral
option. State when that option is reasonable.

## Recommended seam or contract

Describe the adapter, schema, interface, policy boundary, or state machine. Explain how
this boundary permits replacement. Identify authoritative and rebuildable state.

## Dependencies and ordering

Identify required correctness, measurement, identity, schema, or security work. State
whether this work can proceed independently.

## Adoption trigger

Give a measurable condition for adopting the added complexity. Examples include an
exceeded 95th-percentile latency or resident-memory limit. Other examples include
duplicate local work, cross-user deployment, or a demonstrated continuity failure.

## Evidence gate

Define the measurements and tests for acceptance. Performance work must measure
retrieval quality and resource cost. Distributed work must test crashes, duplicates,
reordering, offline operation, and recovery. Security work must test denial and
revocation.

## Failure, privacy, and overload behavior

Describe timeout, full-queue, full-disk, process-stop, network-partition, stale-access,
malformed-data, and partial-migration behavior. Identify data that can leave the
workstation. Identify data that can enter logs or metrics.

## Compatibility, migration, and rollback

Cover schema, interface, and configuration compatibility. Cover mixed versions, index
rebuilds, cache invalidation, interrupted migrations, rollback, and old-data cleanup.

## Open decisions

List unresolved questions and decision owners. Identify the evidence that will resolve
each question.
```

## Review standard

A brief is ready for implementation discussion when a reviewer can answer these
questions:

- Why does this change help the declared workload?
- Which users should not enable it?
- What failure can it introduce?
- What triggers adoption and what evidence permits rejection?
- Can the local Library still operate if this component is absent or unavailable?
- Can the change be disabled, rebuilt, migrated, and rolled back safely?

Link the completed brief to benchmarks, threat models, architecture decision records,
and implementation issues. Do not duplicate those materials.
