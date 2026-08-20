# Contributing

Contributions are welcome from agent developers, retrieval researchers, database and
distributed-systems engineers, privacy specialists, and technical writers.

```bash
python -m pip install -e .
python -m compileall -q context_cache library_of_context
python -m unittest discover -s tests -v
```

Changes must preserve durable-before-evictable behavior, bounded prompts, recent-event
visibility, lossless outbox recovery, local independence, and privacy boundaries.

Large persistence, ordering, security, broker, retrieval-policy, or public-API changes
should begin with an RFC issue. Performance changes need quality evidence as well as
latency evidence.

Substantial proposals must explain why the change is justified, why the current design
may remain better, alternatives, non-goals, the measurable adoption trigger, evidence
gate, failure behavior, and rollback. Read [Why These Improvements?](WHY_THE_ROADMAP.md)
and use the [Decision Brief Template](DECISION_BRIEF_TEMPLATE.md).

Read the full
[CONTRIBUTING.md](https://github.com/hwillGIT/library-of-context/blob/main/CONTRIBUTING.md)
for testing expectations and contribution areas.
