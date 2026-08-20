---
title: "MAF v1 — <Chapter Title>"
date: 2026-04-20
lastmod: 2026-04-20
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, <concept-tag>]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "<one-line summary>"
cover:
  image: "img/posts/maf-v1-<slug>.jpg"
  alt: "<alt text>"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — This article is part of *MAF v1: Python and .NET*. If you read the original Python-only version, it lives at [Part N — &lt;title&gt;](<old-url>).

## Why this chapter

<One paragraph: the concrete problem this chapter solves and why the reader cares. Ground it in an e-commerce scenario where possible so the capstone stays in mind.>

## Prerequisites

- Completed [Chapter 00 — Setup](../00-setup/)
- Familiar with [Chapter N-1 — &lt;title&gt;](../NN-previous/)
- Environment variables set: `OPENAI_API_KEY` (or `AZURE_OPENAI_*`) and `LLM_MODEL`

## The concept

<Plain-language explanation in 2–3 paragraphs. One diagram if it helps. Avoid framework jargon until after the concept is clear.>

## Python

Run from the repo root using the shared `tutorials/` uv project (one `uv sync` covers every chapter):

```bash
uv sync --project tutorials
uv run --project tutorials python tutorials/<chapter>/python/main.py
```

<Walk through the key parts of `main.py`. Full file is linked; show only the instructive 10–20 lines inline.>

## .NET

```bash
cd tutorials/<chapter>/dotnet
dotnet run
```

<Same structure — walk through `Program.cs` or the relevant class. Highlight differences from Python where they matter.>

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Key API | `ChatAgent` / `@tool` | `ChatClientAgent` / `AIFunctionFactory.Create` |
| Async model | `async`/`await` with `asyncio` | `async`/`await` with `Task`/`ValueTask` |
| Type hints | `Annotated[...]` + `Field` | `[Description]` attribute |

## Gotchas

- <Common mistake 1 + how to spot it>
- <SDK version or Azure API version to avoid>
- <Platform quirk>

## Tests

Both languages ship with unit tests exercising:

1. **Happy path** — the canonical example produces the expected output with a fake chat client.
2. **Edge case** — <describe>
3. **Concept assertion** — <what specifically proves the MAF concept was exercised, e.g., "tool was invoked exactly once", "middleware observed the run">

```bash
uv run --project tutorials pytest tutorials/<chapter>/python/tests -v
cd tutorials/<chapter>/dotnet && dotnet test
```

## How this shows up in the capstone

<Pointer into `agents/` or `dotnet/src/` where this pattern is used in the real app, with a file path and line number.>

## What's next

- Next chapter: [Chapter N+1 — &lt;title&gt;](../NN-next/)
- [Full source on GitHub](https://github.com/nitin27may/e-commerce-agents/tree/main/tutorials/<chapter>)
