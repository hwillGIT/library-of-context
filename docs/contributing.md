# Contributing

The project welcomes contributions from developers, researchers, engineers, privacy
specialists, and technical writers. The [glossary](GLOSSARY.md) defines shared terms.
Preserve code identifiers and quoted text exactly.

For dense engineering text, name the component and its action. Preserve concurrency
ownership, operation order, atomic boundaries, failure behavior, and resource limits.

```bash
python -m pip install -e .
python -m compileall -q context_cache library_of_context
python -m unittest discover -s tests -v
```

Changes must preserve durable storage before eviction. They must preserve bounded
prompts, recent-event visibility, outbox recovery, local operation, and privacy
boundaries.

Open a request for comments (RFC) issue for a high-impact change. High-impact changes
affect persistence, ordering, security, message brokers, retrieval policy, or a public
application programming interface.

Provide quality and latency evidence for a performance change.

Write explanatory prose in one editorial present. State behavior, constraints, failure
modes, and operational reasons. Do not narrate the prompt, author process, or edit
history. Do not state when explanatory content entered the repository relative to other
content. Put necessary chronology in history, status, plan, migration, compatibility,
or citation documents.

The repository's
[timeless technical prose skill](https://github.com/hwillGIT/library-of-context/blob/main/.agents/skills/write-timeless-technical-prose/SKILL.md)
defines the review procedure. Use active voice and American spelling. Limit descriptive
sentences to 25 words. Limit procedural sentences to 20 words. Do not use contractions
or semicolons.

A substantial proposal must explain its reason and alternatives. It must identify
non-goals, adoption triggers, evidence gates, failure behavior, and rollback.

Read [Why These Improvements?](WHY_THE_ROADMAP.md). Use the
[Decision Brief Template](DECISION_BRIEF_TEMPLATE.md). Follow
[ADR 0001](adr/0001-thread-scope-and-shared-runtime.md) for changes to thread identity,
scope, promotion, runtime ownership, or durable schema. Follow the
[contributor quality workflow](DEVELOPMENT_WORKFLOW.md).

Read the full
[CONTRIBUTING.md](https://github.com/hwillGIT/library-of-context/blob/main/CONTRIBUTING.md)
for testing expectations and contribution areas.
