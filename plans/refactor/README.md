# Refactor Sub-Plans

Transform the Python e-commerce app into a reference implementation of MAF best practices. Each sub-plan is one PR, behind a feature flag where behavior changes. Reuses every existing env var; adds new env vars only as **optional** with defaults that preserve current behavior.

## Order (dependency-respecting)

1. [`00-readiness-and-strategy.md`](./00-readiness-and-strategy.md) — overall strategy, feature flags, rollback.
2. [`01-env-var-alignment.md`](./01-env-var-alignment.md) — preserve existing names, add aliases for MAF conventions.
3. [`02-config-loader.md`](./02-config-loader.md) — centralize all config in `shared/config.py`.
4. [`07-context-providers-cleanup.md`](./07-context-providers-cleanup.md) — tighten to MAF v1 idioms (no contract change).
5. [`06-session-and-history.md`](./06-session-and-history.md) — adopt MAF `AgentSession`.
6. [`03-retire-agent-host-custom-loop.md`](./03-retire-agent-host-custom-loop.md) — replace custom OpenAI loop with MAF native execution.
7. [`04-specialist-agents-native-execution.md`](./04-specialist-agents-native-execution.md) — specialists call `agent.run` directly.
8. [`05-middleware-agent-function-chat.md`](./05-middleware-agent-function-chat.md) — add MAF middleware layers.
9. [`11-checkpointing.md`](./11-checkpointing.md) — Postgres-backed `CheckpointStorage`.
10. [`08-pre-purchase-concurrent.md`](./08-pre-purchase-concurrent.md) — Concurrent workflow.
11. [`09-return-replace-sequential-hitl.md`](./09-return-replace-sequential-hitl.md) — Sequential + HITL.
12. [`10-orchestrator-to-handoff.md`](./10-orchestrator-to-handoff.md) — HandoffBuilder replaces tool-based routing.
13. [`12-declarative-workflows.md`](./12-declarative-workflows.md) — YAML workflow DSL.
14. [`13-visualization.md`](./13-visualization.md) — Mermaid/DOT export for every workflow.

Each sub-plan lists the test file it introduces. See master plan for coverage floors (80% shared, 70% elsewhere).
