<!-- technical-summary:start -->
## Plain-English Technical Summary

<!-- Replace all REPLACE_WITH tokens. Write one or two total summary sentences. -->
<!-- One useful form makes the risk sentence one, then joins the fix and state in sentence two. -->
<!-- technical-risk:start -->REPLACE_WITH_FAILURE_RISK<!-- technical-risk:end -->
<!-- technical-fix:start -->REPLACE_WITH_MECHANICAL_FIX<!-- technical-fix:end -->
<!-- technical-state:start -->REPLACE_WITH_GUARANTEED_STATE<!-- technical-state:end -->

**Key Concepts Explained**

* **"REPLACE_WITH_TERM_1":** REPLACE_WITH_DEFINITION_AND_RUNTIME_IMPACT_1
* **"REPLACE_WITH_TERM_2":** REPLACE_WITH_DEFINITION_AND_RUNTIME_IMPACT_2
* **"REPLACE_WITH_TERM_3":** REPLACE_WITH_DEFINITION_AND_RUNTIME_IMPACT_3
<!-- technical-summary:end -->

## Problem

What problem does this change solve?

## Approach and trade-offs

Describe the implementation. Describe viable alternatives. Identify important failure
behavior.

## Decision context

- Why does this change help or correct the declared baseline workload?
- Who should not enable or adopt it yet?
- What measured trigger justified implementation?
- What result would cause us to reject or roll it back?
- Which non-goals apply?
- Which operational responsibilities does the change add?

Link the decision brief or architecture decision record (ADR) for a high-impact change.
High-impact changes affect schema, ordering, visibility, promotion, prompt policy,
broker guarantees, or a public application programming interface.

## Contract and evidence

- Affected ADR acceptance invariant identifiers:
- Contract, migration, concurrency, fault, or benchmark artifacts:
- Rollback boundary:

## Invariants

- [ ] The system stores durable context before it removes the prompt copy.
- [ ] Prompt budgets remain enforced.
- [ ] Recent unindexed events remain visible where applicable.
- [ ] Queue overflow and retry behavior remain lossless where applicable.
- [ ] Local prompt construction remains independent of remote services.
- [ ] Privacy and authorization boundaries are not weakened.
- [ ] Thread identity and scope routing remain isolated across negative access paths.
- [ ] Worker, queue, registry, desk, and request bounds remain explicit.

## Verification

- [ ] `python -m compileall -q context_cache library_of_context`
- [ ] `python -m unittest discover -s tests -v`
- [ ] `ruff check context_cache library_of_context tests examples`
- [ ] `ruff format --check context_cache library_of_context tests examples`
- [ ] `codespell .`
- [ ] `mkdocs build --strict`
- [ ] Documentation defines public behavior.
- [ ] Dense engineering prose names the component, action, condition, and result.
- [ ] Concurrency prose preserves ownership, order, atomic boundaries, and failure behavior.
- [ ] The technical summary states the risk, fix, and guaranteed state in that order.
- [ ] Each summary claim matches the implementation and linked evidence.
- [ ] Each key-concept bullet defines its term and states its runtime impact.
- [ ] Performance changes include quality and resource evidence.
- [ ] The evidence gate can reject the change as well as confirm it.

## Specialist reviews

- [ ] Security and privacy review for thread keys, scopes, promotion, daemon transport,
      credentials, or team routing.
- [ ] Storage and migration review for schema, transactions, outbox, or rollback.
- [ ] Concurrency review for locks, leases, workers, queues, or schedulers.
- [ ] Interface compatibility review for Python, Model Context Protocol, Hypertext
      Transfer Protocol, command-line, or configuration changes.
- [ ] Retrieval evaluation for ranking, filtering, embedding, or cache changes.
- [ ] Technical documentation review for public prose and examples.

Mark each review that does not apply. Link evidence for each required review.

## Compatibility and migration

Describe schema, interface, configuration, and operational migration effects. Write
`none` when there is no effect.

Follow the [technical language guide](../docs/TECHNICAL_LANGUAGE.md),
[glossary](../docs/GLOSSARY.md), and
[skill profile](../.agents/skills/write-timeless-technical-prose/SKILL.md). Preserve
code identifiers and quoted text exactly.
