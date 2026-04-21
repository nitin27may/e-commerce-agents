# Chapter 01 — Your First Agent

## Goal

Teach `ChatAgent` (Python) / `ChatClientAgent` (.NET) creation — the minimum code to go from editor to a working agent that answers a question.

## Article mapping

- **Supersedes**: [Part 1 — AI Agents Concepts and Your First Implementation](https://nitinksingh.com/posts/ai-agents-concepts-and-your-first-implementation/) (split into Ch01 + Ch02)
- **New slug**: `/posts/maf-v1-first-agent/`

## Teaching strategy

- [x] Refactor excerpt — minimal form of `agents/orchestrator/agent.py:86`, stripped of tools and context providers. Then point forward at the real version.

## Deliverables

### `python/`
- `pyproject.toml` pinning `agent-framework` (latest v1.x).
- `main.py` — ~40 lines: instantiate `OpenAIChatClient`, wrap in `ChatAgent`, ask a question, print the response.
- `tests/test_first_agent.py` — ≥ 3 tests using MAF's fake chat client: happy path, empty-message error, correct instructions applied.

### `dotnet/`
- `.csproj` referencing `Microsoft.Agents.AI.OpenAI` prerelease.
- `Program.cs` — top-level statements equivalent to `main.py`.
- `tests/FirstAgent.Tests.csproj` — xUnit + FluentAssertions, ≥ 3 tests using a fake `IChatClient`.

### Article
- `README.md` filled from template. Emphasise: agent = chat client + instructions; everything else is opt-in.

## Verification

- `cd python && uv run python main.py` prints an answer to "What's the capital of France?".
- `cd dotnet && dotnet run` prints the same answer.
- Both test suites green.

## How this maps into the capstone

Real usage at `agents/orchestrator/agent.py:86` (Python) — and, after the .NET port, the equivalent in `dotnet/src/ECommerceAgents.Orchestrator/OrchestratorAgent.cs`.

## Out of scope

- Tools (Ch02), streaming (Ch03), memory (Ch04+).
