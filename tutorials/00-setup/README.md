# Chapter 00 — Setup your dev environment

> **Post:** [https://nitinksingh.com/posts/maf-v1-00-setup/](https://nitinksingh.com/posts/maf-v1-00-setup/) — concept, diagrams, walkthrough.

Everything you need installed before chapter 1 — uv, .NET 9, Docker, OpenAI keys, and a one-shot verify script.

## What to do

This chapter walks you through installing the toolchain. There is no demo code in this folder — the code starts in [Chapter 01](../01-first-agent/).

Steps are in the full article, but the short version:

```bash
# Install uv (Python), .NET 9 SDK, Docker + Compose v2, Node 20 + pnpm
# — see the article for per-OS commands.

git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
cp .env.example .env
# edit .env — pick one LLM provider, below.

./scripts/verify-setup.sh   # checks every prerequisite, prints pass/fail
./scripts/dev.sh            # brings up the full Python stack
```

## Environment variables

Set one of the blocks in repo-root `.env`:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`), `EMBEDDING_MODEL` (default `text-embedding-3-small`), `LLM_BASE_URL` — see below |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2025-03-01-preview`), `AZURE_EMBEDDING_DEPLOYMENT` |
| **Replay** (no key needed) | — | `REPLAY_FIXTURES_DIR`, `RECORD`, `REPLAY_RECORD_PROVIDER` — see below |

`LLM_PROVIDER` selects the block (`openai`, `azure`, or `replay`). `JWT_SECRET` and `AGENT_SHARED_SECRET` stay at their defaults for local dev.

Full variable reference with purpose and defaults: see the [full article](https://nitinksingh.com/posts/maf-v1-00-setup/#environment-variable-reference).

### Don't have a paid API key? Two options

**Option 1 — GitHub Models (free, real model, `LLM_PROVIDER=openai`).** GitHub Models
exposes an OpenAI-compatible endpoint, free with a GitHub personal access token:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=<a GitHub PAT with the models:read scope>
LLM_BASE_URL=https://models.inference.ai.azure.com
LLM_MODEL=gpt-4o
```

`LLM_BASE_URL` works with any OpenAI-compatible endpoint the same way — OpenRouter, a
local vLLM/LM Studio server, etc. — not just GitHub Models.

**Option 2 — Replay (free, no network, no key at all).** Every chapter's `tests/`
directory ships committed fixtures recorded against a real model. Set
`LLM_PROVIDER=replay` and the chapter's own client construction plays them back with
zero credentials:

```bash
LLM_PROVIDER=replay uv run --project tutorials python tutorials/01-first-agent/python/main.py
```

This is also what lets the tutorial test suite run in CI without secrets — see
`shared/replay_client.py` (production) or `tutorials/_shared/replay_client.py`
(tutorials) for how it works, and each chapter's own test file for the recorded
question it plays back. To record a fixture yourself (e.g. after changing a chapter's
prompt or question), set `RECORD=true` and a real provider's credentials —
`REPLAY_RECORD_PROVIDER` picks which one (`openai` or `azure`, default `openai`):

```bash
LLM_PROVIDER=replay RECORD=true REPLAY_RECORD_PROVIDER=azure \
  uv run --project tutorials python tutorials/01-first-agent/python/main.py "your question"
```

Re-run without `RECORD` afterward to confirm it replays deterministically, and commit
the new fixture file under that chapter's `tests/fixtures/replay/`.

## Troubleshooting

Common `verify-setup.sh` failures and fixes are in the [Troubleshooting section](https://nitinksingh.com/posts/maf-v1-00-setup/#troubleshooting) of the article.

## Learn more

- **Full article:** [maf-v1-00-setup](https://nitinksingh.com/posts/maf-v1-00-setup/)
- [Series index](../README.md) · Next: [Chapter 01 — Your First Agent](../01-first-agent/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
