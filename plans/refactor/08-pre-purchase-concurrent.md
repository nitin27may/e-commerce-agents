# Refactor 08 — Pre-Purchase to Concurrent Workflow

## Goal

Replace `agents/workflows/pre_purchase.py:35` `PrePurchaseWorkflow` with a MAF `WorkflowBuilder` using Concurrent orchestration.

## Deliverables

- `agents/workflows/pre_purchase.py` rewritten:
  - `ParseRequestExecutor` — extracts product IDs and intent.
  - Fan-out to three agent-executors in parallel: `ReviewSummarizer`, `StockChecker`, `PriceHistoryFetcher`.
  - Sequential step after `StockChecker`: `ShippingEstimator`.
  - Fan-in barrier to `SynthesisAgent` → `yield_output` with the final recommendation.
- Expose via `POST /api/workflows/pre-purchase` so the frontend can trigger it directly.
- Register the workflow in `shared/workflow_factory.py` for visualization.

## Test file

`agents/tests/test_workflow_pre_purchase.py` — minimum 6 tests:

- Three parallel executors run concurrently (wall-clock ≈ max(each), not sum).
- Shipping waits for stock.
- Synthesis aggregates all inputs.
- Failure in one branch: graceful degradation (other branches still inform synthesis).
- Empty product list: workflow emits `WorkflowOutputEvent` with explanatory message.
- Event stream contains expected `ExecutorInvokedEvent`s for every node.

## Verification

- Pre-refactor vs post-refactor parity test returns equivalent recommendations on canned inputs.
- Mermaid diagram committed at `docs/workflows/pre_purchase.mmd`.

## Out of scope

- Caching of stock/price history (future follow-up).
