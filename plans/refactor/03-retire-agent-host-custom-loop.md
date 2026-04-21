# Refactor 03 — Retire Custom OpenAI Tool Loop

## Goal

Replace the custom tool-calling loop in `agents/shared/agent_host.py:24` (`_run_agent_with_tools` + `_run_agent_with_tools_stream`) with MAF-native execution.

## Background

The custom loop was written to dodge an Azure OpenAI API-version incompatibility in an earlier MAF release. We need to verify the current MAF version works against the API version pinned in `docker-compose.yml` (`AZURE_OPENAI_API_VERSION`).

## Deliverables

- **Pre-work check** (documented in the PR description): MAF native execution works against `2025-03-01-preview` (current default).
- Replace every call site of `_run_agent_with_tools*` with `agent.run()` / `agent.run_stream()`.
- Convert `shared/agent_host.py` into a thin SSE adapter that converts `AgentResponseUpdate` → frontend-friendly `data: {"delta": "..."}\n\n` events.
- Keep a compatibility shim behind `MAF_NATIVE_EXECUTION=false` only for documented Azure regions on older API versions.
- Document the original gotcha + resolution in `docs/architecture.md`.

## Test file

`agents/tests/test_native_execution.py` — minimum 4 tests:

- With `MAF_NATIVE_EXECUTION=true`, a canned conversation with tools produces the same observable output as with `=false` (parity).
- Streaming yields chunks progressively (not a single bulk chunk).
- Tool invocation interleaves correctly with streaming deltas.
- Max-iteration safety: a tool that keeps requesting itself terminates cleanly.

## Verification

- Existing Playwright suite green against the refactored backend.
- Latency of a canned 3-turn chat is not worse than before (simple timer in test).

## Out of scope

- Specialist-level changes — those live in sub-plan `04`.
