# Contributing

The project welcomes contributions from developers, researchers, engineers, privacy
specialists, technical writers, and users. The
[glossary](docs/GLOSSARY.md) defines shared technical terms.

Follow the [technical language guide](docs/TECHNICAL_LANGUAGE.md) and the
[skill profile](.agents/skills/write-timeless-technical-prose/SKILL.md). Preserve code
identifiers and quoted text exactly.

For dense engineering text, name the component and its action. Preserve concurrency
ownership, operation order, atomic boundaries, failure behavior, and resource limits.

## Start with the design contract

Each change must preserve these invariants:

- The system stores acknowledged context before it removes that context from a model
  prompt.
- The system bounds each prompt.
- The system makes recent unindexed events visible.
- A full bounded queue does not cause durable data loss.
- SQLite is the authoritative local store.
- Local prompt construction does not require a remote or team service.
- The system authorizes shared context before it loads that context.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before you change the lifecycle. Also read the
[context governor guide](docs/CONTEXT_GOVERNOR.md).

Read [Why These Improvements?](docs/WHY_THE_ROADMAP.md) before you implement a roadmap
item. Each item specifies an adoption trigger and an evidence gate.

Follow [ADR 0001](docs/adr/0001-thread-scope-and-shared-runtime.md) for changes to
thread identity, scope, promotion, runtime ownership, or schema. Follow the
[contributor quality workflow](docs/DEVELOPMENT_WORKFLOW.md).

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

The default test suite does not require an external dependency or Redis service. Redis
is an in-memory data store. Use this command for the live Redis integration test:

```powershell
$env:CONTEXT_CACHE_TEST_REDIS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Do not run this test against a shared production Redis instance.

## Good first contribution areas

- Add prompt-budget tests for code, Chinese, Japanese, Korean, and long tool payloads.
- Improve error messages and examples.
- Add platform setup notes for Linux and macOS.
- Build benchmark corpus generators and machine-readable result output.
- Add queue, crash, retry, Redis-loss, and disk-pressure tests.
- Improve the diagrams and contributor documentation.

Discuss high-impact work before implementation. This work includes approximate nearest
neighbor adapters, model tokenizers, daemon supervision, fair scheduling, access
control, knowledge promotion, and team message brokers.

## Issue and RFC workflow

- Use a bug issue for reproducible incorrect behavior.
- Use a research issue when the required behavior is unknown.
- Use a feature issue for a bounded addition with a clear contract.
- Open a request for comments (RFC) issue for a high-impact change.

An RFC must explain the problem and constraints. It must describe at least two viable
options. It must cover failure modes, migration, security, evaluation, and open
questions.

High-impact changes affect schema meaning, ordering, privacy scope, broker guarantees,
prompt policy, or a public application programming interface.

Copy the [Decision Brief Template](docs/DECISION_BRIEF_TEMPLATE.md) for a substantial
change. Explain why the change is useful. Explain why the baseline can remain
preferable.

Identify non-goals and the option to defer. Define a measurable adoption trigger and
an evidence gate. Describe failure modes, compatibility, rollback, and operational
ownership. A general scale claim is not an adoption trigger.

## Pull requests

Keep each pull request focused. Include:

- A plain-English technical summary that states the failure risk, mechanical fix, and
  guaranteed state in that order.
- At least three key-concept bullets that define terms and their runtime impact.

Keep the hidden risk, fix, and state markers in the pull request template. The
automated check validates structure and sentence limits. Reviewers validate technical
accuracy against the implementation, tests, and evidence.

1. the problem and intended behavior.
2. the implementation and important trade-offs.
3. relevant recovery and failure tests.
4. documentation for public behavior.
5. latency and retrieval-quality evidence for performance changes.
6. compatibility or migration notes for schema or interface changes.

Do not include unrelated formatting or dependency changes. Explain the value of each
runtime dependency. Assess maintenance, licensing, and a dependency-free alternative.

## Testing expectations

At minimum, run:

```bash
python -m compileall -q context_cache library_of_context
python -m unittest discover -s tests -v
```

Test these conditions for a lifecycle change:

- storage before prompt removal.
- idempotent retries.
- prompt-budget enforcement.
- recent-event visibility before indexing.
- work-ring overflow and outbox recovery.
- protected-context behavior.
- restart recovery and watermarks.
- HTTP and MCP contract compatibility when applicable.

HTTP means Hypertext Transfer Protocol. MCP means Model Context Protocol.

Test negative scope access for thread and runtime changes. Test stable
`ThreadKey(collection, session_id)` routing. Test fixed worker counts and bounded
registries.

Test claim expiration, reclaim, terminal quarantine, and explicit retry. Test exclusive
daemon ownership of the database.

A retrieval optimization must report latency, resource use, and quality. Use the exact
scorer in the comparison.

## Code style

- Support Python 3.11 and later versions.
- Prefer Python standard-library implementations in the core.
- Use type hints and small explicit data contracts.
- Use plain technical English.
- Document failure behavior.
- Keep provider-specific code behind adapters.
- Do not weaken durability, privacy, ordering, or budget guarantees without notice.

## Editorial standard

Write explanatory material in one editorial present. Describe the system contract and
operational reasons. Do not narrate the prompt, author process, or edit sequence. Do not
state when explanatory content entered the repository relative to other content.

Put completed history in `CHANGELOG.md`. Put capability state and future sequence in
status and roadmap documents. Put version transitions in migration or compatibility
sections. Put publication and access dates in citations.

Use the repository's
[timeless technical prose skill](.agents/skills/write-timeless-technical-prose/SKILL.md)
for the editing and review procedure. Use active voice and American spelling. Limit
descriptive sentences to 25 words. Limit procedural sentences to 20 words.

Put one instruction in each procedural sentence. Do not use contractions or
semicolons. Limit each paragraph to six sentences and one topic. Define specialized
terms and acronyms at their first use.

## Security and privacy

Do not put private or secret data in issues, fixtures, logs, or benchmark sets. This
data includes real prompts, credentials, tokens, private documents, and customer data.
Follow [SECURITY.md](SECURITY.md) for vulnerability reports.

## Community

Communicate directly and constructively. Critique designs with evidence and
alternatives. Treat disagreement as a source of testable options. Follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
