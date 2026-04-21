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

Set one of the two blocks in repo-root `.env`:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`), `EMBEDDING_MODEL` (default `text-embedding-3-small`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2025-03-01-preview`), `AZURE_EMBEDDING_DEPLOYMENT` |

`LLM_PROVIDER` selects the block (`openai` or `azure`). `JWT_SECRET` and `AGENT_SHARED_SECRET` stay at their defaults for local dev.

Full variable reference with purpose and defaults: see the [full article](https://nitinksingh.com/posts/maf-v1-00-setup/#environment-variable-reference).

## Troubleshooting

Common `verify-setup.sh` failures and fixes are in the [Troubleshooting section](https://nitinksingh.com/posts/maf-v1-00-setup/#troubleshooting) of the article.

## Learn more

- **Full article:** [maf-v1-00-setup](https://nitinksingh.com/posts/maf-v1-00-setup/)
- [Series index](../README.md) · Next: [Chapter 01 — Your First Agent](../01-first-agent/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
