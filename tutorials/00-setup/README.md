---
title: "MAF v1 — Setup your dev environment (Python + .NET)"
date: 2026-04-20
lastmod: 2026-04-20
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, setup, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Everything you need installed before chapter 1 — uv, .NET 9, Docker, OpenAI keys, and a one-shot verify script."
cover:
  image: "img/posts/maf-v1-setup.jpg"
  alt: "Developer laptop with terminals open for Python and .NET"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — This is chapter 0 of *MAF v1: Python and .NET*. The original Python-only series at [Building a Multi-Agent E-Commerce Platform](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/) assumed you had the Python stack ready. This new series covers both languages, so you need a slightly bigger tool belt.

## Why this chapter

The rest of the series runs real code on real LLMs. You need one Python toolchain, one .NET toolchain, Docker for the infra, and a key for either OpenAI or Azure OpenAI. Everything else is written for you.

Do this once and forget it.

## Prerequisites

You need a Unix-like shell. macOS and Linux work out of the box; on Windows use WSL2.

## Step 1 — Install the toolchains

### `uv` (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify:

```bash
uv --version          # expect: uv 0.5.x or later
uv python install 3.12
```

Why `uv` and not `pip`/`poetry`: it resolves and installs 10–100× faster, handles virtualenv creation automatically, and is what the repo is pinned to.

### .NET 9 SDK

- macOS: `brew install --cask dotnet-sdk`
- Ubuntu: follow [Microsoft's official instructions](https://learn.microsoft.com/en-us/dotnet/core/install/linux-ubuntu).
- Windows: download from [dotnet.microsoft.com](https://dotnet.microsoft.com/download).

Verify:

```bash
dotnet --list-sdks    # expect: 9.0.x or later
```

### Docker + Compose v2

Install Docker Desktop (macOS/Windows) or Docker Engine + `docker-compose-plugin` on Linux. `docker compose version` must work.

### Node 20 + pnpm (for the frontend)

```bash
# If you don't have Node:
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install 20

# pnpm:
corepack enable pnpm
```

## Step 2 — Clone and configure

```bash
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
cp .env.example .env
```

Open `.env` and pick **one** provider:

### Option A — OpenAI

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...           # from platform.openai.com/api-keys
LLM_MODEL=gpt-4.1
EMBEDDING_MODEL=text-embedding-3-small
```

### Option B — Azure OpenAI

```dotenv
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_API_VERSION=2025-03-01-preview
AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-small
```

Keep `JWT_SECRET` and `AGENT_SHARED_SECRET` at their defaults for local dev — they're rotated in production only.

## Step 3 — Verify

One script checks everything:

```bash
./scripts/verify-setup.sh
```

You should see something like:

```
Tooling
  ✓ uv (Python package manager)
  ✓ Python 3.12+
  ✓ .NET SDK 9+
  ✓ Docker
  ✓ Docker Compose v2
  ...

Summary
  All 15 checks passed.
```

If any check shows ✗, fix that item and re-run. The script is idempotent.

## Step 4 — Run something

Bring up the full Python stack:

```bash
./scripts/dev.sh
```

- Frontend: http://localhost:3000
- Orchestrator: http://localhost:8080
- Aspire Dashboard (telemetry): http://localhost:18888

Log in with any of the seeded test users (see main README) and type a prompt like *"show me running shoes under $100"*.

To try the .NET stack (as each sub-plan lands):

```bash
docker compose -f docker-compose.dotnet.yml --profile agents up --build
```

Both compose files point the frontend at `:8080`, so you only run one backend at a time.

## Side-by-side differences

There aren't any code differences yet — this chapter is pure tooling. But one thing to note:

| Concern | Python | .NET |
|---------|--------|------|
| Env-var loading | `pydantic-settings` reads `.env` automatically | ASP.NET Core reads env vars + `launchSettings.json`; `.env` needs a small loader (shipped in `ECommerceAgents.Shared`) |
| Package management | `uv sync` | `dotnet restore` (central package management via `Directory.Packages.props`) |

## Gotchas

- **Azure deployment name mismatch.** If the portal shows `gpt-4.1-prod` but your `.env` has `gpt-4.1`, requests fail with 404. Match exactly.
- **`OPENAI_API_KEY=sk-your-openai-api-key-here`**: the placeholder in `.env.example`. `verify-setup.sh` rejects the placeholder; replace it with your real key.
- **Docker Desktop on macOS not starting**: old versions hang silently; reinstall or update if compose commands freeze.
- **Ports 5432/6379/8080 already in use**: stop any local Postgres/Redis/other services or change ports in `docker-compose.yml`.

## Tests

The setup script is the test. Run it in CI to catch toolchain regressions:

```bash
./scripts/verify-setup.sh
echo "exit code: $?"   # 0 if all checks pass
```

## How this shows up in the capstone

`verify-setup.sh` lives alongside `scripts/dev.sh` and is linked from the main `README.md`. It's the single source of truth for "my environment is ready".

## What's next

- Next chapter: [Chapter 01 — Your First Agent](../01-first-agent/)
- [Repository root](../../) for the full quick-start
- [Main README](../../README.md#learning-path--maf-v1-python-and-net) for the series index
