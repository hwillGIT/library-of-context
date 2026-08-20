# Repository instructions

## Technical prose

Apply [the timeless technical prose skill](.agents/skills/write-timeless-technical-prose/SKILL.md)
when creating or editing documentation, READMEs, architecture text, examples, API or CLI
help, docstrings, and code comments.

Write explanatory material in one editorial present. Describe the system's behavior,
interfaces, constraints, failure modes, and operational rationale. Do not narrate the
prompt, requester, author decisions, drafting process, refactoring process, or sequence
of edits. Do not imply when a section, example, class, module, feature, or explanation
entered the repository relative to other content. State the complete structure and
contract directly.

Put completed change history in `CHANGELOG.md`; capability state and future sequence in
`docs/STATUS.md` and `ROADMAP.md`; version transitions in compatibility or migration
sections; and publication or access dates in citations. Preserve time-relative words
when they identify real runtime state, such as the current request, recent ring, new
snapshot, or previous desk.

Use plain standard technical English. Replace promotional or vague language with the
specific mechanism, condition, and effect. Do not claim that a design is better,
scalable, robust, or production-ready without a stated comparison and evidence.
Do not label explanatory material as updated, latest, or improved; state the resulting
behavior, or identify the relevant version and measured baseline when time or comparison
is part of the contract. Use before, after, first, next, and similar sequence words only
for technical ordering such as runtime flow, procedures, dependencies, migrations, and
evaluation protocols.
