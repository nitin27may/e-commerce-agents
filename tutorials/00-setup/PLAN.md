# Chapter 00 — Setup your dev environment

## Goal

Unblock readers with repeatable env setup for both stacks.

## Article mapping

- **Supersedes / updates / references**: none (new chapter)
- **New slug**: `/posts/maf-v1-setup/`

## Teaching strategy

- [x] New mini-chapter — the existing repo README covers setup for the Python stack only; this chapter covers both Python and .NET.

## Deliverables

### `python/`
- `README.md` snippet: `uv sync`, Python 3.12+, running `tutorials/01-first-agent/python` as smoke test.

### `dotnet/`
- `README.md` snippet: `dotnet --list-sdks` (expect 9.x), building `tutorials/01-first-agent/dotnet` as smoke test.

### Scripts
- `scripts/verify-setup.sh` (new, committed under repo root) — checks uv, dotnet 9, docker, required env vars, prints green/red per item.

### Article
- `README.md`: walkthrough of OpenAI vs Azure OpenAI keys, filling `.env` from `.env.example`, running `./scripts/dev.sh` (Python stack) and planned `./scripts/dev-dotnet.sh` (.NET stack).

## Verification

- On a clean Mac / Linux box following the article, `./scripts/verify-setup.sh` reports all green in under 10 minutes.
- Both stacks' "first agent" examples run end-to-end with the populated `.env`.

## How this maps into the capstone

This chapter points at the existing repo-root `README.md` quick-start and notes the additional steps for .NET. No capstone code changes.

## Out of scope

- Cloud deployment (Azure Container Apps / AKS) — covered later.
- Secrets management (Key Vault, managed identity) — mentioned but not implemented here.
