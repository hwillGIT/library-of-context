## Problem

What problem does this change solve?

## Approach and trade-offs

Describe the implementation, alternatives considered, and important failure behavior.

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

## Compatibility and migration

Describe schema, API, configuration, or operational migration impact. Write “none” when
there is no impact.
