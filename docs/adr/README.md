# Architecture Decision Records

An architecture decision record (ADR) is a short note that states one design decision, what it
rules out and what would make it wrong. The first five record the decisions that shaped this
repository, each as it was taken at the time; 0006 is a proposal that is not yet decided. Where the system has since moved on, the record
carries a dated update note instead of being rewritten.

Each decision is also argued somewhere in the prose — `docs/architecture.md`,
`docs/roadmap.md` or a module docstring — but nowhere a reader would think to look. A decision
recorded only as a passing sentence inside a 500-line document is indistinguishable from an
accident, and the most common consequence is someone "fixing" it later at some cost.

Each record states what was decided, what it rules out, and — most usefully — **what
would make it wrong**. A decision with no stated reversal condition is dogma.

| # | Decision | Status |
|---|---|---|
| [0001](0001-a2a-over-direct-calls.md) | Specialists talk over A2A HTTP, not in-process calls | Accepted |
| [0002](0002-no-text-to-sql.md) | No text-to-SQL; tools own their queries | Accepted |
| [0003](0003-yaml-prompt-composition.md) | Prompts compose from YAML, never hardcoded strings | Accepted |
| [0004](0004-maf-native-execution.md) | MAF runs the tool-calling loop, not this repo | Accepted |
| [0005](0005-dual-stack-parity.md) | Two backends, one frontend, gated by a real test run | Accepted |
| [0006](0006-foundry-hosted-agents.md) | Run the agents as Foundry hosted agents, one at a time, behind a reachability spike | Proposed |
| [0007](0007-keep-the-hand-rolled-group-chat.md) | Keep the hand-rolled group chat and orchestration modes instead of MAF's builders | Accepted |

## Format

Deliberately short. These are records, not designs. Anything longer than a page here goes
unread, which defeats the purpose.
