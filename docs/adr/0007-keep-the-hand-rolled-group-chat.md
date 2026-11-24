# ADR 0007 — Keep the hand-rolled group chat and orchestration modes

**Status:** Accepted · **Date:** 2026-11-24 · **Closes the decision half of** [#98](https://github.com/nitin27may/e-commerce-agents/issues/98)

## Context

Issue #98 lists a gap between the tutorials and the production app: the app builds its multi-agent
patterns itself while `agent_framework.orchestrations` (1.4.2 here) ships `GroupChatBuilder`,
`SequentialBuilder` and `ConcurrentBuilder`. The issue asks for one of two outcomes: adopt the
builders, or write down why the hand-rolled versions stay. This is the second.

What "hand-rolled" means here needs stating exactly, because it is less than it sounds. The modes are
not a custom engine. `workflows/group_chat.py`, `workflows/pre_purchase.py` and
`workflows/return_replace.py` are built on MAF's `WorkflowBuilder`, `Executor` and `WorkflowContext`:
real MAF graphs with real edges. What this repo writes itself is the pattern layer on top (who speaks,
in what order, what state travels between steps), which is what the builders would supply.

| Mode | Shape in this repo | Builder it resembles |
|---|---|---|
| `group-chat` | Two agent-backed panelists in a fixed order over a shared transcript, then a moderator | `GroupChatBuilder` |
| `workflow:return-replace` | `check, policy-gate, initiate-return, search, discount, finalize`, paused by `ctx.request_info` | `SequentialBuilder` |
| `workflow:pre-purchase` | Fan-out to reviews, stock and price history, merge, synthesize | `ConcurrentBuilder` |

## Decision

Keep `workflows/group_chat.py` and the other hand-built modes. Do not migrate them to the three
builders. `handoff` already uses MAF's `HandoffBuilder` and stays as it is. This is a decision about
the pattern layer only; the modes keep running on `WorkflowBuilder`.

## Why

These are the reasons that hold in this codebase today. Each is something a migration would have to
preserve, not a preference.

**Checkpoint and resume on our own store.** `return-replace` and `pre-purchase` attach
`shared.factory.get_checkpoint_storage()` (the Postgres store, wrapped in `RecordingCheckpointStorage`
so every save is an event and links to the run's `usage_logs` row). Resume builds a fresh `Workflow`
from `checkpoint_id` plus a `ReturnDecision` response. That contract is what `/runs`, the approvals
inbox and the SLA timers sit on. A builder's workflow would have to accept the same storage, produce
the same node ids (the web graph correlates live `node_id` to Mermaid ids, dashes swapped for
underscores) and survive the same resume path. Nothing in the builders' documented surface promises
that, and it has not been tried.

**The human-in-the-loop gate is policy, not a chat turn.** The return gate asks the approval policy
(`config/policies/approvals.yaml`), pauses before anything is created, restores the full
`WorkflowState` on resume, and treats an SLA breach as a rejection. `SequentialBuilder` gives an
ordered pipeline of participants; it does not give a gate that decides who may release it. Wrapping
one would leave the gate as custom code around a builder that no longer owns the control flow.

**Grounding wraps every mode's final text.** `@grounded_run` verifies `run_completed` and puts the
report on the payload, identically for all five modes. The wrapper is applied to the mode's event
stream, so it is indifferent to how the graph inside was built; but the events it expects
(`node_enter`, `node_exit`, `request_info`, `checkpoint`) come from our executors. A builder changes
what is emitted and so what the translation in `orchestrator/events.py` has to cope with.

**Replay fixtures are keyed on the exact request.** The keyless CI gate, the no-key demo and the
eval suites match a recorded answer by a hash of every message and tool schema. The group-chat
panelists each send a specific prompt and transcript. A builder that frames turns differently (a
speaker-selection prompt, its own system message, a different way of passing the transcript) changes
every key, so every recording for that mode becomes a miss. That is a re-record of the fixtures for
the mode and a new live-key spend to do it, for no change a user would see.

**One implementation on two stacks.** `Modes/GroupChatMode.cs` reproduces the same panelists, prompts
and sequential transcript on .NET (`docs/parity-matrix.md`, row 12). The two stacks share a contract
because both are small and explicit. Adopting the Python builder moves the Python behaviour to
whatever the builder does, and the .NET side would have to follow it by hand or diverge.

**The group chat is small.** `group_chat.py` is about 150 lines, two panelists and a moderator that
is a local synthesizer (no model call). A selection manager, round limits and termination conditions
are what `GroupChatBuilder` adds, and none is needed for a fixed two-voice panel.

## Consequences

- The three modes keep their own code and their own tests; a MAF release that changes a builder
  costs nothing here, and one that changes `WorkflowBuilder` is covered by the replay gate
  (`scripts/replay-evals.sh`), as before.
- `group-chat` has no checkpoints (`supports_checkpoints=False`) and no gate. That is a property of
  the mode, not of the decision: the reasons above are weakest for it.
- A reader looking for "how MAF's builders are used for real" will not find them for these three
  patterns. `HandoffBuilder` is the one used in production, and the tutorials cover the others.

## What would change this decision

- **A builder that accepts a `CheckpointStorage` and an approval pause**, and whose resumed run
  reproduces the same node ids and events, shown by porting `return-replace` and passing the existing
  resume and approvals tests unchanged. That removes the first two reasons.
- **A mode that needs what the builders add.** More than two panelists, a model-chosen next speaker,
  or termination on a condition: the first time one of those is wanted, write it on `GroupChatBuilder`
  rather than extending `group_chat.py`.
- **A re-record that is already scheduled.** If the fixtures for the group-chat panelists must be
  re-recorded for another reason, migrate that mode in the same pass, so the cost of the key spend is
  paid once.
- **The .NET stack gaining the same builders** at preview or better, so the two ports could move
  together.

Reversal is cheap for `group-chat` (one mode, no persisted state) and expensive for the two workflow
modes (persisted checkpoints are tied to the graph that wrote them).
