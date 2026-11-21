# Durable Task spike: is it a fit for the return lifecycle?

A write-up, not a build. Question: should the return lifecycle ([return-lifecycle.md](return-lifecycle.md), #95)
run on the Durable Task extension for Microsoft Agent Framework instead of the current Postgres checkpoints plus
outbox timers? Nothing was installed or run for this page. Every claim about the extension comes from Microsoft
documentation read on 2026-11-21, and each one that this repo did not exercise is marked.

**Recommendation: keep the current design.** Revisit only if the stack moves to Azure Functions or a hosted
Durable Task Scheduler for other reasons, and then as a port of one workflow, not a rewrite. Reasons are in
[Recommendation](#recommendation).

## What the lifecycle does today

The graph pauses twice with `ctx.request_info(...)`: once for the carrier's first scan, once for staff inspection.
Each pause writes a checkpoint to Postgres (`PostgresCheckpointStorage`), a `hitl_requests` row carrying MAF's resume
token, and an outbox timer, all in one transaction. The outbox worker resumes the run when an event row
(`return.carrier_scanned`) arrives or the timer fires, through the same claim, fence and run-as-requester code a
reviewer's decision uses. State lives in tables the rest of the system already reads: `approvals`, `returns`,
`hitl_requests`, `outbox`.

## What the Durable Task extension is

From Microsoft Learn:

- It "brings durable execution directly into the Microsoft Agent Framework". Registering an agent makes it durable
  with persistent sessions, built-in endpoints and distributed scaling. Internally each agent session is a durable
  entity.
  [Durable Task extension for Microsoft Agent Framework](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-agents-microsoft-agent-framework)
- It also supports MAF graph workflows (`WorkflowBuilder`): "The extension automatically checkpoints each step in
  the graph and recovers from failures without changes to the workflow definition." The same page shows a
  human-in-the-loop workflow using `ctx.request_info()` (Python) or `RequestPort` (.NET), with generated HTTP
  endpoints for run, status and respond.
  [same page](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-agents-microsoft-agent-framework)
- Two hosting approaches: Azure Functions (`agent-framework-azurefunctions`, `AgentFunctionApp`) or bring your own
  compute (`agent-framework-durabletask`, `DurableAIAgentWorker`, a `DurableTaskSchedulerWorker`).
  [Durable extension (Agent Framework docs)](https://learn.microsoft.com/en-us/agent-framework/integrations/durable-extension)
- Backend: the Durable Task Scheduler (DTS), a managed Azure service reached over gRPC with TLS and the app's
  identity, optional private endpoints, a dashboard, and an emulator for local work. Per the docs it works with any
  Azure compute including AKS and Container Apps through the Durable Task SDKs.
  [Durable Task Scheduler](https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler)
- Status: the Python package `agent-framework-durabletask` was listed at a beta version (`1.0.0b260519`) and the
  .NET package `Microsoft.Agents.AI.DurableTask` at a preview version (`1.16.0-preview.261029.1`) in search results
  for this spike. The extension was announced as a public preview in November 2025.
  [PyPI](https://pypi.org/project/agent-framework-durabletask/),
  [NuGet](https://www.nuget.org/packages/microsoft.agents.ai.durabletask/),
  [announcement](https://techcommunity.microsoft.com/blog/appsonazureblog/bulletproof-agents-with-the-durable-task-extension-for-microsoft-agent-framework/4467122).
  I could not load the PyPI page, so the current version and its `agent-framework-core` floor are **not verified**
  for today. The plan's earlier note (durabletask 1.0.0 needing core >= 1.19) predates the repo's move to core 1.26
  and was not re-checked.

## What it would replace

| Today | With Durable Task |
|---|---|
| `PostgresCheckpointStorage` and `shared/checkpoint_storage.py` for the return graph | DTS history and per-step checkpoints |
| `hitl_requests` rows as the resume token for `carrier_scan` and `return_inspection` | A pending `request_info` surfaced by the status endpoint, answered through respond |
| Outbox timers `return.scan_timeout` and `return.inspection_timeout` plus the worker handler | Durable timers inside the orchestration |
| `resume_workflow` claim and fence for these two kinds | The orchestration's own single-writer semantics |

It would not replace the approvals tables, the policy engine, `resolve_return`'s once-only payout, the outbox for
non-return events, or the admin simulator. Those are product state, not execution state.

## What it would add

- Waits measured in days without a polling worker; the platform owns timer delivery.
- A dashboard of paused and failed runs, which the repo builds by hand in `/runs`.
- Retries and replay on activity failure for free.

## Hosting needs and risks

1. **A new Azure dependency.** DTS is a managed Azure resource with its own billing: a Dedicated SKU billed per
   capacity unit, or a Consumption SKU billed per action (the Consumption SKU was described as preview in the
   material found; **GA status not verified**). [Billing](https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler-billing)
   The repo's identity today is "runs offline with Postgres, Redis and a compose file". The keyless demo path
   cannot use DTS.
2. **The emulator cannot show durability.** The docs say it "stores orchestration and entity state in local
   memory", so restarting it loses every paused return. Demonstrating the one thing this feature is for needs a real
   Azure scheduler.
3. **Preview packages on a version treadmill.** The repo already pins MAF exactly and re-keys fixtures per bump. A
   preview package that tracks MAF core adds a second moving part.
4. **Execution model mismatch.** Durable orchestrators replay deterministically; MAF executors that read
   ContextVars, query Postgres or call tools must sit behind activities. This repo's tools read identity from
   ContextVars and run as the original requester; that contract would need to be rebuilt inside activities. **Not
   verified** whether MAF workflow executors run as activities or in the orchestrator in the BYO-compute Python path.
5. **Python and .NET diverge.** The lifecycle is Python-only (parity row 41). The extension has .NET and Python
   packages, but the docs list TTL as .NET only, and nothing says the two stacks could share a run.
6. **Limits.** Payloads and entity state are capped at 1 MB each (large payload support is a workaround). The
   return `WorkflowState` is small today, but conversation-bearing state would not be.
7. **The worker serves both stacks.** The outbox worker handles every deadline in the system (approvals SLA, merchant
   proposals, autonomy). Moving only the return timers leaves two timer systems, which is worse than one.
8. **Auth and tenancy model.** DTS authenticates the app by identity and task hub RBAC. The repo's JWT and
   per-user data rules would still be enforced by our own code; DTS adds no user-level isolation. **Not verified:**
   how the status and respond endpoints authenticate callers in the BYO path.

## Recommendation

Do not adopt it for #95. The current design already does the hard part, and the properties it lacks do not matter
here:

- Durability across restarts is proven against real Postgres, including the claim, fence and double-resume cases
  (return-lifecycle.md). The extension's headline value is the same property.
- The data the product needs (who approved, who inspected, what was paid, once) is in tables the UI and audit trail
  read. A second store for execution state would duplicate and then drift from it.
- The repo runs offline and keyless; DTS needs Azure and its emulator cannot honestly demonstrate durability.
- It is preview, and the integration cost (activities, ContextVars, two timer systems) is higher than the
  maintenance cost of about three modules.

Revisit if any of these becomes true: the product moves to Azure Functions anyway; return waits start to need
fan-out or human-timer patterns the outbox handles badly; the extension reaches GA with a verified Python path for
workflows with `request_info` on bring-your-own compute.

If someone wants to spike it later, the cheapest honest test is one throwaway workflow (the two-pause return graph
with stub executors) on the emulator, to check item 4 above, with no production code and no compose profile
committed.

## Not verified

- Current package versions and their `agent-framework-core` requirements.
- Whether Python workflow HITL is supported on bring-your-own compute (the docs show a Python workflow with
  `request_info` under the Azure Functions tab; the other-compute tab shows only a .NET example).
- DTS Consumption SKU GA status and current prices.
- Whether MAF executors may call Postgres and read ContextVars inside a durable workflow.
- Anything about running it: nothing was installed.
