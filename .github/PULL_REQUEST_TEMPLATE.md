## Problem

What problem does this change solve?

## Approach and trade-offs

Describe the implementation, alternatives considered, and important failure behavior.

## Decision context

- Why is this change better than keeping the current design for the declared workload?
- Who should not enable or adopt it yet?
- What measured trigger justified implementation?
- What result would cause us to reject or roll it back?
- Which non-goals and new operational responsibilities should reviewers understand?

## Invariants

- [ ] Durable context remains safe before prompt eviction.
- [ ] Prompt budgets remain enforced.
- [ ] Recent unindexed events remain visible where applicable.
- [ ] Queue overflow and retry behavior remain lossless where applicable.
- [ ] Local prompt construction remains independent of remote services.
- [ ] Privacy and authorization boundaries are not weakened.

## Verification

- [ ] `python -m compileall -q context_cache library_of_context`
- [ ] `python -m unittest discover -s tests -v`
- [ ] Public behavior is documented.
- [ ] Performance changes include quality and resource evidence.
- [ ] The evidence gate can reject the change as well as confirm it.

## Compatibility and migration

Describe schema, API, configuration, or operational migration impact. Write “none” when
there is no impact.
