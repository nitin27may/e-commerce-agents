# Architecture Decision Records

Five decisions that shaped this repository, each one argued somewhere in the prose
already — in `CLAUDE.md`, `docs/architecture.md`, `docs/roadmap.md`, or a module
docstring — and none of them anywhere a reader would think to look.

That is the gap these records close. A decision recorded only as a passing sentence
inside a 500-line document is indistinguishable from an accident, and the most common
consequence is someone "fixing" it later at some cost.

Each record states what was decided, what it rules out, and — most usefully — **what
would make it wrong**. A decision with no stated reversal condition is dogma.

| # | Decision | Status |
|---|---|---|
| [0001](0001-a2a-over-direct-calls.md) | Specialists talk over A2A HTTP, not in-process calls | Accepted |
| [0002](0002-no-text-to-sql.md) | No text-to-SQL; tools own their queries | Accepted |
| [0003](0003-yaml-prompt-composition.md) | Prompts compose from YAML, never hardcoded strings | Accepted |
| [0004](0004-maf-native-execution.md) | MAF runs the tool-calling loop, not this repo | Accepted |
| [0005](0005-dual-stack-parity.md) | Two backends, one frontend, gated by a real test run | Accepted |

## Format

Deliberately short. These are records, not designs — the design lives in
`.claude/plans/`. Anything longer than a page here goes unread, which defeats the
purpose.
