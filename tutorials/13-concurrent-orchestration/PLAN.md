# Chapter 13 — Concurrent Orchestration

## Goal

Teach `ConcurrentBuilder` — multiple agents process the same input in parallel; results feed an aggregator.

## Article mapping

- **Partially supersedes**: [Part 11 — Graph-Based Workflows](https://nitinksingh.com/posts/graph-based-workflows--beyond-simple-orchestration/)
- **New slug**: `/posts/maf-v1-concurrent-orchestration/`

## Teaching strategy

- [x] Refactor candidate — `agents/workflows/pre_purchase.py:35` is a custom concurrent workflow using `asyncio.gather`. Ch13 shows a simplified Concurrent example; Phase 7 `plans/refactor/08-pre-purchase-concurrent.md` refactors the real workflow.

## Deliverables

### `python/`
- `main.py` — 3 agents analyze a product launch concurrently: `Researcher`, `Marketer`, `Legal`. Custom aggregator summarizes.
- `tests/test_concurrent.py` — ≥ 4 tests: all three run in parallel; aggregator sees all results; slowest determines latency; failure in one branch reported but others complete.

### `dotnet/`
- `ConcurrentBuilder.BuildConcurrent(agents)`.

### Article
- Barriers and fan-in: how MAF ensures aggregator waits for all branches.
- Latency vs throughput trade-offs.

## Verification

- Three agents complete in roughly the same wall-clock time as one of them (demonstrating parallelism).

## How this maps into the capstone

`agents/workflows/pre_purchase.py` will be refactored to Concurrent in Phase 7 — with reviews, stock, and price-history as parallel branches.

## Out of scope

- Voting ensembles — brief mention.
- Magentic manager (Ch16).
