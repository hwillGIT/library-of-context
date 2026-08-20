# Contributing

Thank you for helping build the Library of Context. Contributions are welcome from AI
application developers, retrieval researchers, database and distributed-systems
engineers, privacy specialists, technical writers, and curious users.

## Start with the design contract

Changes must preserve these invariants:

- acknowledged context is durable before it can leave the model prompt;
- prompt size is bounded;
- recent unindexed events are visible;
- bounded queue overflow never becomes durable data loss;
- SQLite is authoritative in local mode;
- remote/team services do not become mandatory for local prompt construction;
- authorization is applied before shared-context hydration.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and
[docs/CONTEXT_GOVERNOR.md](docs/CONTEXT_GOVERNOR.md) before changing the lifecycle.
Read [Why These Improvements?](docs/WHY_THE_ROADMAP.md) before implementing a roadmap
item. Each item has an adoption trigger and an evidence gate.

## Development setup

Windows PowerShell:

```powershell
git clone https://github.com/hwillGIT/library-of-context.git
cd library-of-context
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

macOS or Linux:

```bash
git clone https://github.com/hwillGIT/library-of-context.git
cd library-of-context
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

No external dependency or Redis service is needed for the default test suite. To run
the live Redis integration test:

```powershell
$env:CONTEXT_CACHE_TEST_REDIS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Do not run this against a shared production Redis instance.

## Good first contribution areas

- Add adversarial prompt-budget tests for code, CJK, and long tool payloads.
- Improve error messages and examples.
- Add platform setup notes for Linux and macOS.
- Build benchmark corpus generators and machine-readable result output.
- Add queue, crash, retry, Redis-loss, and disk-pressure tests.
- Improve the diagrams and contributor documentation.

Larger areas needing design discussion include ANN adapters, model tokenizers, daemon
topology, scope/ACL routing, knowledge promotion, and team broker selection.

## Issue and RFC workflow

- Use a bug issue for reproducible incorrect behavior.
- Use a research-question issue when the desired behavior is not yet known.
- Use a feature issue for bounded additions with a clear contract.
- Open an RFC issue before changing durable schema semantics, ordering, privacy scope,
  broker guarantees, prompt policy, or public APIs.

An RFC should explain the problem, constraints, at least two viable options, failure
modes, migration path, security implications, performance/quality evaluation, and open
questions.

For a substantial improvement, copy the
[Improvement Decision Brief Template](docs/DECISION_BRIEF_TEMPLATE.md). The brief must
include both **why** and **why not**, non-goals, the option to defer, a measurable adoption
trigger, an evidence gate, new failure modes, compatibility, rollback, and operational
ownership. “This architecture is more scalable” is not an adoption trigger.

## Pull requests

Keep a pull request focused. Include:

1. the problem and intended behavior;
2. implementation and important trade-offs;
3. tests, including recovery or failure tests when relevant;
4. documentation for public behavior;
5. benchmark and retrieval-quality evidence for performance changes;
6. compatibility or migration notes for schema/API changes.

Avoid unrelated formatting or dependency changes. New runtime dependencies need a clear
benefit, maintenance assessment, license check, and dependency-free fallback discussion.

## Testing expectations

At minimum, run:

```bash
python -m compileall -q context_cache library_of_context
python -m unittest discover -s tests -v
```

Lifecycle changes should test:

- durable-before-prompt ordering;
- idempotent retries;
- prompt-budget enforcement;
- recent-overlay visibility before indexing;
- work-ring overflow and outbox recovery;
- protected-context behavior;
- restart recovery and watermarks;
- HTTP and MCP contract compatibility when applicable.

Retrieval optimizations must report both latency/resource changes and quality changes
against the exact scorer.

## Code style

- Support Python 3.11 and newer.
- Prefer standard-library implementations in the core.
- Use type hints and small explicit data contracts.
- Favor plain technical English and document failure behavior.
- Keep provider-specific code behind adapters.
- Avoid silently weakening durability, privacy, ordering, or budget guarantees.

## Editorial standard

Write explanatory documentation, examples, help text, docstrings, and comments in one
editorial present. Describe the system contract and operational rationale, not the
sequence of edits or the author's process. Do not refer to the prompt, requester,
drafting process, or refactoring process. Do not describe when one section, component,
example, or explanation was added relative to another. Present the complete structure
directly.

Put completed change history in `CHANGELOG.md`; capability state and future sequence in
the status and roadmap documents; version transitions in compatibility or migration
sections; and publication or access dates in citations. Preserve time-relative words
when they identify actual runtime state.

Use the repository's
[timeless technical prose skill](.agents/skills/write-timeless-technical-prose/SKILL.md)
for the full editing and review procedure. Prefer concrete mechanisms and measurable
claims over promotional terms or unsupported comparisons.
Do not use updated, latest, or improved as freestanding labels for explanatory material.
State the resulting behavior, version boundary, or measured comparison instead.
Use sequence words only when they specify technical order, including procedures,
runtime transitions, dependencies, migrations, and evaluation protocols.

## Security and privacy

Do not include real prompts, credentials, tokens, private documents, or customer data in
issues, fixtures, logs, or benchmark corpora. Follow [SECURITY.md](SECURITY.md) for
vulnerability reports.

## Community

Be direct, curious, and constructive. Critique designs with evidence and alternatives.
Assume contributors are trying to improve the system, and make disagreement useful.
Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
